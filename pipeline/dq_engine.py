"""Step 4 / 6 - Config-driven data quality engine.

How it works:
  1. Rules are read from config/dq_rules.yaml, plus any automatically suggested rules that a
     data steward has APPROVED in config/steward_decisions.yaml (human-in-the-loop).
  2. Each rule is compiled into a SQL query that returns the IDs of FAILING records.
  3. Failures are written to governance.dq_failures; per-rule results (records checked,
     failed, failure %, PASS/FAIL against the threshold) go to governance.dq_rule_results.
  4. Records failing a rule whose action is 'quarantine' are moved to silver.dq_quarantine
     with reason codes. Nothing is silently dropped: bronze = silver + quarantine.
  5. Records failing 'flag' rules continue to silver with their rule IDs in dq_flags.
  6. A scorecard summarises pass rates by table, by Critical Data Element and by dimension.
"""

from __future__ import annotations

import pandas as pd

from pipeline.common import RunContext, load_yaml, render_sql, resolve, sql_literal

DIMENSIONS = {"Completeness", "Validity", "Accuracy", "Uniqueness", "Consistency", "Timeliness"}
ROW_CHECKS = {"not_null", "regex", "allowed_values", "range", "expression", "unique", "group_consistency", "referential"}
TABLE_CHECKS = {"freshness", "daily_volume"}
REQUIRED = ("id", "name", "table", "dimension", "check", "threshold_pct", "severity", "action", "owner")
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------- rule loading
def load_rules(cfg: dict) -> list[dict]:
    """Authored rules + steward-approved suggestions, validated before anything runs."""
    rules = [dict(r, source="authored") for r in load_yaml(cfg["dq"]["rules_file"])["rules"]]
    decisions_path = resolve(cfg["dq"]["decisions_file"])
    if decisions_path.exists():
        decisions = (load_yaml(decisions_path) or {}).get("decisions") or []
        for d in decisions:
            if d.get("decision") == "approved":
                rule = dict(d["rule"])
                rule.update(id=f"DQ-{d['suggestion_id']}", source="steward-approved suggestion",
                            approved_by=d.get("decided_by"), approved_at=str(d.get("decided_at")))
                rules.append(rule)
    validate_rules(rules, cfg["dq"]["tables"])
    return rules


def validate_rules(rules: list[dict], tables: dict) -> None:
    """Reject malformed rules up front: a broken control must never run silently."""
    seen = set()
    for r in rules:
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            raise ValueError(f"DQ rule {r.get('id', '?')} is missing fields: {missing}")
        if r["id"] in seen:
            raise ValueError(f"Duplicate DQ rule id {r['id']}")
        seen.add(r["id"])
        if r["dimension"] not in DIMENSIONS:
            raise ValueError(f"{r['id']}: unknown dimension {r['dimension']}")
        if r["check"] not in ROW_CHECKS | TABLE_CHECKS:
            raise ValueError(f"{r['id']}: unknown check type {r['check']}")
        if r["table"] not in tables:
            raise ValueError(f"{r['id']}: unknown table {r['table']}")
        if r["action"] not in ("quarantine", "flag"):
            raise ValueError(f"{r['id']}: action must be quarantine or flag")
        if r["action"] == "quarantine" and tables[r["table"]]["layer"] != "bronze":
            raise ValueError(f"{r['id']}: only bronze rules can quarantine")


# ---------------------------------------------------------------------------- SQL compilation
def failing_records_sql(rule: dict, relation: str, key: str, params: dict | None = None) -> str:
    """Compile one row-level rule into SQL returning the keys of failing records (as record_id)."""
    params = params or {}
    check, col, p = rule["check"], rule.get("column"), rule.get("params") or {}
    base = relation if not rule.get("where") else f"(SELECT * FROM {relation} WHERE {rule['where']})"

    if check == "not_null":
        cols = rule.get("columns") or [col]
        cond = " OR ".join(f"{c} IS NULL" for c in cols)
        return f"SELECT {key} AS record_id FROM {base} WHERE {cond}"
    if check == "regex":
        pattern = p["pattern"].replace("'", "''")
        return (f"SELECT {key} AS record_id FROM {base} WHERE {col} IS NOT NULL "
                f"AND NOT regexp_full_match(CAST({col} AS VARCHAR), '{pattern}')")
    if check == "allowed_values":
        values = ", ".join(sql_literal(str(v)) for v in p["values"])
        return (f"SELECT {key} AS record_id FROM {base} WHERE {col} IS NOT NULL "
                f"AND CAST({col} AS VARCHAR) NOT IN ({values})")
    if check == "range":
        conds = []
        if "min" in p:
            conds.append(f"{col} < {p['min']}")
        if "max" in p:
            conds.append(f"{col} > {p['max']}")
        return f"SELECT {key} AS record_id FROM {base} WHERE {col} IS NOT NULL AND ({' OR '.join(conds)})"
    if check == "expression":
        expr = render_sql(rule["fail_if"], params)
        return f"SELECT {key} AS record_id FROM {base} WHERE COALESCE(({expr}), FALSE)"
    if check == "unique":
        cols = rule.get("columns") or [col]
        not_null = " AND ".join(f"{c} IS NOT NULL" for c in cols)
        # Keep the first occurrence (lowest key); every later copy fails.
        return (f"SELECT record_id FROM (SELECT {key} AS record_id, row_number() OVER "
                f"(PARTITION BY {', '.join(cols)} ORDER BY {key}) AS rn FROM {base} WHERE {not_null}) WHERE rn > 1")
    if check == "group_consistency":
        grp = p["group_by"]
        # Records whose value differs from the most common value for the same group fail.
        return (f"SELECT t.{key} AS record_id FROM {base} t JOIN (SELECT {grp}, mode({col}) AS modal_value "
                f"FROM {base} WHERE {col} IS NOT NULL AND {grp} IS NOT NULL GROUP BY {grp}) g USING ({grp}) "
                f"WHERE t.{col} IS NOT NULL AND t.{col} <> g.modal_value")
    if check == "referential":
        return (f"SELECT t.{key} AS record_id FROM {base} t WHERE t.{col} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {p['ref_relation']} r WHERE r.{p['ref_column']} = t.{col})")
    raise ValueError(f"{rule['id']}: {check} is not a row-level check")


def evaluate_table_check(con, rule: dict, relation: str, params: dict) -> tuple[int, int, str]:
    """Table-level checks return (units checked, units failed, detail)."""
    p, col = rule.get("params") or {}, rule["column"]
    as_of = params["as_of"]
    if rule["check"] == "freshness":
        latest = con.execute(
            f"SELECT max({col}) FROM {relation} WHERE {col} <= TIMESTAMP '{as_of}'").fetchall()[0][0]
        if latest is None:
            return 1, 1, "No records found"
        lag_h = con.execute(f"SELECT date_diff('minute', TIMESTAMP '{latest}', TIMESTAMP '{as_of}') / 60.0").fetchall()[0][0]
        failed = int(lag_h > p["max_lag_hours"])
        return 1, failed, f"Latest record {latest}, {lag_h:.1f} h before as-of {as_of} (limit {p['max_lag_hours']} h)"
    if rule["check"] == "daily_volume":
        rows = con.execute(
            f"""SELECT d.day, count(t.{col}) AS n
                FROM (SELECT unnest(generate_series(DATE '{params['period_start']}', CAST(TIMESTAMP '{as_of}' AS DATE),
                      INTERVAL 1 DAY)) AS day) d
                LEFT JOIN {relation} t ON CAST(t.{col} AS DATE) = CAST(d.day AS DATE)
                GROUP BY d.day""").fetchall()
        low = [(str(day)[:10], n) for day, n in rows if n < p["min_rows"]]
        detail = f"{len(low)} of {len(rows)} days below {p['min_rows']} records"
        if low:
            detail += ": " + ", ".join(f"{d} ({n})" for d, n in low[:5])
        return len(rows), len(low), detail
    raise ValueError(f"{rule['id']}: {rule['check']} is not a table-level check")


# ---------------------------------------------------------------------------- execution
def run_rules(con, rules: list[dict], tables: dict, run_id: str, phase: str, params: dict) -> pd.DataFrame:
    """Evaluate every rule for one layer (phase = 'bronze' or 'gold') and persist results."""
    con.execute("""CREATE TABLE IF NOT EXISTS governance.dq_failures (
                       run_id VARCHAR, rule_id VARCHAR, table_name VARCHAR, record_id VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS governance.dq_rule_results (
                       run_id VARCHAR, rule_id VARCHAR, rule_name VARCHAR, table_name VARCHAR, column_name VARCHAR,
                       dimension VARCHAR, check_type VARCHAR, severity VARCHAR, is_cde BOOLEAN, action VARCHAR,
                       owner VARCHAR, source VARCHAR, threshold_pct DOUBLE, records_checked BIGINT,
                       records_failed BIGINT, failure_pct DOUBLE, pass_rate_pct DOUBLE, status VARCHAR,
                       synthetic_failed BIGINT, detail VARCHAR)""")
    phase_rules = [r for r in rules if tables[r["table"]]["layer"] == phase]
    table_names = sorted({r["table"] for r in phase_rules})
    for t in table_names:
        con.execute("DELETE FROM governance.dq_failures WHERE run_id = ? AND table_name = ?", [run_id, t])
    con.execute(f"DELETE FROM governance.dq_rule_results WHERE run_id = ? AND rule_id IN "
                f"({', '.join(sql_literal(r['id']) for r in phase_rules) or 'NULL'})", [run_id])

    results = []
    for rule in phase_rules:
        t = tables[rule["table"]]
        relation, key = t["relation"], t["key"]
        synthetic_failed, detail = None, ""
        if rule["check"] in TABLE_CHECKS:
            checked, failed, detail = evaluate_table_check(con, rule, relation, params)
        else:
            sql = failing_records_sql(rule, relation, key, params)
            con.execute(
                f"INSERT INTO governance.dq_failures SELECT ?, ?, ?, CAST(record_id AS VARCHAR) FROM ({sql})",
                [run_id, rule["id"], rule["table"]])
            failed = con.execute("SELECT count(*) FROM governance.dq_failures WHERE run_id = ? AND rule_id = ?",
                                 [run_id, rule["id"]]).fetchall()[0][0]
            where = f" WHERE {rule['where']}" if rule.get("where") else ""
            checked = con.execute(f"SELECT count(*) FROM {relation}{where}").fetchall()[0][0]
            if t["layer"] == "bronze" and failed:
                synthetic_failed = con.execute(
                    f"""SELECT count(*) FROM governance.dq_failures f JOIN {relation} b
                        ON CAST(f.record_id AS BIGINT) = b.{key}
                        WHERE f.run_id = ? AND f.rule_id = ? AND b.is_synthetic""", [run_id, rule["id"]]).fetchall()[0][0]
            elif t["layer"] == "bronze":
                synthetic_failed = 0
        failure_pct = round(100.0 * failed / checked, 4) if checked else 0.0
        results.append({
            "run_id": run_id, "rule_id": rule["id"], "rule_name": rule["name"], "table_name": rule["table"],
            "column_name": ",".join(rule.get("columns") or [rule.get("column") or ""]),
            "dimension": rule["dimension"], "check_type": rule["check"], "severity": rule["severity"],
            "is_cde": bool(rule.get("is_cde", False)), "action": rule["action"], "owner": rule["owner"],
            "source": rule.get("source", "authored"), "threshold_pct": float(rule["threshold_pct"]),
            "records_checked": int(checked), "records_failed": int(failed), "failure_pct": failure_pct,
            "pass_rate_pct": round(100.0 - failure_pct, 4),
            "status": "PASS" if failure_pct <= float(rule["threshold_pct"]) else "FAIL",
            "synthetic_failed": synthetic_failed, "detail": detail,
        })
    df = pd.DataFrame(results)
    if not df.empty:
        con.register("dq_results_df", df)
        con.execute("INSERT INTO governance.dq_rule_results BY NAME SELECT * FROM dq_results_df")
        con.unregister("dq_results_df")
    return df


def rule_meta_frame(rules: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "rule_id": r["id"], "table_name": r["table"], "action": r["action"],
        "reason_code": r.get("reason_code", r["id"]), "flag_in_silver": bool(r.get("flag_in_silver", True)),
        # sort key so the primary reason is the most severe failed rule (ties broken by rule id)
        "severity_key": f"{5 - SEVERITY_ORDER.get(r['severity'], 0)}-{r['id']}",
    } for r in rules])


def build_silver(con, rules: list[dict], run_id: str, quarantined_at: str) -> None:
    """Quarantine blocked records (with reasons), and build silver from everything else."""
    con.register("rule_meta", rule_meta_frame(rules))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE silver.dq_quarantine AS
        WITH q AS (
            SELECT CAST(f.record_id AS BIGINT) AS bronze_row_id,
                   list_sort(list_distinct(list(f.rule_id)))     AS failed_rules,
                   list_sort(list_distinct(list(m.reason_code))) AS reason_codes,
                   min_by(m.reason_code, m.severity_key)         AS primary_reason
            FROM governance.dq_failures f JOIN rule_meta m USING (rule_id)
            WHERE f.run_id = '{run_id}' AND m.action = 'quarantine' AND m.table_name = 'transactions'
            GROUP BY 1
        )
        SELECT q.bronze_row_id, q.failed_rules, q.reason_codes, q.primary_reason,
               b._source, b.is_synthetic, b.synthetic_tag,
               TRY_CAST(NULLIF(trim(b.amt), '') AS DECIMAL(18, 2)) AS amount,
               '{run_id}' AS run_id, TIMESTAMP '{quarantined_at}' AS quarantined_at,
               to_json(b) AS raw_record
        FROM q JOIN bronze.transactions_raw b USING (bronze_row_id)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE silver.transactions AS
        WITH flags AS (
            SELECT CAST(f.record_id AS BIGINT) AS bronze_row_id, list_sort(list_distinct(list(f.rule_id))) AS dq_flags
            FROM governance.dq_failures f JOIN rule_meta m USING (rule_id)
            WHERE f.run_id = '{run_id}' AND m.action = 'flag' AND m.flag_in_silver AND m.table_name = 'transactions'
            GROUP BY 1
        )
        SELECT
            t.bronze_row_id, t.trans_num, t.txn_ts, t.cc_num AS card_number,
            regexp_replace(t.merchant, '^fraud_', '') AS merchant_name,   -- strip simulator artefact
            t.category, t.amount, t.first_name, t.last_name, t.gender, t.street, t.city, t.state,
            lpad(t.zip, 5, '0') AS zip,                                    -- restore lost leading zeros
            t.home_lat, t.home_long, t.city_pop, t.job, t.dob, t.merch_lat, t.merch_long,
            t.is_fraud, t.is_synthetic, t.synthetic_tag, t._source,
            COALESCE(fl.dq_flags, CAST([] AS VARCHAR[])) AS dq_flags
        FROM bronze.transactions_typed t
        LEFT JOIN flags fl USING (bronze_row_id)
        WHERE NOT EXISTS (SELECT 1 FROM silver.dq_quarantine q WHERE q.bronze_row_id = t.bronze_row_id)
        """
    )
    con.unregister("rule_meta")


def scorecard(con, run_id: str) -> dict[str, pd.DataFrame]:
    """Pass rates by dimension, by table and by Critical Data Element (record-weighted)."""
    out = {}
    base = f"FROM governance.dq_rule_results WHERE run_id = '{run_id}'"
    agg = ("count(*) AS rules, count(*) FILTER (WHERE status = 'PASS') AS rules_passed, "
           "sum(records_checked) AS records_checked, sum(records_failed) AS records_failed, "
           "round(100.0 * (1 - sum(records_failed) / nullif(sum(records_checked), 0)), 4) AS pass_rate_pct")
    out["by_dimension"] = con.execute(f"SELECT dimension, {agg} {base} GROUP BY 1 ORDER BY 1").df()
    out["by_table"] = con.execute(f"SELECT table_name, {agg} {base} GROUP BY 1 ORDER BY 1").df()
    out["by_cde"] = con.execute(
        f"SELECT table_name, column_name AS cde, {agg} {base} AND is_cde GROUP BY 1, 2 ORDER BY 1, 2").df()
    out["monthly"] = con.execute(
        f"""
        WITH fails AS (
            SELECT strftime(b.txn_ts, '%Y-%m') AS month, r.dimension, count(*) AS failed
            FROM governance.dq_failures f
            JOIN governance.dq_rule_results r ON r.run_id = f.run_id AND r.rule_id = f.rule_id
            JOIN bronze.transactions_typed b ON CAST(f.record_id AS BIGINT) = b.bronze_row_id
            WHERE f.run_id = '{run_id}' AND f.table_name = 'transactions' AND b.txn_ts IS NOT NULL
              AND b.txn_ts BETWEEN TIMESTAMP '2019-01-01' AND TIMESTAMP '2020-12-31 23:59:59'
            GROUP BY 1, 2
        ), vol AS (
            SELECT strftime(txn_ts, '%Y-%m') AS month, count(*) AS records FROM bronze.transactions_typed
            WHERE txn_ts BETWEEN TIMESTAMP '2019-01-01' AND TIMESTAMP '2020-12-31 23:59:59' GROUP BY 1
        ), dims AS (
            SELECT dimension, count(*) AS n_rules FROM governance.dq_rule_results
            WHERE run_id = '{run_id}' AND table_name = 'transactions' AND check_type NOT IN ('freshness', 'daily_volume')
            GROUP BY 1
        )
        SELECT v.month, d.dimension, v.records * d.n_rules AS checks, COALESCE(f.failed, 0) AS failed,
               round(100.0 * (1 - COALESCE(f.failed, 0) / (v.records * d.n_rules)), 4) AS pass_rate_pct
        FROM vol v CROSS JOIN dims d LEFT JOIN fails f ON f.month = v.month AND f.dimension = d.dimension
        ORDER BY 1, 2
        """
    ).df()
    for name, df in out.items():
        con.register("sc_df", df.assign(run_id=run_id))
        con.execute(f"CREATE OR REPLACE TABLE governance.dq_scorecard_{name} AS SELECT * FROM sc_df")
        con.unregister("sc_df")
    return out


def engine_params(ctx: RunContext) -> dict:
    return {"as_of": ctx.cfg["project"]["batch_as_of"], "period_start": ctx.cfg["project"]["period_start"]}


def run_bronze(ctx: RunContext) -> None:
    con, cfg = ctx.con, ctx.cfg
    rules = load_rules(cfg)
    ctx.metrics["dq_rules"] = rules
    res = run_rules(con, rules, cfg["dq"]["tables"], ctx.run_id, "bronze", engine_params(ctx))
    build_silver(con, rules, ctx.run_id, ctx.started_at.strftime("%Y-%m-%d %H:%M:%S"))
    q = ctx.scalar("SELECT count(*) FROM silver.dq_quarantine")
    s = ctx.scalar("SELECT count(*) FROM silver.transactions")
    ctx.metrics.update(quarantined_rows=int(q), silver_rows=int(s))
    ctx.add_lineage("bronze.transactions_typed", "silver.dq_quarantine", "dq_engine",
                    "Records failing quarantine rules, with reason codes")
    ctx.add_lineage("bronze.transactions_typed", "silver.transactions", "dq_engine",
                    "Passing records; merchant prefix stripped, ZIP left-padded, dq_flags added")
    ctx.add_lineage("config/dq_rules.yaml", "governance.dq_rule_results", "dq_engine", "Rule definitions")
    ctx.log.info("DQ bronze: %d rules, %d passed, %d failed; quarantined %s, silver %s",
                 len(res), (res.status == "PASS").sum(), (res.status == "FAIL").sum(), f"{q:,}", f"{s:,}")


def run_gold(ctx: RunContext) -> None:
    con, cfg = ctx.con, ctx.cfg
    rules = ctx.metrics.get("dq_rules") or load_rules(cfg)
    run_rules(con, rules, cfg["dq"]["tables"], ctx.run_id, "gold", engine_params(ctx))
    allres = con.execute("SELECT * FROM governance.dq_rule_results WHERE run_id = ?", [ctx.run_id]).df()
    sc = scorecard(con, ctx.run_id)
    ctx.metrics.update(
        dq_rules_evaluated=len(allres), dq_rules_passed=int((allres.status == "PASS").sum()),
        dq_rules_failed=int((allres.status == "FAIL").sum()), dq_scorecard=sc,
    )
    ctx.add_lineage("gold.fact_transactions", "governance.dq_rule_results", "dq_engine", "Gold-layer detective rules")
    ctx.log.info("DQ gold: total %d rules evaluated, %d passed, %d failed", len(allres),
                 ctx.metrics["dq_rules_passed"], ctx.metrics["dq_rules_failed"])
