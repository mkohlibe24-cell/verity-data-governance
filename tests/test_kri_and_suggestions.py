"""KRI red/amber/green logic and automated rule suggestion."""

from __future__ import annotations

import re

import pandas as pd
import pytest

from pipeline.kri import rag_status
from pipeline.rule_suggester import pattern_to_regex, propose, suggestion_id


@pytest.mark.parametrize(
    ("value", "direction", "amber", "red", "expected"),
    [
        (99.9, "higher_is_better", 99.5, 98.0, "Green"),
        (99.5, "higher_is_better", 99.5, 98.0, "Green"),   # exactly on the amber line is still green
        (99.0, "higher_is_better", 99.5, 98.0, "Amber"),
        (97.0, "higher_is_better", 99.5, 98.0, "Red"),
        (0, "lower_is_better", 1, 2, "Green"),
        (1, "lower_is_better", 1, 2, "Amber"),
        (2, "lower_is_better", 1, 2, "Red"),
        (None, "lower_is_better", 1, 2, "Grey"),            # no data is never shown as green
    ],
)
def test_rag_status(value, direction, amber, red, expected):
    assert rag_status(value, direction, amber, red) == expected


def test_kri_thresholds_are_ordered_consistently(cfg):
    for k in cfg["kris"]:
        if k["direction"] == "higher_is_better":
            assert k["red"] <= k["amber"], k["id"]
        else:
            assert k["red"] >= k["amber"], k["id"]


def test_kri02_counts_open_critical_high_dq_issues_but_not_kri_breaches():
    from pipeline.common import connect
    from pipeline.issues import ensure_table
    from pipeline.kri import open_critical_high_dq_issues

    con = connect(None)
    ensure_table(con)
    for issue_id, source_type, severity, status in [
        ("ISS-1", "DQ rule", "critical", "Open"),         # counted
        ("ISS-2", "Reconciliation", "high", "Open"),      # counted
        ("ISS-3", "DQ rule", "high", "Closed"),           # closed
        ("ISS-4", "DQ rule", "medium", "Open"),           # not critical/high
        ("ISS-5", "KRI breach", "high", "Open"),          # KRI-02's own breach must not count
    ]:
        con.execute("INSERT INTO governance.issue_log (issue_id, source_type, severity, status) VALUES (?, ?, ?, ?)",
                    [issue_id, source_type, severity, status])
    assert open_critical_high_dq_issues(con) == 2


def test_pattern_to_regex_matches_the_profiled_shape():
    rx = pattern_to_regex("9999-99-99")
    assert re.fullmatch(rx, "2020-01-31")
    assert not re.fullmatch(rx, "31/01/2020")


def profile_row(column, **kw):
    base = {"column": column, "data_type": "VARCHAR", "rows": 10_000, "null_count": 0, "distinct_count": 5000,
            "min": None, "max": None, "top_pattern": None, "top_pattern_share": None, "top_values": None}
    base.update(kw)
    return base


def test_suggestions_skip_existing_rules_and_rare_values(cfg):
    prof = pd.DataFrame([
        profile_row("city"),                                       # no nulls, no rule yet -> not_null
        profile_row("trans_num"),                                  # already has a not_null rule -> nothing new
        profile_row("gender", rows=100_000, distinct_count=3, top_values=[
            {"value": "F", "count": 50_000}, {"value": "M", "count": 49_999}, {"value": "U", "count": 1}]),
    ])
    existing = [{"table": "transactions", "column": "trans_num", "check": "not_null"},
                {"table": "transactions", "column": "gender", "check": "not_null"}]
    out = {(s["column"], s["check"]): s for s in propose(prof, existing, cfg["dq"]["suggestions"])}
    assert ("city", "not_null") in out
    assert ("trans_num", "not_null") not in out
    allowed = out[("gender", "allowed_values")]
    assert allowed["params"]["values"] == ["F", "M"]      # the rare 'U' is left for the steward to review
    assert allowed["confidence"] == "medium"


def test_suggestion_ids_are_stable():
    assert suggestion_id("transactions", "city", "not_null") == suggestion_id("transactions", "city", "not_null")
    assert suggestion_id("transactions", "city", "not_null") != suggestion_id("transactions", "city", "regex")
