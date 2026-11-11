"""Run the whole Verity pipeline with one command.

    python pipeline/run_pipeline.py            # full Kaggle data (data/raw/kaggle_fraud.zip)
    python pipeline/run_pipeline.py --sample   # committed CC0 sample (fresh clone / CI)

Every step is timed and logged to logs/<run_id>.log, and the run is recorded in the
governance.pipeline_runs audit table (run_id, start/end, row counts per layer, rules
passed/failed). If a step fails, the run is still recorded with status FAILED.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path

# Allow "python pipeline/run_pipeline.py" from the repo root (adds the repo to the import path)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import (  # noqa: E402
    catalog,
    dq_engine,
    gold,
    governance_outputs,
    ingest,
    issues,
    kri,
    ml,
    monitoring,
    profiling,
    reconciliation,
    rule_suggester,
)
from pipeline.common import RunContext, connect, load_config, setup_logging  # noqa: E402


def record_step(ctx: RunContext, step: str, started: float, status: str, error: str | None = None) -> None:
    ctx.con.execute(
        "INSERT INTO governance.pipeline_steps VALUES (?, ?, ?, ?, ?, ?)",
        [ctx.run_id, step, dt.datetime.fromtimestamp(started), dt.datetime.now(),
         round(time.time() - started, 2), status if error is None else f"{status}: {error[:200]}"],
    )


def init_audit_tables(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS governance.pipeline_runs (
               run_id VARCHAR PRIMARY KEY, mode VARCHAR, started_at TIMESTAMP, ended_at TIMESTAMP,
               duration_seconds DOUBLE, status VARCHAR, bronze_rows BIGINT, quarantined_rows BIGINT,
               silver_rows BIGINT, gold_fact_rows BIGINT, dq_rules_evaluated INTEGER, dq_rules_passed INTEGER,
               dq_rules_failed INTEGER, recon_breaks INTEGER, alerts INTEGER, open_issues INTEGER, error VARCHAR)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS governance.pipeline_steps (
               run_id VARCHAR, step VARCHAR, started_at TIMESTAMP, ended_at TIMESTAMP,
               duration_seconds DOUBLE, status VARCHAR)"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Verity data governance pipeline")
    parser.add_argument("--sample", action="store_true", help="use the committed sample instead of the Kaggle zip")
    parser.add_argument("--db", default=None, help="override the DuckDB warehouse path")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    cfg = load_config()
    mode = "sample" if args.sample else "full"
    db_path = args.db or (cfg["paths"]["warehouse"].replace(".duckdb", "_sample.duckdb") if args.sample
                          else cfg["paths"]["warehouse"])
    run_id = f"RUN-{dt.datetime.now():%Y%m%d-%H%M%S}"
    log = setup_logging(run_id, cfg["paths"]["logs_dir"])
    con = connect(db_path)
    init_audit_tables(con)
    ctx = RunContext(run_id=run_id, cfg=cfg, con=con, log=log, mode=mode)
    log.info("=== Verity pipeline %s started (%s mode, warehouse %s) ===", run_id, mode, db_path)

    steps = [
        ("1. Ingest to bronze (+ tagged generator rows)", ingest.run),
        ("2. Profile data (discovery) + PII scan", profiling.run),
        ("3. Suggest DQ rules from profile", rule_suggester.run),
        ("4. DQ rules on bronze -> quarantine + silver", dq_engine.run_bronze),
        ("5. Build gold (PII masked)", gold.run),
        ("6. DQ rules on gold", dq_engine.run_gold),
        ("7. Reconciliation raw -> bronze -> silver -> gold", reconciliation.run),
        ("8. Monitoring rules (SQL)", monitoring.run_rules),
        ("9. ML risk scoring", ml.run),
        ("10. Alerts (hybrid rules + ML)", monitoring.build_alerts),
        ("11. Data contracts", catalog.run_contracts),
        ("12. Catalog, lineage, control inventory", catalog.run_catalog),
        ("13. Issue log (DQ, reconciliation, contracts)", issues.run),
        ("14. KRIs", kri.run),
        ("15. Issue log (KRI breaches)", issues.run_kri),
        ("16. Reports (Excel, markdown, results.json)", governance_outputs.run),
    ]
    status, error = "SUCCESS", None
    for name, func in steps:
        started = time.time()
        log.info("--- %s", name)
        try:
            func(ctx)
            record_step(ctx, name, started, "OK")
            log.info("    done in %.1fs", time.time() - started)
        except Exception as exc:  # record the failure in the audit log, then stop
            status, error = "FAILED", f"{name}: {exc}"
            record_step(ctx, name, started, "FAILED", str(exc))
            log.error("Step failed: %s\n%s", name, traceback.format_exc())
            break

    m = ctx.metrics
    ended = dt.datetime.now()
    con.execute("DELETE FROM governance.pipeline_runs WHERE run_id = ?", [run_id])
    con.execute(
        "INSERT INTO governance.pipeline_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, mode, ctx.started_at, ended, round((ended - ctx.started_at).total_seconds(), 1), status,
         m.get("bronze_rows"), m.get("quarantined_rows"), m.get("silver_rows"), m.get("gold_fact_rows"),
         m.get("dq_rules_evaluated"), m.get("dq_rules_passed"), m.get("dq_rules_failed"),
         m.get("recon_breaks"), m.get("alerts"), m.get("open_issues"), error],
    )
    if status == "SUCCESS":
        governance_outputs.write_run_log(ctx)
    log.info("=== Run %s finished: %s in %.0fs ===", run_id, status, (ended - ctx.started_at).total_seconds())
    if status != "SUCCESS":
        log.info("Metrics so far: %s", json.dumps({k: v for k, v in m.items() if not isinstance(v, list | dict)}))
    con.close()
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
