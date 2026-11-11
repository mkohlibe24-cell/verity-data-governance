"""Step 14 - Key Risk Indicators with Red / Amber / Green status.

A KRI measures RISK exposure (how likely or how bad something could go wrong), unlike a
KPI, which measures performance. Thresholds are defined in config.yaml and were set before
looking at results, so they cannot be tuned to make the dashboard look green.
"""

from __future__ import annotations

import pandas as pd

from pipeline.common import RunContext


def rag_status(value: float | None, direction: str, amber: float, red: float) -> str:
    """Return Red / Amber / Green. 'higher_is_better' KRIs go red when they FALL below red."""
    if value is None or pd.isna(value):
        return "Grey"
    if direction == "higher_is_better":
        if value < red:
            return "Red"
        if value < amber:
            return "Amber"
        return "Green"
    if value >= red:
        return "Red"
    if value >= amber:
        return "Amber"
    return "Green"


def monitoring_kris(con, month: str) -> dict[str, float | None]:
    """KRI-06/07/08 for one calendar month, from the hybrid alerts."""
    row = con.execute(
        """
        WITH f AS (SELECT * FROM gold.fact_transactions WHERE txn_month = ? AND is_fraud IS NOT NULL),
             a AS (SELECT trans_num FROM monitoring.alerts WHERE txn_month = ?)
        SELECT
            (SELECT count(*) FROM monitoring.alerts WHERE txn_month = ?)                                AS alerts,
            (SELECT count(*) FROM monitoring.alerts WHERE txn_month = ? AND is_fraud = 0)               AS false_alerts,
            (SELECT count(*) FROM f WHERE is_fraud = 1)                                                 AS fraud_txns,
            (SELECT count(*) FROM f JOIN a USING (trans_num) WHERE f.is_fraud = 1)                      AS fraud_alerted,
            (SELECT sum(amount) FROM f)                                                                 AS spend,
            (SELECT sum(amount) FROM f WHERE is_fraud = 1 AND trans_num NOT IN (SELECT trans_num FROM a)) AS missed_amount
        """, [month, month, month, month]).fetchall()[0]
    alerts, false_alerts, fraud_txns, fraud_alerted, spend, missed = row
    return {
        "KRI-06": round(100.0 * false_alerts / alerts, 2) if alerts else None,
        "KRI-07": round(100.0 * fraud_alerted / fraud_txns, 2) if fraud_txns else None,
        "KRI-08": round(10000.0 * float(missed or 0) / float(spend), 2) if spend else None,
    }


def open_critical_high_dq_issues(con) -> int:
    """KRI-02. KRI-breach issues are excluded: otherwise KRI-02 counts its own breach and drifts between runs."""
    return con.execute("SELECT count(*) FROM governance.issue_log WHERE status = 'Open'"
                       " AND severity IN ('critical', 'high') AND source_type <> 'KRI breach'").fetchall()[0][0]


def compute(ctx: RunContext) -> pd.DataFrame:
    con, cfg, run_id = ctx.con, ctx.cfg, ctx.run_id
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()[0][0]  # noqa: E731
    cde = q(f"""SELECT 100.0 * (1 - sum(records_failed) / sum(records_checked)) FROM governance.dq_rule_results
                WHERE run_id = '{run_id}' AND is_cde AND check_type NOT IN ('freshness', 'daily_volume')""")
    open_hi = open_critical_high_dq_issues(con)
    breaks = q(f"SELECT count(*) FROM governance.reconciliation_results WHERE run_id = '{run_id}' AND status = 'BREAK'")
    quarantine = q("SELECT 100.0 * (SELECT count(*) FROM silver.dq_quarantine) / (SELECT count(*) FROM bronze.transactions_raw)")
    contract = q(f"SELECT 100.0 * avg(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) FROM governance.contract_results "
                 f"WHERE run_id = '{run_id}'")
    month = cfg["project"]["report_month"]
    values = {"KRI-01": round(cde, 4), "KRI-02": open_hi, "KRI-03": breaks, "KRI-04": round(quarantine, 4),
              "KRI-05": round(contract, 2), **monitoring_kris(con, month)}
    rows = []
    for k in cfg["kris"]:
        v = values[k["id"]]
        period = month if k["category"] == "Monitoring" else f"Run {run_id}"
        rows.append({
            "run_id": run_id, "kri_id": k["id"], "name": k["name"], "category": k["category"], "period": period,
            "value": None if v is None else float(v), "unit": k["unit"], "direction": k["direction"],
            "amber_threshold": k["amber"], "red_threshold": k["red"],
            "rag": rag_status(v, k["direction"], k["amber"], k["red"]), "formula": k["formula"],
            "rationale": k["rationale"], "escalation": k["escalation"], "owner": k["owner"],
        })
    return pd.DataFrame(rows)


def trend(ctx: RunContext) -> pd.DataFrame:
    """Monthly history: monitoring KRIs over the test months, DQ KRIs by transaction month."""
    con, cfg, run_id = ctx.con, ctx.cfg, ctx.run_id
    defs = {k["id"]: k for k in cfg["kris"]}
    months = [r[0] for r in con.execute(
        "SELECT DISTINCT txn_month FROM gold.fact_transactions WHERE txn_ts >= ? ORDER BY 1",
        [cfg["ml"]["validation_end"]]).fetchall()]
    rows = []
    for m in months:
        for kid, v in monitoring_kris(con, m).items():
            rows.append({"kri_id": kid, "period": m, "value": v})
    cde_monthly = con.execute(
        f"""
        WITH cde_rules AS (SELECT rule_id FROM governance.dq_rule_results
                           WHERE run_id = '{run_id}' AND is_cde AND table_name = 'transactions'
                             AND check_type NOT IN ('freshness', 'daily_volume')),
             vol AS (SELECT strftime(txn_ts, '%Y-%m') AS m, count(*) AS n FROM bronze.transactions_typed
                     WHERE txn_ts BETWEEN TIMESTAMP '2019-01-01' AND TIMESTAMP '2020-12-31 23:59:59' GROUP BY 1),
             fails AS (SELECT strftime(b.txn_ts, '%Y-%m') AS m, count(*) AS failed
                       FROM governance.dq_failures f JOIN cde_rules USING (rule_id)
                       JOIN bronze.transactions_typed b ON CAST(f.record_id AS BIGINT) = b.bronze_row_id
                       WHERE f.run_id = '{run_id}' GROUP BY 1),
             quar AS (SELECT strftime(b.txn_ts, '%Y-%m') AS m, count(*) AS q FROM silver.dq_quarantine s
                      JOIN bronze.transactions_typed b USING (bronze_row_id) GROUP BY 1)
        SELECT vol.m,
               round(100.0 * (1 - COALESCE(fails.failed, 0) / (vol.n * (SELECT count(*) FROM cde_rules))), 4) AS cde,
               round(100.0 * COALESCE(quar.q, 0) / vol.n, 4) AS quarantine
        FROM vol LEFT JOIN fails USING (m) LEFT JOIN quar USING (m) ORDER BY 1
        """).fetchall()
    for m, cde, quar in cde_monthly:
        rows.append({"kri_id": "KRI-01", "period": m, "value": cde})
        rows.append({"kri_id": "KRI-04", "period": m, "value": quar})
    df = pd.DataFrame(rows)
    df["rag"] = [rag_status(v, defs[k]["direction"], defs[k]["amber"], defs[k]["red"])
                 for k, v in zip(df["kri_id"], df["value"], strict=True)]
    df["run_id"] = run_id
    return df


def run(ctx: RunContext) -> None:
    con = ctx.con
    df = compute(ctx)
    con.execute("""CREATE TABLE IF NOT EXISTS governance.kri_results (
                       run_id VARCHAR, kri_id VARCHAR, name VARCHAR, category VARCHAR, period VARCHAR, value DOUBLE,
                       unit VARCHAR, direction VARCHAR, amber_threshold DOUBLE, red_threshold DOUBLE, rag VARCHAR,
                       formula VARCHAR, rationale VARCHAR, escalation VARCHAR, owner VARCHAR)""")
    con.execute("DELETE FROM governance.kri_results WHERE run_id = ?", [ctx.run_id])
    con.register("kri_df", df)
    con.execute("INSERT INTO governance.kri_results BY NAME SELECT * FROM kri_df")
    con.unregister("kri_df")
    tr = trend(ctx)
    con.register("trend_df", tr)
    con.execute("CREATE OR REPLACE TABLE governance.kri_trend AS SELECT * FROM trend_df")
    con.unregister("trend_df")
    ctx.metrics["kris"] = df[["kri_id", "name", "value", "unit", "rag"]].to_dict("records")
    ctx.add_lineage("governance.dq_rule_results", "governance.kri_results", "kri", "DQ KRIs")
    ctx.add_lineage("monitoring.alerts", "governance.kri_results", "kri", "Monitoring KRIs (report month)")
    for r in df.itertuples():
        ctx.log.info("  %s %-34s %s %s -> %s", r.kri_id, r.name, r.value, r.unit, r.rag)
