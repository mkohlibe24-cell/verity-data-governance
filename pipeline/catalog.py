"""Steps 11 and 12 - Data contracts, data catalog, lineage and control inventory.

run_contracts: checks each gold data product against its contract (config/contracts/*.yaml):
               schema, required fields, unique keys, freshness, volume, forbidden PII columns
               and quality thresholds.
run_catalog:   builds the data catalog (auto-generated technical metadata + curated business
               metadata), the lineage graph (from what the pipeline actually did this run),
               and the control inventory (every DQ rule, reconciliation, contract check and
               monitoring rule listed as a control).
"""

from __future__ import annotations

import pandas as pd

from pipeline.common import RunContext, load_yaml, resolve

CATALOG_TABLES = [
    ("bronze", "transactions_raw"), ("silver", "transactions"), ("silver", "dq_quarantine"),
    ("gold", "fact_transactions"), ("gold", "dim_customer"), ("gold", "dim_merchant"), ("monitoring", "alerts"),
]
SENSITIVITY_BY_PII = {"PCI": "Restricted", "Direct": "Restricted", "Quasi": "Confidential"}


# ---------------------------------------------------------------------------- contracts
def load_contracts() -> list[dict]:
    return [load_yaml(p) for p in sorted(resolve("config/contracts").glob("*.yaml"))]


def check_contract(con, c: dict, run_id: str, as_of: str) -> list[dict]:
    rel = c["contract"]
    schema, table = rel.split(".")
    actual = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns "
                              "WHERE table_schema = ? AND table_name = ?", [schema, table]).fetchall())
    results = []

    def add(clause, ok, detail):
        results.append({"run_id": run_id, "contract": rel, "version": str(c["version"]), "owner": c["owner"],
                        "clause": clause, "status": "PASS" if ok else "FAIL", "detail": detail})

    cols = c["schema"]
    missing = [x["name"] for x in cols if x["name"] not in actual]
    add("Schema: required columns present", not missing, f"missing: {missing}" if missing else f"{len(cols)} columns present")
    wrong = [f"{x['name']} ({actual[x['name']]} != {x['type']})" for x in cols
             if x["name"] in actual and not actual[x["name"]].upper().startswith(x["type"].upper())]
    add("Schema: column types match", not wrong, "; ".join(wrong) if wrong else "all types match")
    required = [x["name"] for x in cols if x.get("required") and x["name"] in actual]
    if required:
        nulls = con.execute(f"SELECT {', '.join(f'count(*) FILTER (WHERE {r} IS NULL)' for r in required)} FROM {rel}").fetchall()[0]
        bad = [f"{r}: {n:,} nulls" for r, n in zip(required, nulls, strict=True) if n]
        add("Required fields populated", not bad, "; ".join(bad) if bad else f"{len(required)} required fields fully populated")
    for x in cols:
        if x.get("unique") and x["name"] in actual:
            dupes = con.execute(f"SELECT count(*) - count(DISTINCT {x['name']}) FROM {rel}").fetchall()[0][0]
            add(f"Unique key: {x['name']}", dupes == 0, f"{dupes:,} duplicate values")
    if c.get("freshness"):
        f = c["freshness"]
        lag = con.execute(f"SELECT date_diff('minute', max({f['column']}), TIMESTAMP '{as_of}') / 60.0 FROM {rel}").fetchall()[0][0]
        add(f"Freshness: latest {f['column']} within {f['max_lag_hours']} h of as-of",
            lag is not None and lag <= f["max_lag_hours"], f"lag {lag:.1f} h" if lag is not None else "no data")
    n = con.execute(f"SELECT count(*) FROM {rel}").fetchall()[0][0]
    add(f"Volume: at least {c['min_rows']:,} rows", n >= c["min_rows"], f"{n:,} rows")
    leaked = [col for col in c.get("forbidden_columns", []) if col in actual]
    add("Privacy: no raw PII columns", not leaked, f"forbidden columns present: {leaked}" if leaked else "none present")
    for qc in c.get("quality", []):
        if qc["metric"] == "gold_rules_pass":
            fails = con.execute("SELECT count(*) FROM governance.dq_rule_results WHERE run_id = ? AND table_name = ? "
                                "AND status = 'FAIL'", [run_id, qc["table"]]).fetchall()[0][0]
            add(f"Quality: all gold DQ rules on {qc['table']} pass", fails == 0, f"{fails} failing rule(s)")
        elif qc["metric"] == "cde_pass_rate_pct":
            rate = con.execute("SELECT 100.0 * (1 - sum(records_failed) / sum(records_checked)) FROM governance.dq_rule_results "
                               "WHERE run_id = ? AND is_cde AND table_name = 'transactions' "
                               "AND check_type NOT IN ('freshness', 'daily_volume')", [run_id]).fetchall()[0][0]
            add(f"Quality: source CDE pass rate >= {qc['min']}%", rate >= qc["min"], f"{rate:.4f}%")
    return results


def run_contracts(ctx: RunContext) -> None:
    con = ctx.con
    rows = []
    for c in load_contracts():
        rows += check_contract(con, c, ctx.run_id, ctx.cfg["project"]["batch_as_of"])
    df = pd.DataFrame(rows)
    con.execute("""CREATE TABLE IF NOT EXISTS governance.contract_results (
                       run_id VARCHAR, contract VARCHAR, version VARCHAR, owner VARCHAR, clause VARCHAR,
                       status VARCHAR, detail VARCHAR)""")
    con.execute("DELETE FROM governance.contract_results WHERE run_id = ?", [ctx.run_id])
    con.register("con_df", df)
    con.execute("INSERT INTO governance.contract_results BY NAME SELECT * FROM con_df")
    con.unregister("con_df")
    passed = int((df.status == "PASS").sum())
    ctx.metrics.update(contract_clauses=len(df), contract_clauses_passed=passed, contracts=df["contract"].nunique())
    ctx.add_lineage("config/contracts/*.yaml", "governance.contract_results", "contracts", "Contract checks on gold")
    ctx.log.info("Contracts: %d products, %d/%d clauses passed", df["contract"].nunique(), passed, len(df))


# ---------------------------------------------------------------------------- catalog
def build_catalog(con, run_id: str) -> pd.DataFrame:
    curated = load_yaml("config/catalog_curated.yaml")
    tables_meta, columns_meta = curated["tables"], curated["columns"]
    pii = dict(con.execute("SELECT \"column\", suspected_pii FROM governance.profile_columns").fetchall())
    rule_counts = dict(con.execute(
        "SELECT table_name || '.' || column_name, count(*) FROM governance.dq_rule_results WHERE run_id = ? "
        "GROUP BY 1", [run_id]).fetchall())
    rows = []
    for schema, table in CATALOG_TABLES:
        rel = f"{schema}.{table}"
        tmeta = tables_meta.get(rel, {})
        cols = con.execute("SELECT column_name, data_type, ordinal_position FROM information_schema.columns "
                           "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", [schema, table]).fetchall()
        for name, dtype, _ in cols:
            meta = columns_meta.get(f"{rel}.{name}") or columns_meta.get(f"*.{name}") or {}
            auto_pii = pii.get(name) or pii.get({"card_number": "cc_num"}.get(name, name))
            sensitivity = meta.get("sensitivity") or (
                SENSITIVITY_BY_PII.get(auto_pii.split(" ")[0], "Internal") if auto_pii else "Internal")
            dq_table = {"bronze.transactions_raw": "transactions", "gold.fact_transactions": "fact_transactions",
                        "gold.dim_customer": "dim_customer", "gold.dim_merchant": "dim_merchant"}.get(rel)
            rows.append({
                "layer": schema, "table_name": rel, "column_name": name, "data_type": dtype,
                "description": meta.get("description", "Not yet curated"),
                "business_owner": meta.get("owner", tmeta.get("owner", "Unassigned")),
                "data_steward": tmeta.get("steward", "Unassigned"),
                "source": meta.get("source", tmeta.get("source", "")),
                "sensitivity": sensitivity, "pii_type": meta.get("pii") or auto_pii or "",
                "is_cde": bool(meta.get("cde", False)), "cde_reason": meta.get("cde_reason", ""),
                "dq_rules": int(rule_counts.get(f"{dq_table}.{name}", 0)) if dq_table else 0,
                "curated": bool(meta.get("description")),
            })
    return pd.DataFrame(rows)


def lineage_mermaid(edges: pd.DataFrame) -> str:
    ids, lines = {}, ["flowchart LR"]

    def node(name: str) -> str:
        if name not in ids:
            ids[name] = f"n{len(ids)}"
            lines.append(f'    {ids[name]}["{name}"]')
        return ids[name]

    for e in edges.itertuples():
        a, b = node(e.source), node(e.target)
        lines.append(f"    {a} -->|{e.step}| {b}")
    return "\n".join(lines)


def control_inventory(ctx: RunContext) -> pd.DataFrame:
    con, cfg, run_id = ctx.con, ctx.cfg, ctx.run_id
    dq = con.execute("SELECT * FROM governance.dq_rule_results WHERE run_id = ?", [run_id]).df()
    rows = [{
        "control_id": r.rule_id, "control_name": r.rule_name, "category": f"Data quality - {r.dimension}",
        "risk_addressed": f"Inaccurate or incomplete {r.column_name} data reaching consumers",
        "control_type": "Preventive" if r.action == "quarantine" else "Detective", "automation": "Automated",
        "frequency": "Every pipeline run (daily)", "owner": r.owner,
        "evidence": "governance.dq_rule_results / governance.dq_failures", "last_result": r.status,
    } for r in dq.itertuples()]
    rec = con.execute("SELECT * FROM governance.reconciliation_results WHERE run_id = ?", [run_id]).df()
    rows += [{
        "control_id": r.control_id, "control_name": r.control_name, "category": "Reconciliation",
        "risk_addressed": "Records or value lost or duplicated between layers", "control_type": "Detective",
        "automation": "Automated", "frequency": "Every pipeline run (daily)", "owner": "Data Engineering Lead",
        "evidence": "governance.reconciliation_results", "last_result": r.status,
    } for r in rec.itertuples()]
    con_res = con.execute("SELECT contract, owner, bool_and(status = 'PASS') AS ok FROM governance.contract_results "
                          "WHERE run_id = ? GROUP BY ALL ORDER BY 1", [run_id]).fetchall()
    rows += [{
        "control_id": f"CON-{i:02d}", "control_name": f"Data contract check: {c}", "category": "Data contract",
        "risk_addressed": "Breaking changes or poor quality delivered to data consumers", "control_type": "Detective",
        "automation": "Automated", "frequency": "Every pipeline run (daily)", "owner": owner,
        "evidence": "governance.contract_results", "last_result": "PASS" if ok else "FAIL",
    } for i, (c, owner, ok) in enumerate(con_res, start=1)]
    for rid, r in cfg["monitoring"]["rules"].items():
        rows.append({
            "control_id": rid, "control_name": r["name"], "category": "Transaction monitoring",
            "risk_addressed": r["risk"], "control_type": "Detective", "automation": "Automated",
            "frequency": r["frequency"], "owner": r["owner"], "evidence": "monitoring.rule_hits / monitoring.alerts",
            "last_result": "Operating",
        })
    rows += [
        {"control_id": "GOV-01", "control_name": "PII masking and card-number hashing in gold", "category": "Data protection",
         "risk_addressed": "Exposure of card numbers or personal data to analysts", "control_type": "Preventive",
         "automation": "Automated", "frequency": "Every pipeline run (daily)", "owner": "Head of Data Governance",
         "evidence": "DQ-G05 result, contract privacy clauses", "last_result": "Operating"},
        {"control_id": "GOV-02", "control_name": "Steward approval of suggested DQ rules", "category": "Change management",
         "risk_addressed": "Unreviewed rules blocking good data or missing bad data", "control_type": "Preventive",
         "automation": "Manual", "frequency": "On each new suggestion", "owner": "Transaction Data Steward",
         "evidence": "config/steward_decisions.yaml (git history)", "last_result": "Operating"},
        {"control_id": "GOV-03", "control_name": "Quarantine of failing records with reason codes", "category": "Data quality",
         "risk_addressed": "Bad records silently dropped or silently passed downstream", "control_type": "Preventive",
         "automation": "Automated", "frequency": "Every pipeline run (daily)", "owner": "Transaction Data Steward",
         "evidence": "silver.dq_quarantine, REC-03/REC-04", "last_result": "Operating"},
        {"control_id": "GOV-04", "control_name": "Monthly KRI review against RAG thresholds", "category": "Risk monitoring",
         "risk_addressed": "Deteriorating data quality or monitoring effectiveness going unnoticed",
         "control_type": "Detective", "automation": "Manual", "frequency": "Monthly", "owner": "Head of Data Governance",
         "evidence": "governance.kri_results, Excel report", "last_result": "Operating"},
        {"control_id": "GOV-05", "control_name": "Issue management with owners, due dates and auto-verification",
         "category": "Issue management", "risk_addressed": "Known issues not remediated", "control_type": "Corrective",
         "automation": "Manual", "frequency": "Weekly review", "owner": "Head of Data Governance",
         "evidence": "governance.issue_log", "last_result": "Operating"},
        {"control_id": "GOV-06", "control_name": "Pipeline run audit log", "category": "Operational",
         "risk_addressed": "No evidence of what ran, when, and with what outcome", "control_type": "Detective",
         "automation": "Automated", "frequency": "Every pipeline run", "owner": "Data Engineering Lead",
         "evidence": "governance.pipeline_runs, logs/<run_id>.log", "last_result": "Operating"},
    ]
    return pd.DataFrame(rows)


def save(con, name: str, df: pd.DataFrame) -> None:
    con.register("tmp_df", df)
    con.execute(f"CREATE OR REPLACE TABLE governance.{name} AS SELECT * FROM tmp_df")
    con.unregister("tmp_df")


def run_catalog(ctx: RunContext) -> None:
    con = ctx.con
    cat = build_catalog(con, ctx.run_id)
    save(con, "data_catalog", cat)
    ctx.add_lineage("information_schema + config/catalog_curated.yaml", "governance.data_catalog", "catalog",
                    "Technical + curated business metadata")
    controls = control_inventory(ctx)
    save(con, "control_inventory", controls)
    edges = pd.DataFrame(ctx.lineage).drop_duplicates()
    save(con, "lineage", edges.assign(run_id=ctx.run_id))
    ctx.metrics.update(
        catalog_columns=len(cat), catalog_curated_pct=round(100 * cat["curated"].mean(), 1),
        catalog_cdes=int(cat["is_cde"].sum()), controls=len(controls),
        controls_by_type=controls.groupby("control_type").size().to_dict(),
        lineage_edges=len(edges), lineage_mermaid=lineage_mermaid(edges),
    )
    ctx.log.info("Catalog: %d columns (%.1f%% curated, %d CDEs); %d controls; %d lineage edges",
                 len(cat), ctx.metrics["catalog_curated_pct"], ctx.metrics["catalog_cdes"], len(controls), len(edges))
