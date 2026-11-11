"""Alert case management with maker-checker (segregation of duties), stored in SQLite.

Workflow for one alert:

    New --(analyst proposes a disposition)--> Pending approval
    Pending approval --(reviewer approves)--> Closed
    Pending approval --(reviewer returns)---> Returned --(analyst proposes again)--> ...

Rules enforced here, not in the browser:
- only a user with the analyst role can propose (the maker);
- only a user with the reviewer role can approve or return (the checker);
- the checker must be a different person from the maker, even when one user holds both roles;
- every action writes an append-only audit event (who, role, when, from/to status, note).

The case state lives in its own SQLite file so the analytical warehouse (DuckDB) stays
read-only for the web app and the pipeline can rebuild it without losing case history.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DISPOSITIONS = ("False positive", "Confirmed fraud", "Escalated to investigation")
NEW, PENDING, CLOSED, RETURNED = "New", "Pending approval", "Closed", "Returned"
STATUSES = (NEW, PENDING, RETURNED, CLOSED)


class CaseError(Exception):
    """Base class; `status_code` is the HTTP status the API returns."""

    status_code = 400


class RoleError(CaseError):
    status_code = 403


class SegregationOfDutiesError(CaseError):
    status_code = 403


class StateError(CaseError):
    status_code = 409


@dataclass(frozen=True)
class User:
    id: str
    name: str
    title: str
    roles: tuple[str, ...]

    def primary_role(self) -> str:
        return self.roles[0]


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    alert_id     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    disposition  TEXT,
    maker_id     TEXT,
    maker_name   TEXT,
    maker_note   TEXT,
    proposed_at  TEXT,
    checker_id   TEXT,
    checker_name TEXT,
    checker_note TEXT,
    decided_at   TEXT,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    actor_name  TEXT NOT NULL,
    actor_roles TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_events(entity_type, entity_id);
"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log_event(conn: sqlite3.Connection, user: User, entity_type: str, entity_id: str, action: str,
              from_status: str | None = None, to_status: str | None = None, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_events (ts, actor_id, actor_name, actor_roles, entity_type, entity_id, action,"
        " from_status, to_status, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now(), user.id, user.name, ",".join(user.roles), entity_type, entity_id, action,
         from_status, to_status, json.dumps(detail or {})),
    )


def get_case(conn: sqlite3.Connection, alert_id: str) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE alert_id = ?", (alert_id,)).fetchone()
    return dict(row) if row else {"alert_id": alert_id, "status": NEW}


def cases_by_status(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {s: [] for s in STATUSES if s != NEW}
    for row in conn.execute("SELECT alert_id, status FROM cases"):
        out.setdefault(row["status"], []).append(row["alert_id"])
    return out


def all_cases(conn: sqlite3.Connection, alert_ids: list[str]) -> dict[str, dict]:
    if not alert_ids:
        return {}
    marks = ",".join("?" * len(alert_ids))
    rows = conn.execute(f"SELECT * FROM cases WHERE alert_id IN ({marks})", alert_ids).fetchall()
    return {r["alert_id"]: dict(r) for r in rows}


def _require_note(note: str) -> str:
    if not note or not note.strip():
        raise CaseError("A note explaining the decision is required")
    return note.strip()


def propose(conn: sqlite3.Connection, alert_id: str, user: User, disposition: str, note: str) -> dict:
    """Maker step: an analyst proposes how the alert should be closed."""
    if "analyst" not in user.roles:
        raise RoleError(f"{user.name} does not hold the analyst role and cannot propose a disposition")
    if disposition not in DISPOSITIONS:
        raise CaseError(f"Disposition must be one of: {', '.join(DISPOSITIONS)}")
    note = _require_note(note)
    case = get_case(conn, alert_id)
    if case["status"] not in (NEW, RETURNED):
        raise StateError(f"Alert is '{case['status']}'; only New or Returned alerts can be proposed")
    ts = now()
    with conn:
        conn.execute(
            "INSERT INTO cases (alert_id, status, disposition, maker_id, maker_name, maker_note, proposed_at,"
            " checker_id, checker_name, checker_note, decided_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)"
            " ON CONFLICT(alert_id) DO UPDATE SET status=excluded.status, disposition=excluded.disposition,"
            " maker_id=excluded.maker_id, maker_name=excluded.maker_name, maker_note=excluded.maker_note,"
            " proposed_at=excluded.proposed_at, checker_id=NULL, checker_name=NULL, checker_note=NULL,"
            " decided_at=NULL, updated_at=excluded.updated_at",
            (alert_id, PENDING, disposition, user.id, user.name, note, ts, ts),
        )
        log_event(conn, user, "alert", alert_id, "Proposed disposition", case["status"], PENDING,
                  {"disposition": disposition, "note": note})
    return get_case(conn, alert_id)


def _check(conn: sqlite3.Connection, alert_id: str, user: User, note: str, approve: bool) -> dict:
    """Checker step: a different reviewer approves or returns the maker's proposal."""
    if "reviewer" not in user.roles:
        raise RoleError(f"{user.name} does not hold the reviewer role and cannot approve or return")
    note = _require_note(note)
    case = get_case(conn, alert_id)
    if case["status"] != PENDING:
        raise StateError(f"Alert is '{case['status']}'; only alerts pending approval can be checked")
    if case["maker_id"] == user.id:
        raise SegregationOfDutiesError(
            "Maker-checker violation: the person who proposed a disposition cannot approve or return it")
    new_status = CLOSED if approve else RETURNED
    ts = now()
    with conn:
        conn.execute(
            "UPDATE cases SET status=?, checker_id=?, checker_name=?, checker_note=?, decided_at=?, updated_at=?"
            " WHERE alert_id=?",
            (new_status, user.id, user.name, note, ts, ts, alert_id),
        )
        log_event(conn, user, "alert", alert_id, "Approved disposition" if approve else "Returned to analyst",
                  PENDING, new_status, {"disposition": case["disposition"], "note": note})
    return get_case(conn, alert_id)


def approve(conn: sqlite3.Connection, alert_id: str, user: User, note: str) -> dict:
    return _check(conn, alert_id, user, note, approve=True)


def return_case(conn: sqlite3.Connection, alert_id: str, user: User, note: str) -> dict:
    return _check(conn, alert_id, user, note, approve=False)


def audit_trail(conn: sqlite3.Connection, entity_type: str | None = None, entity_id: str | None = None,
                limit: int = 200) -> list[dict]:
    sql, params = "SELECT * FROM audit_events WHERE 1=1", []
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    if entity_id:
        sql += " AND entity_id = ?"
        params.append(entity_id)
    sql += " ORDER BY event_id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return rows
