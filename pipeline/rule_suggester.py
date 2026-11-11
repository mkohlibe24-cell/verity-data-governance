"""Step 3 - Automated DQ rule suggestion (human-in-the-loop).

The profile tells us what the data normally looks like. From it, we propose new rules:
  * not_null        - a column that currently has no nulls
  * allowed_values  - a text column with a small set of values
  * range           - a numeric column (bounds = observed min/max)
  * regex           - a text column where almost every value has the same shape
  * unique          - a column where (almost) every value is distinct

A suggestion is NEVER applied automatically. It goes to a "pending approval" list with
evidence and an impact preview (how many current records would fail). A data steward
approves or rejects it (pipeline/steward.py, or the web app in Tier 2). Approved rules
are loaded by the DQ engine on the next run, and every decision is recorded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

import pandas as pd

from pipeline.common import RunContext, load_yaml, resolve
from pipeline.dq_engine import failing_records_sql, load_rules

TABLE = "transactions"
# Columns that are derived/raw duplicates or technical: suggestions there add noise
SKIP = {"txn_ts_raw", "dob_raw", "is_fraud_raw"}


def suggestion_id(table: str, column: str, check: str) -> str:
    """Deterministic ID so a steward's decision still applies after the pipeline re-runs."""
    return "SUG-" + hashlib.sha1(f"{table}|{column}|{check}".encode()).hexdigest()[:6].upper()


def pattern_to_regex(pattern: str) -> str:
    """'9999-99-99' -> '[0-9]{4}-[0-9]{2}-[0-9]{2}' (runs of 9 = digits, runs of A = letters)."""
    out = []
    for run in re.finditer(r"9+|A+|[^9A]+", pattern):
        s = run.group(0)
        if s[0] == "9":
            out.append(f"[0-9]{{{len(s)}}}")
        elif s[0] == "A":
            out.append(f"[A-Za-z]{{{len(s)}}}")
        else:
            out.append(re.escape(s))
    return "".join(out)


def existing_checks(rules: list[dict]) -> set[tuple[str, str]]:
    found = set()
    for r in rules:
        if r["table"] != TABLE:
            continue
        for c in r.get("columns") or [r.get("column")]:
            found.add((c, r["check"]))
    return found


def propose(prof: pd.DataFrame, rules: list[dict], scfg: dict) -> list[dict]:
    have = existing_checks(rules)
    out = []
    for r in prof.itertuples():
        col = r.column
        if col in SKIP or r.rows < scfg["min_rows"]:
            continue
        non_null = r.rows - r.null_count

        def add(check, params, dimension, evidence, confidence, col=col):
            if (col, check) in have:
                return
            out.append({
                "suggestion_id": suggestion_id(TABLE, col, check), "table": TABLE, "column": col,
                "check": check, "params": params, "dimension": dimension, "evidence": evidence,
                "confidence": confidence,
            })

        if r.null_count == 0:
            add("not_null", {}, "Completeness", f"0 nulls in {r.rows:,} records", "high")
        if r.data_type == "VARCHAR" and r.top_values and r.distinct_count <= scfg["max_allowed_values"]:
            # Ignore very rare values (<0.01%): they are more likely errors than valid codes
            common = [v for v in r.top_values if v["value"] is not None and v["count"] / non_null >= 0.0001]
            rare = [v for v in r.top_values if v["value"] is not None and v["count"] / non_null < 0.0001]
            note = f"; {len(rare)} rare value(s) excluded for steward review" if rare else ""
            add("allowed_values", {"values": sorted(v["value"] for v in common)}, "Validity",
                f"{len(common)} distinct values cover {sum(v['count'] for v in common) / non_null:.3%} of records{note}",
                "high" if not rare else "medium")
        if r.data_type.startswith(("DECIMAL", "DOUBLE", "BIGINT", "INTEGER")) and r.min is not None:
            lo, hi = float(r.min), float(r.max)
            lo_b = math.floor(lo) if lo >= 0 else math.floor(lo * 1.1)
            hi_b = math.ceil(hi * 1.1) if hi > 0 else math.ceil(hi)
            add("range", {"min": lo_b, "max": hi_b}, "Validity",
                f"Observed range {lo:,.4g} to {hi:,.4g}; proposed bounds include a 10% margin", "medium")
        if (r.data_type == "VARCHAR" and r.top_pattern and r.top_pattern_share is not None
                and r.top_pattern_share >= scfg["min_pattern_share"] and len(r.top_pattern) <= 40):
            add("regex", {"pattern": pattern_to_regex(r.top_pattern)}, "Validity",
                f"{r.top_pattern_share:.3%} of values have the shape '{r.top_pattern}'", "high")
        if non_null and r.distinct_count / non_null >= scfg["unique_share"] and r.data_type == "VARCHAR":
            add("unique", {}, "Uniqueness", f"{r.distinct_count:,} distinct of {non_null:,} non-null values", "medium")
    return out


def impact_preview(con, s: dict, relation: str, key: str) -> tuple[int, float]:
    """Run the proposed rule now (read-only) to show how many records it would fail."""
    rule = {"id": s["suggestion_id"], "check": s["check"], "column": s["column"], "params": s["params"]}
    sql = failing_records_sql(rule, relation, key)
    failed = con.execute(f"SELECT count(*) FROM ({sql})").fetchall()[0][0]
    total = con.execute(f"SELECT count(*) FROM {relation}").fetchall()[0][0]
    return int(failed), round(100.0 * failed / total, 4) if total else 0.0


def load_decisions(cfg: dict) -> dict[str, dict]:
    path = resolve(cfg["dq"]["decisions_file"])
    if not path.exists():
        return {}
    decisions = (load_yaml(path) or {}).get("decisions") or []
    return {d["suggestion_id"]: d for d in decisions}


def run(ctx: RunContext) -> pd.DataFrame:
    con, cfg = ctx.con, ctx.cfg
    prof = con.execute("SELECT * FROM governance.profile_columns").df()
    prof["top_values"] = prof["top_values"].apply(lambda v: json.loads(v) if isinstance(v, str) else None)
    authored = [r for r in load_rules(cfg) if r.get("source") == "authored"]
    suggestions = propose(prof, authored, cfg["dq"]["suggestions"])
    decisions = load_decisions(cfg)
    t = cfg["dq"]["tables"][TABLE]
    for s in suggestions:
        s["would_fail_records"], s["would_fail_pct"] = impact_preview(con, s, t["relation"], t["key"])
        d = decisions.get(s["suggestion_id"])
        s["status"] = d["decision"] if d else "pending"
        s["decided_by"] = d.get("decided_by") if d else None
        s["decision_reason"] = d.get("reason") if d else None
        s["proposed_rule"] = {
            "name": f"Suggested: {s['column']} {s['check'].replace('_', ' ')}", "table": TABLE,
            "column": s["column"], "dimension": s["dimension"], "check": s["check"], "params": s["params"],
            "threshold_pct": 0.1, "severity": "medium", "is_cde": False, "action": "flag",
            "owner": "Transaction Data Steward", "reason_code": f"SUGGESTED_{s['check'].upper()}",
            "root_cause_hint": "New rule from automated profiling; investigate the first failures with the data owner.",
            "remediation": "Confirm the expected values with the data owner and correct at source.",
        }
    df = pd.DataFrame(suggestions)
    stored = df.copy()
    for c in ("params", "proposed_rule"):
        stored[c] = stored[c].apply(json.dumps)
    stored.insert(0, "run_id", ctx.run_id)
    con.register("sugg_df", stored)
    con.execute("CREATE OR REPLACE TABLE governance.rule_suggestions AS SELECT * FROM sugg_df")
    con.unregister("sugg_df")
    out = resolve(cfg["paths"]["reports_dir"]) / "rule_suggestions.json"
    out.write_text(json.dumps(suggestions, indent=2, default=str), encoding="utf-8")
    ctx.add_lineage("governance.profile_columns", "governance.rule_suggestions", "rule_suggester",
                    "Profile-driven rule proposals awaiting steward approval")
    pending = int((df["status"] == "pending").sum()) if not df.empty else 0
    ctx.metrics.update(rule_suggestions=len(df), rule_suggestions_pending=pending)
    ctx.log.info("Rule suggestions: %d proposed (%d pending steward approval)", len(df), pending)
    return df
