"""Data steward command line: review automatically suggested DQ rules.

    python pipeline/steward.py list
    python pipeline/steward.py approve SUG-1A2B3C --by "Muskaan Kohli" --reason "Matches the data standard"
    python pipeline/steward.py reject  SUG-1A2B3C --by "Muskaan Kohli" --reason "Too strict for new markets"

Decisions are appended to config/steward_decisions.yaml (who, when, why, and the exact rule
approved). The file is under version control, so git history is the audit trail. Approved
rules are picked up by the DQ engine on the next pipeline run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import load_config, resolve  # noqa: E402

HEADER = (
    "# Data steward decisions on automatically suggested DQ rules (human-in-the-loop).\n"
    "# Written by pipeline/steward.py or the web app. Approved rules are loaded by the DQ engine\n"
    "# on the next run. Do not edit by hand: git history of this file is the audit trail.\n"
)


def load_suggestions(cfg: dict) -> list[dict]:
    path = resolve(cfg["paths"]["reports_dir"]) / "rule_suggestions.json"
    if not path.exists():
        raise SystemExit("No suggestions yet. Run the pipeline first.")
    return json.loads(path.read_text(encoding="utf-8"))


def record_decision(cfg: dict, suggestion_id: str, decision: str, by: str, reason: str) -> dict:
    """Validate and append one decision. Shared by the CLI and the API (Tier 2)."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not by.strip() or not reason.strip():
        raise ValueError("A decision needs the steward's name and a reason")
    suggestions = {s["suggestion_id"]: s for s in load_suggestions(cfg)}
    if suggestion_id not in suggestions:
        raise ValueError(f"Unknown suggestion {suggestion_id}")
    path = resolve(cfg["dq"]["decisions_file"])
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    decisions = data.get("decisions") or []
    if any(d["suggestion_id"] == suggestion_id for d in decisions):
        raise ValueError(f"{suggestion_id} already has a decision; decisions are not overwritten")
    entry = {
        "suggestion_id": suggestion_id, "decision": decision, "decided_by": by.strip(),
        "decided_at": dt.datetime.now().isoformat(timespec="seconds"), "reason": reason.strip(),
        "rule": suggestions[suggestion_id]["proposed_rule"],
    }
    decisions.append(entry)
    path.write_text(HEADER + yaml.safe_dump({"decisions": decisions}, sort_keys=False), encoding="utf-8")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Review suggested data quality rules")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("suggestion_id")
        p.add_argument("--by", required=True)
        p.add_argument("--reason", required=True)
    args = parser.parse_args()
    cfg = load_config()
    if args.cmd == "list":
        for s in load_suggestions(cfg):
            print(f"{s['suggestion_id']}  [{s['status']:<8}] {s['column']:<12} {s['check']:<15} "
                  f"would fail {s['would_fail_records']:>7,} ({s['would_fail_pct']}%)  - {s['evidence']}")
        return
    entry = record_decision(cfg, args.suggestion_id, "approved" if args.cmd == "approve" else "rejected",
                            args.by, args.reason)
    print(f"Recorded: {entry['suggestion_id']} {entry['decision']} by {entry['decided_by']} at {entry['decided_at']}")


if __name__ == "__main__":
    main()
