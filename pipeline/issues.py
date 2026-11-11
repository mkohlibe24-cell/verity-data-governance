"""Steps 13 and 15 - Issue log, created automatically from control failures.

Sources: failed DQ rules, reconciliation breaks, data contract breaches and Red KRIs.
Each issue gets a severity, an owner (the control owner), a root cause, a remediation plan,
a due date (from the severity SLA) and a status.

The log persists across runs (governance.issue_log is never dropped):
  * a failure already logged and still open is updated, not duplicated;
  * an open issue whose control now passes is closed as "Closed - auto-verified".
Root causes for the real source-data findings are 5-Whys analyses in config/issue_rca.yaml.
For generator-injected defects the log states that plainly.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from pipeline.common import RunContext, load_yaml, resolve

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def ensure_table(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS governance.issue_log (
               issue_id VARCHAR PRIMARY KEY, source_type VARCHAR, source_ref VARCHAR, title VARCHAR,
               description VARCHAR, severity VARCHAR, owner VARCHAR, root_cause VARCHAR, five_whys VARCHAR,
               remediation_plan VARCHAR, records_affected BIGINT, synthetic_share_pct DOUBLE,
               opened_at DATE, due_date DATE, status VARCHAR, remediation_progress_pct INTEGER,
               first_seen_run VARCHAR, last_seen_run VARCHAR, closed_at DATE)"""
    )


def load_rca(cfg: dict) -> dict:
    path = resolve("config/issue_rca.yaml")
    return (load_yaml(path) or {}).get("root_causes", {}) if path.exists() else {}


def dq_findings(ctx: RunContext) -> list[dict]:
    rules = {r["id"]: r for r in (ctx.metrics.get("dq_rules") or [])}
    rca = load_rca(ctx.cfg)
    fails = ctx.con.execute(
        "SELECT * FROM governance.dq_rule_results WHERE run_id = ? AND status = 'FAIL'", [ctx.run_id]).df()
    out = []
    for r in fails.itertuples():
        rule = rules.get(r.rule_id, {})
        synthetic = None if pd.isna(r.synthetic_failed) else float(r.synthetic_failed)
        share = round(100.0 * synthetic / r.records_failed, 2) if synthetic is not None and r.records_failed else None
        curated = rca.get(r.rule_id)
        if curated:
            root, whys = curated["root_cause"], " | ".join(curated["five_whys"])
        elif share is not None and share >= 99.99:
            root = ("All failing records are generator-injected test defects (synthetic_tag 'dq:*'), "
                    "so this confirms the control detects this failure mode. In production the likely cause is: "
                    + rule.get("root_cause_hint", "to be investigated by the owner."))
            whys = ""
        else:
            root, whys = "Hypothesis to confirm with the owner: " + rule.get("root_cause_hint", "unknown"), ""
        out.append({
            "source_type": "DQ rule", "source_ref": f"DQ:{r.rule_id}",
            "title": f"{r.rule_id} failed: {r.rule_name}",
            "description": (f"{r.records_failed:,} of {r.records_checked:,} records failed ({r.failure_pct}%), "
                            f"tolerance {r.threshold_pct}%. Dimension: {r.dimension}. Action: {r.action}."),
            "severity": r.severity, "owner": r.owner, "root_cause": root, "five_whys": whys,
            "remediation_plan": rule.get("remediation", "Owner to define."),
            "records_affected": int(r.records_failed), "synthetic_share_pct": share,
        })
    return out


def recon_findings(ctx: RunContext) -> list[dict]:
    breaks = ctx.con.execute(
        "SELECT * FROM governance.reconciliation_results WHERE run_id = ? AND status = 'BREAK'", [ctx.run_id]).df()
    return [{
        "source_type": "Reconciliation", "source_ref": f"REC:{b.control_id}",
        "title": f"{b.control_id} break: {b.control_name}",
        "description": f"Expected {b.expected:,.2f}, actual {b.actual:,.2f}, difference {b.difference:,.2f} "
                       f"(tolerance {b.tolerance}).",
        "severity": "high", "owner": "Data Engineering Lead",
        "root_cause": "Hypothesis to confirm: records lost or duplicated between layers.", "five_whys": "",
        "remediation_plan": "Trace the difference record by record using bronze_row_id; fix the transformation and re-run.",
        "records_affected": int(abs(b.difference)) if b.metric == "count" else None, "synthetic_share_pct": None,
    } for b in breaks.itertuples()]


def contract_findings(ctx: RunContext) -> list[dict]:
    fails = ctx.con.execute(
        "SELECT * FROM governance.contract_results WHERE run_id = ? AND status = 'FAIL'", [ctx.run_id]).df()
    out = []
    for contract, grp in fails.groupby("contract"):
        out.append({
            "source_type": "Data contract", "source_ref": f"CONTRACT:{contract}",
            "title": f"Data contract breached: {contract}",
            "description": "; ".join(f"{c.clause}: {c.detail}" for c in grp.itertuples()),
            "severity": "high", "owner": grp["owner"].iloc[0],
            "root_cause": "Hypothesis to confirm: an upstream change was not agreed with consumers.", "five_whys": "",
            "remediation_plan": "Notify consumers, fix the producing step or agree a new contract version.",
            "records_affected": None, "synthetic_share_pct": None,
        })
    return out


def kri_findings(ctx: RunContext) -> list[dict]:
    reds = ctx.con.execute(
        "SELECT * FROM governance.kri_results WHERE run_id = ? AND rag = 'Red'", [ctx.run_id]).df()
    rca = load_rca(ctx.cfg)
    default = {"root_cause": "Hypothesis to confirm: see the contributing rules/controls for this KRI.", "five_whys": []}
    return [{
        "source_type": "KRI breach", "source_ref": f"KRI:{k.kri_id}",
        "title": f"{k.kri_id} red: {k.name} = {k.value} {k.unit}",
        "description": f"{k.name} is {k.value} {k.unit} for {k.period} (red threshold {k.red_threshold}). {k.formula}.",
        "severity": "high", "owner": k.owner,
        "root_cause": rca.get(k.kri_id, default)["root_cause"],
        "five_whys": " | ".join(rca.get(k.kri_id, default)["five_whys"]),
        "remediation_plan": k.escalation, "records_affected": None, "synthetic_share_pct": None,
    } for k in reds.itertuples()]


def upsert(ctx: RunContext, findings: list[dict], source_types: list[str]) -> None:
    """Insert new issues, refresh open ones, auto-close resolved ones (for these source types)."""
    con, run_id = ctx.con, ctx.run_id
    ensure_table(con)
    today = dt.date.today()
    sla = ctx.cfg["dq"]["severity_sla_days"]
    current_refs = {f["source_ref"] for f in findings}
    existing = con.execute("SELECT issue_id, source_ref, status FROM governance.issue_log").df()
    open_refs = dict(zip(existing.loc[existing.status == "Open", "source_ref"],
                         existing.loc[existing.status == "Open", "issue_id"], strict=True))
    next_n = 1 + max([int(i.split("-")[1]) for i in existing["issue_id"]] or [0])
    for f in sorted(findings, key=lambda x: (SEVERITY_RANK.get(x["severity"], 9), x["source_ref"])):
        if f["source_ref"] in open_refs:
            # Refresh the facts and analysis, but keep the issue ID, opened date and due date
            con.execute("UPDATE governance.issue_log SET last_seen_run = ?, records_affected = ?, description = ?, "
                        "title = ?, root_cause = ?, five_whys = ?, remediation_plan = ?, synthetic_share_pct = ? "
                        "WHERE issue_id = ?",
                        [run_id, f["records_affected"], f["description"], f["title"], f["root_cause"], f["five_whys"],
                         f["remediation_plan"], f["synthetic_share_pct"], open_refs[f["source_ref"]]])
            continue
        issue_id = f"ISS-{next_n:04d}"
        next_n += 1
        con.execute(
            "INSERT INTO governance.issue_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [issue_id, f["source_type"], f["source_ref"], f["title"], f["description"], f["severity"], f["owner"],
             f["root_cause"], f["five_whys"], f["remediation_plan"], f["records_affected"], f["synthetic_share_pct"],
             today, today + dt.timedelta(days=int(sla.get(f["severity"], 30))), "Open", 0, run_id, run_id, None])
    for ref, issue_id in open_refs.items():
        if ref not in current_refs and any(ref.startswith(p) for p in source_types):
            con.execute("UPDATE governance.issue_log SET status = 'Closed - auto-verified', closed_at = ?, "
                        "remediation_progress_pct = 100 WHERE issue_id = ?", [today, issue_id])


def run(ctx: RunContext) -> None:
    findings = dq_findings(ctx) + recon_findings(ctx) + contract_findings(ctx)
    upsert(ctx, findings, ["DQ:", "REC:", "CONTRACT:"])
    ctx.add_lineage("governance.dq_rule_results", "governance.issue_log", "issues", "Failed rules become issues")
    ctx.add_lineage("governance.reconciliation_results", "governance.issue_log", "issues", "Breaks become issues")
    ctx.log.info("Issue log: %d findings from DQ, reconciliation and contracts", len(findings))


def run_kri(ctx: RunContext) -> None:
    findings = kri_findings(ctx)
    upsert(ctx, findings, ["KRI:"])
    n_open = ctx.scalar("SELECT count(*) FROM governance.issue_log WHERE status = 'Open'")
    ctx.metrics["open_issues"] = int(n_open)
    ctx.add_lineage("governance.kri_results", "governance.issue_log", "issues", "Red KRIs become issues")
    ctx.log.info("Issue log: %d KRI breaches; %d issues open in total", len(findings), n_open)
