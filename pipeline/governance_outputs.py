"""Step 16 - Reporting outputs.

  reports/Verity_DQ_Governance_Report.xlsx   management workbook (scorecard, issues, catalog,
                                             control inventory, KRIs, reconciliation, ...)
  reports/*.md                               readable reports for GitHub
  docs/data_catalog.md, lineage.md, control_inventory.md, data_contracts.md
  reports/results.json                       every headline number, from this run only
  reports/pipeline_run_log.csv               the run audit log

Every number in the README and resume bullets is copied from results.json, never typed by hand.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from pipeline.catalog import lineage_mermaid, save
from pipeline.common import RunContext, resolve

NAVY = "1F2A44"
STATUS_FILL = {
    "PASS": "D9EAD3", "Green": "D9EAD3", "Operating": "D9EAD3", "Closed - auto-verified": "D9EAD3",
    "Amber": "FCE8B2", "FAIL": "F4C7C3", "BREAK": "F4C7C3", "Red": "F4C7C3", "OVERDUE": "F4C7C3",
}


# ---------------------------------------------------------------------------- helpers
def md_table(df: pd.DataFrame, cols: list[str] | None = None, max_rows: int | None = None) -> str:
    if df is None or df.empty:
        return "_No rows._"
    d = df[cols] if cols else df
    if max_rows:
        d = d.head(max_rows)

    def cell(v):
        if not isinstance(v, list | tuple | dict | np.ndarray):
            try:
                if pd.isna(v):
                    return ""
            except (TypeError, ValueError):
                pass
        if isinstance(v, pd.Timestamp | dt.date):
            ts = pd.Timestamp(v)
            return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else str(v)
        if isinstance(v, int) and not isinstance(v, bool):
            return f"{v:,}"
        if isinstance(v, list | tuple):
            v = ", ".join(map(str, v))
        return str(v).replace("|", "/").replace("\n", " ") if v is not None else ""

    lines = ["| " + " | ".join(d.columns) + " |", "|" + "---|" * len(d.columns)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in d.itertuples(index=False)]
    return "\n".join(lines)


def xl_value(v):
    """Convert pandas/numpy values (NA, numpy scalars, dicts) into something Excel accepts."""
    if isinstance(v, list | tuple | dict):
        return str(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def write_sheet(wb: Workbook, title: str, df: pd.DataFrame, status_cols: tuple[str, ...] = ()) -> None:
    ws = wb.create_sheet(title[:31])
    if df.empty:
        ws.append(["No rows"])
        return
    df = df.copy()
    for c in df.columns:
        df[c] = df[c].apply(lambda v: ", ".join(map(str, v)) if isinstance(v, list | tuple) else v)
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([xl_value(v) for v in row])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    status_idx = [i + 1 for i, c in enumerate(df.columns) if c in status_cols]
    for row in ws.iter_rows(min_row=2):
        for idx in status_idx:
            cell = row[idx - 1]
            fill = STATUS_FILL.get(str(cell.value))
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)
    for i, c in enumerate(df.columns, start=1):
        width = max(len(str(c)), *(len(str(v)) for v in df[c].head(200))) if len(df) else len(str(c))
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


# ---------------------------------------------------------------------------- results.json
def collect_results(ctx: RunContext) -> dict:
    con, run_id, m = ctx.con, ctx.run_id, ctx.metrics
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()[0][0]  # noqa: E731
    dq = con.execute("SELECT * FROM governance.dq_rule_results WHERE run_id = ?", [run_id]).df()
    defects_total = q("SELECT count(*) FROM bronze.transactions_raw WHERE synthetic_tag LIKE 'dq:%'")
    defects_caught = q(
        """SELECT count(DISTINCT b.bronze_row_id) FROM bronze.transactions_raw b
           JOIN governance.dq_failures f ON f.table_name = 'transactions' AND f.run_id = ?
                AND CAST(f.record_id AS BIGINT) = b.bronze_row_id
           WHERE b.synthetic_tag LIKE 'dq:%'""", [run_id])
    by_defect = con.execute(
        """SELECT b.synthetic_tag, count(*) AS injected,
                  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM governance.dq_failures f WHERE f.run_id = ?
                      AND f.table_name = 'transactions' AND CAST(f.record_id AS BIGINT) = b.bronze_row_id)) AS caught
           FROM bronze.transactions_raw b WHERE b.synthetic_tag LIKE 'dq:%' GROUP BY 1 ORDER BY 1""", [run_id]).df()
    real = dq[(dq["table_name"] == "transactions") & dq["synthetic_failed"].notna()].copy()
    real["source_failed"] = real["records_failed"] - real["synthetic_failed"]
    real = real[real["source_failed"] > 0][["rule_id", "rule_name", "source_failed", "status"]]
    cde_pass = q(f"""SELECT 100.0 * (1 - sum(records_failed) / sum(records_checked)) FROM governance.dq_rule_results
                     WHERE run_id = '{run_id}' AND is_cde AND check_type NOT IN ('freshness', 'daily_volume')""")
    issues = con.execute("SELECT severity, source_type, status FROM governance.issue_log").df()
    open_issues = issues[issues.status == "Open"]
    quarantine = con.execute(
        "SELECT primary_reason, count(*) AS n, count(*) FILTER (WHERE is_synthetic) AS synthetic "
        "FROM silver.dq_quarantine GROUP BY 1 ORDER BY n DESC, 1").df()
    return {
        "run_id": run_id, "mode": ctx.mode, "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "batch_as_of": ctx.cfg["project"]["batch_as_of"], "report_month": ctx.cfg["project"]["report_month"],
        "data": {
            "source_rows": m.get("kaggle_rows"), "synthetic_rows": m.get("synthetic_rows"),
            "synthetic_scenario_rows": m.get("synthetic_scenario_rows"),
            "synthetic_defect_rows": m.get("synthetic_defect_rows"), "bronze_rows": m.get("bronze_rows"),
            "quarantined_rows": m.get("quarantined_rows"),
            "quarantine_pct": round(100 * m["quarantined_rows"] / m["bronze_rows"], 4),
            "silver_rows": m.get("silver_rows"), "gold_fact_rows": m.get("gold_fact_rows"),
            "gold_customers": m.get("gold_customers"), "gold_merchants": m.get("gold_merchants"),
        },
        "profiling": {"columns": m.get("profiled_columns"), "pii_columns": m.get("pii_columns"),
                      "findings": m.get("discovery_findings")},
        "dq": {
            "rules_total": len(dq), "rules_bronze": int((dq.table_name == "transactions").sum()),
            "rules_gold": int((dq.table_name != "transactions").sum()),
            "rules_passed": int((dq.status == "PASS").sum()), "rules_failed": int((dq.status == "FAIL").sum()),
            "rules_by_dimension": dq.groupby("dimension").size().to_dict(),
            "quarantine_rules": int((dq.action == "quarantine").sum()), "flag_rules": int((dq.action == "flag").sum()),
            "cde_pass_rate_pct": round(cde_pass, 4),
            "injected_defects": int(defects_total), "injected_defects_caught": int(defects_caught),
            "injected_defect_detection_pct": round(100 * defects_caught / defects_total, 2) if defects_total else None,
            "detection_by_defect_type": by_defect.to_dict("records"),
            "source_data_failures_by_rule": real.to_dict("records"),
            "quarantine_by_reason": quarantine.to_dict("records"),
            "failed_rules": dq[dq.status == "FAIL"][["rule_id", "rule_name", "records_failed", "failure_pct",
                                                      "threshold_pct", "severity"]].to_dict("records"),
        },
        "rule_suggestions": {"proposed": m.get("rule_suggestions"), "pending": m.get("rule_suggestions_pending")},
        "reconciliation": {"controls": m.get("recon_controls"), "breaks": m.get("recon_breaks")},
        "contracts": {"products": m.get("contracts"), "clauses": m.get("contract_clauses"),
                      "clauses_passed": m.get("contract_clauses_passed")},
        "catalog": {"columns": m.get("catalog_columns"), "curated_pct": m.get("catalog_curated_pct"),
                    "cdes": m.get("catalog_cdes")},
        "controls": {"total": m.get("controls"), "by_type": m.get("controls_by_type")},
        "issues": {"total": len(issues), "open": len(open_issues),
                   "open_by_severity": open_issues.groupby("severity").size().to_dict(),
                   "open_by_source": open_issues.groupby("source_type").size().to_dict()},
        "monitoring": {
            "rules": len(ctx.cfg["monitoring"]["rules"]), "rule_hits": m.get("rule_hits"), "alerts": m.get("alerts"),
            "alerts_pct_of_gold": round(100 * m["alerts"] / m["gold_fact_rows"], 3),
            "alerts_by_priority": m.get("alerts_by_priority"),
            "rule_performance": con.execute("SELECT * FROM monitoring.rule_performance").df().to_dict("records"),
        },
        "ml": m.get("ml"),
        "kris": m.get("kris"),
        "lineage_edges": m.get("lineage_edges"),
    }


# ---------------------------------------------------------------------------- markdown + docs
def write_markdown(ctx: RunContext, res: dict) -> None:
    con, run_id = ctx.con, ctx.run_id
    reports, docs = resolve(ctx.cfg["paths"]["reports_dir"]), resolve(ctx.cfg["paths"]["docs_dir"])
    docs.mkdir(parents=True, exist_ok=True)
    stamp = f"_Generated by the pipeline, run `{run_id}` ({ctx.mode} mode). Do not edit by hand._"
    sc = ctx.metrics["dq_scorecard"]
    dq = con.execute("SELECT rule_id, rule_name, dimension, severity, is_cde, action, threshold_pct, records_checked, "
                     "records_failed, failure_pct, synthetic_failed, status FROM governance.dq_rule_results "
                     "WHERE run_id = ? ORDER BY rule_id", [run_id]).df()
    quarantine = pd.DataFrame(res["dq"]["quarantine_by_reason"])
    (reports / "dq_scorecard.md").write_text("\n\n".join([
        "# Data Quality Scorecard", stamp,
        f"CDE pass rate: **{res['dq']['cde_pass_rate_pct']}%** | Rules: {res['dq']['rules_total']} "
        f"({res['dq']['rules_passed']} passed, {res['dq']['rules_failed']} failed) | Quarantined: "
        f"{res['data']['quarantined_rows']:,} records ({res['data']['quarantine_pct']}%) | Injected defects detected: "
        f"{res['dq']['injected_defects_caught']:,} of {res['dq']['injected_defects']:,} "
        f"({res['dq']['injected_defect_detection_pct']}%)",
        "## By dimension", md_table(sc["by_dimension"]), "## By table", md_table(sc["by_table"]),
        "## By Critical Data Element", md_table(sc["by_cde"]),
        "## Rule results", md_table(dq),
        "## Quarantine by primary reason", md_table(quarantine),
        "## Injected defects: detection by type", md_table(pd.DataFrame(res["dq"]["detection_by_defect_type"])),
    ]) + "\n", encoding="utf-8")

    issues = con.execute("SELECT issue_id, severity, title, owner, records_affected, synthetic_share_pct, opened_at, "
                         "due_date, status FROM governance.issue_log ORDER BY issue_id").df()
    rca = con.execute("SELECT issue_id, title, root_cause, five_whys, remediation_plan FROM governance.issue_log "
                      "WHERE five_whys <> '' ORDER BY issue_id").df()
    rca_text = "\n\n".join(
        f"### {r.issue_id}: {r.title}\n\n**Root cause:** {r.root_cause}\n\n" +
        "\n".join(f"{i}. {w.strip()}" for i, w in enumerate(r.five_whys.split("|"), start=1)) +
        f"\n\n**Remediation:** {r.remediation_plan}" for r in rca.itertuples())
    (reports / "issue_log.md").write_text("\n\n".join([
        "# Issue Log", stamp, md_table(issues), "## Root-cause analyses (5 Whys) for source-data issues", rca_text,
    ]) + "\n", encoding="utf-8")

    mlm = res["ml"]
    comp = pd.DataFrame(mlm["comparison_test"]).T.reset_index(names="approach")
    overlap = pd.DataFrame(mlm["overlap_test"]).T.reset_index(names="segment")
    perf = pd.DataFrame(res["monitoring"]["rule_performance"])
    (reports / "monitoring_results.md").write_text("\n\n".join([
        "# Transaction Monitoring Results", stamp,
        f"Test period Jul-Dec 2020: {mlm['split']['test']['rows']:,} transactions, {mlm['split']['test']['fraud']:,} fraud. "
        f"LightGBM threshold {mlm['threshold']['chosen_by_cost_on_validation']} (chosen on June 2020 by cost; "
        f"false alarm = ${mlm['threshold']['cost_false_positive_usd']:.0f}, missed fraud = its amount). {mlm['accuracy_note']}",
        "## Rules vs ML vs hybrid (test period)",
        md_table(comp, ["approach", "alerts", "tp", "fp", "fn", "precision", "recall", "f1", "pr_auc",
                        "fraud_amount_caught", "fraud_amount_missed"] if "pr_auc" in comp else None),
        "## What each approach catches that the other misses",
        md_table(overlap),
        "## Rule performance", md_table(perf),
        "## Top ML risk drivers (mean absolute TreeSHAP contribution, test period)",
        md_table(pd.DataFrame(mlm["top_drivers"])),
    ]) + "\n", encoding="utf-8")

    kris = con.execute("SELECT kri_id, name, category, period, value, unit, amber_threshold, red_threshold, rag, owner "
                       "FROM governance.kri_results WHERE run_id = ? ORDER BY kri_id", [run_id]).df()
    kdefs = con.execute("SELECT kri_id, formula, rationale, escalation FROM governance.kri_results WHERE run_id = ? "
                        "ORDER BY kri_id", [run_id]).df()
    (reports / "kri_report.md").write_text("\n\n".join([
        "# Key Risk Indicators", stamp, md_table(kris), "## Definitions", md_table(kdefs)]) + "\n", encoding="utf-8")

    cat = con.execute("SELECT table_name, column_name, data_type, sensitivity, is_cde, description, business_owner "
                      "FROM governance.data_catalog ORDER BY table_name, column_name").df()
    cdes = con.execute("SELECT table_name, column_name, cde_reason FROM governance.data_catalog WHERE is_cde "
                       "ORDER BY table_name, column_name").df()
    (docs / "data_catalog.md").write_text("\n\n".join([
        "# Data Catalog", stamp,
        f"{res['catalog']['columns']} columns across {cat['table_name'].nunique()} tables; "
        f"{res['catalog']['curated_pct']}% have a curated business definition; {res['catalog']['cdes']} are flagged as CDEs.",
        "## Critical Data Elements", md_table(cdes), "## All columns", md_table(cat),
    ]) + "\n", encoding="utf-8")

    edges = pd.DataFrame(ctx.lineage).drop_duplicates()
    pii_lineage = pd.DataFrame([
        ("bronze.cc_num", "silver.card_number", "gold.dim_customer.card_token", "SHA-256(salt + PAN); masked copy keeps last 4"),
        ("bronze.first / last", "silver.first_name / last_name", "gold.dim_customer.*_name_masked", "First letter kept, rest masked"),
        ("bronze.dob", "silver.dob", "gold.dim_customer.birth_year, age_band", "Generalised to year and band"),
        ("bronze.street", "silver.street", "(removed)", "Not needed by any consumer (data minimisation)"),
        ("bronze.zip", "silver.zip (left-padded)", "gold.dim_customer.zip3", "Truncated to 3 digits"),
        ("bronze.lat / long", "silver.home_lat / home_long", "gold.dim_customer.home_*_2dp; fact.distance_from_home_km",
         "Rounded to 2 dp; distance computed before rounding"),
        ("bronze.unix_time", "(dropped in silver)", "(not in gold)", "Unreliable field (issue DQ-K01)"),
    ], columns=["Bronze", "Silver", "Gold", "Transformation"])
    (docs / "lineage.md").write_text("\n\n".join([
        "# Data Lineage", stamp,
        "Generated from the edges each pipeline step recorded while it ran (not drawn by hand).",
        "```mermaid\n" + lineage_mermaid(edges) + "\n```",
        "## Column-level lineage for personal data", md_table(pii_lineage),
        "## Edges", md_table(edges),
    ]) + "\n", encoding="utf-8")

    ctrl = con.execute("SELECT * FROM governance.control_inventory ORDER BY control_id").df()
    (docs / "control_inventory.md").write_text("\n\n".join([
        "# Data Control Inventory", stamp,
        "Every DQ rule, reconciliation, contract check and monitoring rule, listed as a control. "
        f"Total {len(ctrl)}: " + ", ".join(f"{k} {v}" for k, v in res["controls"]["by_type"].items()) + ".",
        md_table(ctrl),
    ]) + "\n", encoding="utf-8")

    contracts = con.execute("SELECT contract, version, clause, status, detail FROM governance.contract_results "
                            "WHERE run_id = ? ORDER BY contract, clause", [run_id]).df()
    (docs / "data_contracts.md").write_text("\n\n".join([
        "# Data Contracts - latest results", stamp,
        "Contract definitions live in `config/contracts/`. Each gold data product is checked on every run.",
        md_table(contracts),
    ]) + "\n", encoding="utf-8")


def write_excel(ctx: RunContext, res: dict) -> None:
    con, run_id = ctx.con, ctx.run_id
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Verity – Data Quality & Governance Report"])
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    ws.append([f"Run {run_id} | mode {ctx.mode} | batch as-of {res['batch_as_of']} | report month {res['report_month']}"])
    ws.append([])
    summary = [
        ("Records ingested (bronze)", res["data"]["bronze_rows"]),
        ("  of which source (Kaggle)", res["data"]["source_rows"]),
        ("  of which generator-tagged", res["data"]["synthetic_rows"]),
        ("Records quarantined", res["data"]["quarantined_rows"]),
        ("Quarantine rate (%)", res["data"]["quarantine_pct"]),
        ("Records in gold", res["data"]["gold_fact_rows"]),
        ("DQ rules evaluated", res["dq"]["rules_total"]),
        ("DQ rules passed / failed", f"{res['dq']['rules_passed']} / {res['dq']['rules_failed']}"),
        ("CDE pass rate (%)", res["dq"]["cde_pass_rate_pct"]),
        ("Injected defects detected (%)", res["dq"]["injected_defect_detection_pct"]),
        ("Reconciliation breaks", res["reconciliation"]["breaks"]),
        ("Contract clauses passed", f"{res['contracts']['clauses_passed']} / {res['contracts']['clauses']}"),
        ("Open issues", res["issues"]["open"]),
        ("Controls in inventory", res["controls"]["total"]),
        ("Alerts generated", res["monitoring"]["alerts"]),
    ]
    ws.append(["Metric", "Value"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    for row in summary:
        ws.append(list(row))
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 28

    sc = ctx.metrics["dq_scorecard"]
    frames = [
        ("KRIs", con.execute("SELECT kri_id, name, category, period, value, unit, amber_threshold, red_threshold, rag, "
                             "owner, formula, escalation FROM governance.kri_results WHERE run_id = ? ORDER BY kri_id",
                             [run_id]).df(), ("rag",)),
        ("DQ Scorecard - Dimension", sc["by_dimension"], ()),
        ("DQ Scorecard - Table", sc["by_table"], ()),
        ("DQ Scorecard - CDE", sc["by_cde"], ()),
        ("DQ Scorecard - Monthly", sc["monthly"], ()),
        ("DQ Rule Results", con.execute("SELECT * EXCLUDE (run_id) FROM governance.dq_rule_results WHERE run_id = ? "
                                        "ORDER BY rule_id", [run_id]).df(), ("status",)),
        ("Quarantine Summary", pd.DataFrame(res["dq"]["quarantine_by_reason"]), ()),
        ("Reconciliation", con.execute("SELECT * EXCLUDE (run_id) FROM governance.reconciliation_results "
                                       "WHERE run_id = ?", [run_id]).df(), ("status",)),
        ("Issue Log", con.execute("SELECT *, CASE WHEN status = 'Open' AND due_date < current_date THEN 'OVERDUE' "
                                  "ELSE '' END AS overdue FROM governance.issue_log ORDER BY issue_id").df(),
         ("status", "overdue")),
        ("Data Catalog", con.execute("SELECT * FROM governance.data_catalog").df(), ()),
        ("Control Inventory", con.execute("SELECT * FROM governance.control_inventory").df(), ("last_result",)),
        ("Data Contracts", con.execute("SELECT * EXCLUDE (run_id) FROM governance.contract_results WHERE run_id = ?",
                                       [run_id]).df(), ("status",)),
        ("Rule Suggestions", con.execute("SELECT suggestion_id, \"column\", \"check\", params, dimension, evidence, "
                                         "confidence, would_fail_records, would_fail_pct, status "
                                         "FROM governance.rule_suggestions").df(), ()),
        ("Monitoring Rules", con.execute("SELECT * FROM monitoring.rule_catalogue").df(), ()),
        ("Rule Performance", con.execute("SELECT * FROM monitoring.rule_performance").df(), ()),
        ("Model Comparison", pd.DataFrame(res["ml"]["comparison_test"]).T.reset_index(names="approach"), ()),
        ("Lineage", con.execute("SELECT * FROM governance.lineage").df(), ()),
        ("Pipeline Runs", con.execute("SELECT * FROM governance.pipeline_runs ORDER BY started_at DESC").df(), ("status",)),
    ]
    for title, df, status in frames:
        write_sheet(wb, title, df, status)
    path = resolve(ctx.cfg["paths"]["reports_dir"]) / "Verity_DQ_Governance_Report.xlsx"
    wb.save(path)


def run(ctx: RunContext) -> None:
    # Re-save lineage now that every step (including issues/KRIs) has recorded its edges
    edges = pd.DataFrame(ctx.lineage).drop_duplicates()
    save(ctx.con, "lineage", edges.assign(run_id=ctx.run_id))
    ctx.metrics["lineage_edges"] = len(edges)
    res = collect_results(ctx)
    reports = resolve(ctx.cfg["paths"]["reports_dir"])
    name = "results.json" if ctx.mode == "full" else "results_sample.json"
    (reports / name).write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if ctx.mode == "full":  # committed reports always come from the full run
        write_markdown(ctx, res)
        write_excel(ctx, res)
    ctx.log.info("Reports written to %s", reports)


def write_run_log(ctx: RunContext) -> None:
    runs = ctx.con.execute("SELECT * FROM governance.pipeline_runs ORDER BY started_at").df()
    if ctx.mode == "full":
        runs.to_csv(resolve(ctx.cfg["paths"]["reports_dir"]) / "pipeline_run_log.csv", index=False)
