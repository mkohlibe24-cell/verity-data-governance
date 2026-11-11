"""Verity web API (FastAPI).

Reads the governed warehouse (DuckDB, opened read-only) and the pipeline's report outputs.
Writes only two things, both with an audit event:
- steward decisions on suggested DQ rules (config/steward_decisions.yaml, via pipeline.steward);
- alert case decisions with maker-checker (warehouse/casework.sqlite, via api.casework).

Identity is a demo header (X-Verity-User) mapped to made-up users on the server. Roles are
looked up here, never trusted from the browser. A real deployment would use SSO instead.

Run:  uvicorn api.main:app --port 8000      (from the repo root)
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import casework  # noqa: E402
from api.casework import User  # noqa: E402
from pipeline.common import load_config, resolve  # noqa: E402
from pipeline.rule_suggester import load_decisions  # noqa: E402
from pipeline.steward import record_decision  # noqa: E402

CFG = load_config()
DB_PATH = resolve(CFG["paths"]["warehouse"])
CASE_DB = ROOT / "warehouse" / "casework.sqlite"
REPORT_DIRS = {"reports": ROOT / "reports", "docs": ROOT / "docs"}

# Made-up demo users. Neha holds both roles to show the API blocks self-approval.
USERS: dict[str, User] = {u.id: u for u in (
    User("aarav", "Aarav Mehta", "Monitoring Analyst", ("analyst",)),
    User("riya", "Riya Sharma", "Monitoring Reviewer", ("reviewer",)),
    User("neha", "Neha Kapoor", "Monitoring Team Lead", ("analyst", "reviewer")),
    User("kabir", "Kabir Singh", "Data Steward", ("steward",)),
    User("ira", "Ira Banerjee", "Internal Audit (read only)", ("viewer",)),
)}

app = FastAPI(title="Verity API", version="0.2.0")
cases = casework.connect(CASE_DB)


# ---------------------------------------------------------------- helpers

def clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


@contextmanager
def warehouse():
    if not DB_PATH.exists():
        raise HTTPException(503, "Warehouse not found. Run: python pipeline/run_pipeline.py")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [{c: clean(v) for c, v in zip(cols, r, strict=True)} for r in cur.fetchall()]


def latest_run(con: duckdb.DuckDBPyConnection) -> str:
    r = con.execute("SELECT run_id FROM governance.pipeline_runs WHERE status = 'SUCCESS' AND mode = 'full'"
                    " ORDER BY started_at DESC LIMIT 1").fetchall()
    if not r:
        raise HTTPException(503, "No successful full pipeline run yet")
    return r[0][0]


def results() -> dict:
    path = ROOT / "reports" / "results.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def current_user(x_verity_user: str | None = Header(default=None)) -> User:
    user = USERS.get(x_verity_user or "")
    if user is None:
        raise HTTPException(401, "Unknown user. Send X-Verity-User with a demo user id.")
    return user


def as_dict(u: User) -> dict:
    return {"id": u.id, "name": u.name, "title": u.title, "roles": list(u.roles)}


# ---------------------------------------------------------------- identity / overview

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "warehouse": DB_PATH.exists()}


@app.get("/api/users")
def users() -> list[dict]:
    return [as_dict(u) for u in USERS.values()]


@app.get("/api/overview")
def overview(user: User = Depends(current_user)) -> dict:
    res = results()
    with warehouse() as con:
        run = latest_run(con)
        run_row = rows(con, "SELECT * FROM governance.pipeline_runs WHERE run_id = ?", [run])[0]
        kris = rows(con, "SELECT kri_id, name, category, period, value, unit, direction, amber_threshold,"
                         " red_threshold, rag, formula, rationale, escalation, owner"
                         " FROM governance.kri_results WHERE run_id = ? ORDER BY kri_id", [run])
        trend = rows(con, "SELECT kri_id, period, value, rag FROM governance.kri_trend WHERE run_id = ?"
                          " ORDER BY kri_id, period", [run])
        alerts_monthly = rows(con, "SELECT txn_month AS month, priority, count(*) AS n FROM monitoring.alerts"
                                   " GROUP BY ALL ORDER BY 1, 2")
        dq_monthly = rows(con, "SELECT month, sum(checks) AS checks, sum(failed) AS failed,"
                               " round(100 - 100.0 * sum(failed) / nullif(sum(checks), 0), 4) AS pass_rate_pct"
                               " FROM governance.dq_scorecard_monthly WHERE run_id = ? GROUP BY 1 ORDER BY 1", [run])
    return {
        "run": run_row, "batch_as_of": res.get("batch_as_of"), "report_month": res.get("report_month"),
        "data": res.get("data"), "dq": {k: v for k, v in (res.get("dq") or {}).items()
                                        if not isinstance(v, list)},
        "reconciliation": res.get("reconciliation"), "contracts": res.get("contracts"),
        "catalog": res.get("catalog"), "issues": res.get("issues"),
        "rule_suggestions": res.get("rule_suggestions"),
        "monitoring": {k: v for k, v in (res.get("monitoring") or {}).items() if not isinstance(v, list)},
        "ml": {"comparison_test": (res.get("ml") or {}).get("comparison_test"),
               "threshold": (res.get("ml") or {}).get("threshold")},
        "kris": kris, "kri_trend": trend, "alerts_monthly": alerts_monthly, "dq_monthly": dq_monthly,
    }


# ---------------------------------------------------------------- data governance

@app.get("/api/governance/scorecard")
def scorecard(user: User = Depends(current_user)) -> dict:
    with warehouse() as con:
        run = latest_run(con)
        q = "SELECT * EXCLUDE (run_id) FROM governance.{} WHERE run_id = ?"
        return {
            "run_id": run,
            "by_dimension": rows(con, q.format("dq_scorecard_by_dimension") + " ORDER BY dimension", [run]),
            "by_table": rows(con, q.format("dq_scorecard_by_table") + " ORDER BY table_name", [run]),
            "by_cde": rows(con, q.format("dq_scorecard_by_cde") + " ORDER BY pass_rate_pct, cde", [run]),
            "rules": rows(con, q.format("dq_rule_results") + " ORDER BY status, failure_pct DESC, rule_id", [run]),
            "findings": rows(con, "SELECT finding, value, detail FROM governance.discovery_findings"
                                  " WHERE run_id = ?", [run]),
        }


@app.get("/api/governance/catalog")
def catalog(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        return rows(con, "SELECT * FROM governance.data_catalog ORDER BY layer, table_name, column_name")


@app.get("/api/governance/lineage")
def lineage(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        run = latest_run(con)
        return rows(con, "SELECT source, target, step, transformation FROM governance.lineage WHERE run_id = ?",
                    [run])


@app.get("/api/governance/reconciliation")
def reconciliation(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        run = latest_run(con)
        return rows(con, "SELECT * EXCLUDE (run_id) FROM governance.reconciliation_results WHERE run_id = ?"
                         " ORDER BY control_id", [run])


@app.get("/api/governance/contracts")
def contracts(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        run = latest_run(con)
        return rows(con, "SELECT * EXCLUDE (run_id) FROM governance.contract_results WHERE run_id = ?"
                         " ORDER BY contract, clause", [run])


@app.get("/api/governance/controls")
def controls(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        return rows(con, "SELECT * FROM governance.control_inventory ORDER BY control_id")


def suggestion_rows() -> list[dict]:
    with warehouse() as con:
        run = latest_run(con)
        out = rows(con, "SELECT * EXCLUDE (run_id) FROM governance.rule_suggestions WHERE run_id = ?"
                        " ORDER BY would_fail_pct DESC, suggestion_id", [run])
    decisions = load_decisions(CFG)  # decisions made since the last run are shown straight away
    for s in out:
        for k in ("params", "proposed_rule"):
            if isinstance(s.get(k), str):
                s[k] = json.loads(s[k])
        d = decisions.get(s["suggestion_id"])
        if d:
            s.update(status=d["decision"], decided_by=d["decided_by"], decision_reason=d["reason"],
                     decided_at=d["decided_at"])
    return out


@app.get("/api/governance/suggestions")
def suggestions(user: User = Depends(current_user)) -> list[dict]:
    return suggestion_rows()


class Decision(BaseModel):
    decision: str
    reason: str


@app.post("/api/governance/suggestions/{suggestion_id}/decision")
def decide_suggestion(suggestion_id: str, body: Decision, user: User = Depends(current_user)) -> dict:
    if "steward" not in user.roles:
        raise HTTPException(403, f"{user.name} is not a data steward; only stewards approve or reject rules")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be 'approved' or 'rejected'")
    try:
        entry = record_decision(CFG, suggestion_id, body.decision, user.name, body.reason)
    except ValueError as e:
        raise HTTPException(409 if "already" in str(e) else 400, str(e)) from e
    with cases:
        casework.log_event(cases, user, "rule_suggestion", suggestion_id,
                           "Approved rule" if body.decision == "approved" else "Rejected rule",
                           "pending", body.decision, {"reason": entry["reason"], "rule": entry["rule"]["name"]})
    return entry


# ---------------------------------------------------------------- issues

@app.get("/api/issues")
def issues(user: User = Depends(current_user)) -> list[dict]:
    with warehouse() as con:
        out = rows(con, "SELECT * FROM governance.issue_log ORDER BY status DESC,"
                        " CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,"
                        " issue_id")
    for r in out:
        if isinstance(r.get("five_whys"), str):
            try:
                r["five_whys"] = json.loads(r["five_whys"])
            except json.JSONDecodeError:
                pass
    return out


# ---------------------------------------------------------------- monitoring

SORTABLE = {"combined_score", "txn_ts", "amount", "alert_id", "priority", "ml_score", "rule_score"}


@app.get("/api/alerts")
def alerts(user: User = Depends(current_user), priority: str | None = None, status: str | None = None,
           source: str | None = None, q: str | None = None, page: int = Query(0, ge=0),
           page_size: int = Query(25, ge=1, le=200), sort: str = "combined_score", desc: bool = True) -> dict:
    if sort not in SORTABLE:
        raise HTTPException(400, f"sort must be one of {sorted(SORTABLE)}")
    where, params = ["1=1"], []
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if source:
        where.append("detection_source = ?")
        params.append(source)
    if q:
        where.append("(alert_id ILIKE ? OR category ILIKE ? OR merchant_key ILIKE ? OR card_token ILIKE ?)")
        params += [f"%{q.strip()}%"] * 4
    by_status = casework.cases_by_status(cases)
    worked = [a for ids in by_status.values() for a in ids]
    if status == casework.NEW:
        if worked:
            where.append("NOT list_contains(?, alert_id)")
            params.append(worked)
    elif status:
        where.append("list_contains(?, alert_id)")
        params.append(by_status.get(status, []) or ["-"])
    w = " AND ".join(where)
    order = f"{sort} {'DESC' if desc else 'ASC'} NULLS LAST, alert_id"
    with warehouse() as con:
        total = con.execute(f"SELECT count(*) FROM monitoring.alerts WHERE {w}", params).fetchall()[0][0]
        items = rows(con, f"SELECT alert_id, txn_ts, amount, category, priority, combined_score, rule_score,"
                          f" ml_score, rules_fired, detection_source, is_synthetic, card_token"
                          f" FROM monitoring.alerts WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?",
                     params + [page_size, page * page_size])
        counts = rows(con, "SELECT priority, count(*) AS n FROM monitoring.alerts GROUP BY 1 ORDER BY 1")
    state = casework.all_cases(cases, [i["alert_id"] for i in items])
    for i in items:
        c = state.get(i["alert_id"])
        i["status"] = c["status"] if c else casework.NEW
        i["card_token"] = i["card_token"][:10]
    status_counts = {s: len(v) for s, v in by_status.items()}
    status_counts[casework.NEW] = sum(c["n"] for c in counts) - len(worked)
    return {"total": total, "page": page, "page_size": page_size, "items": items,
            "priority_counts": counts, "status_counts": status_counts}


@app.get("/api/alerts/{alert_id}")
def alert_detail(alert_id: str, user: User = Depends(current_user)) -> dict:
    with warehouse() as con:
        found = rows(con, "SELECT * FROM monitoring.alerts WHERE alert_id = ?", [alert_id])
        if not found:
            raise HTTPException(404, f"Alert {alert_id} not found")
        a = found[0]
        txn = rows(con, "SELECT f.trans_num, f.txn_ts, f.amount, f.category, f.merch_lat, f.merch_long,"
                        " f.distance_from_home_km, f.dq_flag_count, f.source_system, m.merchant_name,"
                        " c.card_number_masked, c.age_band, c.gender, c.city, c.state, c.zip3, c.city_pop,"
                        " c.txn_count AS card_txn_count"
                        " FROM gold.fact_transactions f JOIN gold.dim_merchant m USING (merchant_key)"
                        " JOIN gold.dim_customer c USING (card_token) WHERE f.trans_num = ?", [a["trans_num"]])
        recent = rows(con, "SELECT f.txn_ts, f.amount, f.category, m.merchant_name, f.distance_from_home_km,"
                           " f.trans_num = ? AS is_alerted"
                           " FROM gold.fact_transactions f JOIN gold.dim_merchant m USING (merchant_key)"
                           " WHERE f.card_token = ? AND f.txn_ts <= CAST(? AS TIMESTAMP)"
                           " ORDER BY f.txn_ts DESC, f.trans_num LIMIT 12",
                      [a["trans_num"], a["card_token"], a["txn_ts"]])
        hits = rows(con, "SELECT h.rule_id, r.name, h.reason, h.severity, h.base_score"
                         " FROM monitoring.rule_hits h JOIN monitoring.rule_catalogue r USING (rule_id)"
                         " WHERE h.trans_num = ? ORDER BY h.base_score DESC", [a["trans_num"]])
    a["card_token"] = a["card_token"][:16]
    if isinstance(a.get("ml_reasons"), str):
        try:
            a["ml_reasons"] = json.loads(a["ml_reasons"])
        except json.JSONDecodeError:
            pass
    return {"alert": a, "transaction": txn[0] if txn else None, "recent": recent, "rule_hits": hits,
            "case": casework.get_case(cases, alert_id),
            "audit": casework.audit_trail(cases, "alert", alert_id),
            "dispositions": list(casework.DISPOSITIONS)}


class Proposal(BaseModel):
    disposition: str
    note: str


class Check(BaseModel):
    note: str


def run_case(fn, *args) -> dict:
    try:
        return fn(cases, *args)
    except casework.CaseError as e:
        raise HTTPException(e.status_code, str(e)) from e


def ensure_alert(alert_id: str) -> None:
    with warehouse() as con:
        if not con.execute("SELECT 1 FROM monitoring.alerts WHERE alert_id = ?", [alert_id]).fetchall():
            raise HTTPException(404, f"Alert {alert_id} not found")


@app.post("/api/alerts/{alert_id}/propose")
def propose(alert_id: str, body: Proposal, user: User = Depends(current_user)) -> dict:
    ensure_alert(alert_id)
    return run_case(casework.propose, alert_id, user, body.disposition, body.note)


@app.post("/api/alerts/{alert_id}/approve")
def approve(alert_id: str, body: Check, user: User = Depends(current_user)) -> dict:
    ensure_alert(alert_id)
    return run_case(casework.approve, alert_id, user, body.note)


@app.post("/api/alerts/{alert_id}/return")
def return_alert(alert_id: str, body: Check, user: User = Depends(current_user)) -> dict:
    ensure_alert(alert_id)
    return run_case(casework.return_case, alert_id, user, body.note)


@app.get("/api/audit")
def audit(user: User = Depends(current_user), entity_type: str | None = None, limit: int = 200) -> list[dict]:
    return casework.audit_trail(cases, entity_type, None, min(limit, 1000))


@app.get("/api/monitoring/rules")
def monitoring_rules(user: User = Depends(current_user)) -> dict:
    res = results()
    with warehouse() as con:
        perf = rows(con, "SELECT p.*, c.risk_addressed, c.logic, c.threshold_rationale, c.severity, c.owner"
                         " FROM monitoring.rule_performance p JOIN monitoring.rule_catalogue c USING (rule_id)"
                         " ORDER BY rule_id")
        drivers = rows(con, "SELECT * FROM monitoring.ml_feature_importance ORDER BY mean_abs_shap DESC")
    ml = res.get("ml") or {}
    return {"rules": perf, "drivers": drivers, "comparison_test": ml.get("comparison_test"),
            "threshold": ml.get("threshold"), "overlap_test": ml.get("overlap_test"),
            "accuracy_note": ml.get("accuracy_note")}


# ---------------------------------------------------------------- reports

def report_files() -> list[dict]:
    out = []
    for group, folder in REPORT_DIRS.items():
        for p in sorted(folder.glob("*")):
            if p.is_file() and p.suffix in {".xlsx", ".md", ".csv", ".json"} and p.name != "results_sample.json":
                st = p.stat()
                out.append({"group": group, "name": p.name, "path": f"{group}/{p.name}", "bytes": st.st_size,
                            "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


@app.get("/api/reports")
def reports(user: User = Depends(current_user)) -> dict:
    with warehouse() as con:
        runs = rows(con, "SELECT * FROM governance.pipeline_runs ORDER BY started_at DESC LIMIT 20")
        run = latest_run(con)
        steps = rows(con, "SELECT step, started_at, duration_seconds, status FROM governance.pipeline_steps"
                          " WHERE run_id = ? ORDER BY started_at", [run])
        manifest = rows(con, "SELECT source, file, sha256, file_bytes, records, amount_total"
                             " FROM governance.source_manifest WHERE run_id = ?", [run])
    return {"files": report_files(), "runs": runs, "steps": steps, "manifest": manifest}


@app.get("/api/reports/file/{group}/{name}")
def report_file(group: str, name: str) -> FileResponse:
    allowed = {f["path"] for f in report_files()}
    path = f"{group}/{name}"
    if path not in allowed:  # whitelist, so no path traversal
        raise HTTPException(404, "Report not found")
    return FileResponse(REPORT_DIRS[group] / name, filename=name)


# ---------------------------------------------------------------- built web app (production)

DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        target = DIST / full_path
        return FileResponse(target if full_path and target.is_file() else DIST / "index.html")
