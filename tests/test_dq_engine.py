"""Data quality engine: each rule catches exactly the record it should, and quarantine never loses data."""

from __future__ import annotations

import pytest
from conftest import load_bronze, raw_row, trans_id

from pipeline.dq_engine import (
    build_silver,
    evaluate_table_check,
    failing_records_sql,
    load_rules,
    run_rules,
    validate_rules,
)

PARAMS = {"as_of": "2020-12-31 23:59:59", "period_start": "2019-01-01"}
REL, KEY = "bronze.transactions_typed", "bronze_row_id"


def failing_ids(con, rule) -> set[int]:
    return {int(r[0]) for r in con.execute(failing_records_sql(rule, REL, KEY, PARAMS)).fetchall()}


def rule_by_id(cfg, rule_id):
    return next(r for r in load_rules(cfg) if r["id"] == rule_id)


@pytest.mark.parametrize(
    ("rule_id", "override"),
    [
        ("DQ-C03", {"amt": None}),                                # missing amount
        ("DQ-V01", {"trans_date_trans_time": "31/12/2020 10:00"}),  # wrong date format
        ("DQ-V02", {"amt": "-25.00"}),                            # negative amount
        ("DQ-V04", {"category": "unknown"}),                      # not in the reference list
        ("DQ-V06", {"zip": "1234"}),                              # leading zero lost
        ("DQ-V08", {"gender": "X"}),                              # outside the code set
        ("DQ-V10", {"cc_num": "4111111111111112"}),               # fails the Luhn check
        ("DQ-K03", {"dob": "2021-01-01"}),                        # born after the transaction
        ("DQ-T01", {"trans_date_trans_time": "2021-03-01 10:00:00"}),  # future-dated
        ("DQ-T03", {"trans_date_trans_time": "1970-01-01 00:00:00"}),  # default/stale date
    ],
)
def test_rule_flags_only_the_broken_record(con, cfg, rule_id, override):
    rows = [raw_row(i) for i in range(5)]
    rows[2] = raw_row(2, **override)
    load_bronze(con, rows)
    assert failing_ids(con, rule_by_id(cfg, rule_id)) == {3}  # bronze_row_id of rows[2]


def test_unique_keeps_first_occurrence(con, cfg):
    rows = [raw_row(0), raw_row(1), raw_row(2, trans_num=trans_id(1))]  # third row duplicates the second's ID
    load_bronze(con, rows)
    assert failing_ids(con, rule_by_id(cfg, "DQ-U01")) == {3}


def test_group_consistency_flags_minority_value(con, cfg):
    rows = [raw_row(i) for i in range(4)] + [raw_row(4, dob="1955-05-05")]
    load_bronze(con, rows)
    assert failing_ids(con, rule_by_id(cfg, "DQ-K02")) == {5}


def test_valid_data_passes_every_row_rule(con, cfg):
    load_bronze(con, [raw_row(i) for i in range(10)])
    for rule in load_rules(cfg):
        if rule["table"] == "transactions" and rule["check"] not in ("freshness", "daily_volume") and rule["id"] != "DQ-K01":
            assert failing_ids(con, rule) == set(), rule["id"]


def test_threshold_decides_pass_or_fail(con, cfg):
    rows = [raw_row(i) for i in range(200)]
    rows[0] = raw_row(0, zip="1234")  # 0.5% failure: DQ-V06 tolerates 1%, so it PASSES
    load_bronze(con, rows)
    rules = [rule_by_id(cfg, "DQ-V06"), rule_by_id(cfg, "DQ-V02")]
    res = run_rules(con, rules, cfg["dq"]["tables"], "T1", "bronze", PARAMS).set_index("rule_id")
    assert res.loc["DQ-V06", "records_failed"] == 1
    assert res.loc["DQ-V06", "status"] == "PASS"
    assert res.loc["DQ-V02", "status"] == "PASS"


def test_quarantine_and_silver_never_lose_records(con, cfg):
    rows = [raw_row(i) for i in range(10)]
    rows[1] = raw_row(1, amt="-5.00")      # quarantine (DQ-V02)
    rows[2] = raw_row(2, category="xxx")   # quarantine (DQ-V04)
    rows[3] = raw_row(3, zip="1234")       # flag only (DQ-V06), stays in silver
    load_bronze(con, rows)
    rules = load_rules(cfg)
    run_rules(con, rules, cfg["dq"]["tables"], "T1", "bronze", PARAMS)
    build_silver(con, rules, "T1", "2020-12-31 00:00:00")
    q = con.execute("SELECT bronze_row_id, reason_codes FROM silver.dq_quarantine ORDER BY 1").fetchall()
    silver = con.execute("SELECT count(*) FROM silver.transactions").fetchall()[0][0]
    assert [r[0] for r in q] == [2, 3]
    assert q[0][1] == ["NON_POSITIVE_AMOUNT"]
    assert silver + len(q) == 10
    zip_, flags, merchant = con.execute(
        "SELECT zip, dq_flags, merchant_name FROM silver.transactions WHERE bronze_row_id = 4").fetchall()[0]
    assert zip_ == "01234"           # standardised in silver
    assert "DQ-V06" in flags          # but the failure is still visible
    assert merchant == "Test Shop"    # simulator prefix stripped


def test_daily_volume_and_freshness(con, cfg):
    rows = [raw_row(i) for i in range(48)]  # hourly from 2020-06-01 10:00: 14, 24 and 10 rows per day
    load_bronze(con, rows)
    vol = dict(rule_by_id(cfg, "DQ-C09"), params={"min_rows": 12})
    checked, failed, _ = evaluate_table_check(con, vol, REL, {"as_of": "2020-06-03 00:00:00", "period_start": "2020-06-01"})
    assert (checked, failed) == (3, 1)  # 3 days in range; 2020-06-03 has only 10 rows (< 12)
    fresh = rule_by_id(cfg, "DQ-T02")
    assert evaluate_table_check(con, fresh, REL, {"as_of": "2020-06-03 00:00:00"})[1] == 0
    assert evaluate_table_check(con, fresh, REL, {"as_of": "2020-06-10 00:00:00"})[1] == 1


def test_invalid_rule_definitions_are_rejected(cfg):
    tables = cfg["dq"]["tables"]
    good = {"id": "X", "name": "x", "table": "transactions", "dimension": "Validity", "check": "not_null",
            "threshold_pct": 0, "severity": "low", "action": "flag", "owner": "me", "column": "amt_raw"}
    validate_rules([good], tables)
    with pytest.raises(ValueError):
        validate_rules([dict(good, dimension="Prettiness")], tables)
    with pytest.raises(ValueError):
        validate_rules([good, dict(good)], tables)  # duplicate id
    with pytest.raises(ValueError):
        validate_rules([dict(good, table="fact_transactions", action="quarantine")], tables)
