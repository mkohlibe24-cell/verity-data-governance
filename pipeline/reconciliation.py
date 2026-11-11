"""Step 7 - Reconciliation controls: counts and amounts must tie out between layers.

  REC-01  source files -> bronze     record count   (source counted by pandas, not DuckDB)
  REC-02  source files -> bronze     amount total
  REC-03  bronze -> silver + quarantine   record count  (nothing silently dropped)
  REC-04  bronze -> silver + quarantine   amount total
  REC-05  silver -> gold fact        record count
  REC-06  silver -> gold fact        amount total
  REC-07  silver -> gold fact        fraud label count
  REC-08  silver cards -> gold customers  distinct keys (no hash collisions / lost cards)

Each result is PASS or BREAK against a tolerance from config. Breaks feed the issue log
and KRI-03. Results are kept per run (governance.reconciliation_results).
"""

from __future__ import annotations

import pandas as pd

from pipeline.common import RunContext

CONTROLS = [
    ("REC-01", "Source to bronze record count", "source", "bronze", "count"),
    ("REC-02", "Source to bronze amount total", "source", "bronze", "amount"),
    ("REC-03", "Bronze to silver + quarantine record count", "bronze", "silver+quarantine", "count"),
    ("REC-04", "Bronze to silver + quarantine amount total", "bronze", "silver+quarantine", "amount"),
    ("REC-05", "Silver to gold record count", "silver", "gold", "count"),
    ("REC-06", "Silver to gold amount total", "silver", "gold", "amount"),
    ("REC-07", "Silver to gold fraud label count", "silver", "gold", "count"),
    ("REC-08", "Silver cards to gold customer keys", "silver", "gold", "count"),
]


def measure(con, run_id: str) -> dict[str, tuple[float, float]]:
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()[0][0]  # noqa: E731
    bronze_amt = "SUM(TRY_CAST(NULLIF(trim(amt), '') AS DECIMAL(18, 2)))"
    return {
        "REC-01": (q("SELECT sum(records) FROM governance.source_manifest WHERE run_id = ?", [run_id]),
                   q("SELECT count(*) FROM bronze.transactions_raw")),
        "REC-02": (q("SELECT sum(amount_total) FROM governance.source_manifest WHERE run_id = ?", [run_id]),
                   q(f"SELECT {bronze_amt} FROM bronze.transactions_raw")),
        "REC-03": (q("SELECT count(*) FROM bronze.transactions_raw"),
                   q("SELECT (SELECT count(*) FROM silver.transactions) + (SELECT count(*) FROM silver.dq_quarantine)")),
        "REC-04": (q(f"SELECT {bronze_amt} FROM bronze.transactions_raw"),
                   q("SELECT COALESCE((SELECT sum(amount) FROM silver.transactions), 0) "
                     "+ COALESCE((SELECT sum(amount) FROM silver.dq_quarantine), 0)")),
        "REC-05": (q("SELECT count(*) FROM silver.transactions"), q("SELECT count(*) FROM gold.fact_transactions")),
        "REC-06": (q("SELECT sum(amount) FROM silver.transactions"), q("SELECT sum(amount) FROM gold.fact_transactions")),
        "REC-07": (q("SELECT count(*) FILTER (WHERE is_fraud = 1) FROM silver.transactions"),
                   q("SELECT count(*) FILTER (WHERE is_fraud = 1) FROM gold.fact_transactions")),
        "REC-08": (q("SELECT count(DISTINCT card_number) FROM silver.transactions"),
                   q("SELECT count(*) FROM gold.dim_customer")),
    }


def reconcile(con, run_id: str, cfg: dict) -> pd.DataFrame:
    values = measure(con, run_id)
    tol = cfg["reconciliation"]
    rows = []
    for cid, name, src, tgt, metric in CONTROLS:
        left, right = values[cid]
        left, right = float(left or 0), float(right or 0)
        diff = round(right - left, 2)
        tolerance = tol["amount_tolerance"] if metric == "amount" else tol["count_tolerance"]
        rows.append({
            "run_id": run_id, "control_id": cid, "control_name": name, "from_layer": src, "to_layer": tgt,
            "metric": metric, "expected": round(left, 2), "actual": round(right, 2), "difference": diff,
            "tolerance": tolerance, "status": "PASS" if abs(diff) <= tolerance else "BREAK",
        })
    df = pd.DataFrame(rows)
    con.execute("""CREATE TABLE IF NOT EXISTS governance.reconciliation_results (
                       run_id VARCHAR, control_id VARCHAR, control_name VARCHAR, from_layer VARCHAR,
                       to_layer VARCHAR, metric VARCHAR, expected DOUBLE, actual DOUBLE, difference DOUBLE,
                       tolerance DOUBLE, status VARCHAR)""")
    con.execute("DELETE FROM governance.reconciliation_results WHERE run_id = ?", [run_id])
    con.register("rec_df", df)
    con.execute("INSERT INTO governance.reconciliation_results BY NAME SELECT * FROM rec_df")
    con.unregister("rec_df")
    return df


def run(ctx: RunContext) -> None:
    df = reconcile(ctx.con, ctx.run_id, ctx.cfg)
    breaks = int((df["status"] == "BREAK").sum())
    ctx.metrics.update(recon_controls=len(df), recon_breaks=breaks)
    ctx.add_lineage("bronze/silver/gold", "governance.reconciliation_results", "reconciliation",
                    "Counts and amount totals tie-out between layers")
    for r in df.itertuples():
        level = ctx.log.warning if r.status == "BREAK" else ctx.log.info
        level("  %s %-45s expected %s actual %s -> %s", r.control_id, r.control_name,
              f"{r.expected:,.2f}", f"{r.actual:,.2f}", r.status)
