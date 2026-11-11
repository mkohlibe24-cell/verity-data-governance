"""Gold masking, reconciliation and the steward approval workflow."""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import VALID_CARD, load_bronze, raw_row

from pipeline.dq_engine import build_silver, load_rules, run_rules
from pipeline.gold import build_gold
from pipeline.reconciliation import reconcile
from pipeline.steward import record_decision

PARAMS = {"as_of": "2020-12-31 23:59:59", "period_start": "2019-01-01"}
SALT = "test-salt"


def build_layers(con, cfg, rows):
    load_bronze(con, rows)
    rules = load_rules(cfg)
    run_rules(con, rules, cfg["dq"]["tables"], "T1", "bronze", PARAMS)
    build_silver(con, rules, "T1", "2020-12-31 00:00:00")
    build_gold(con, SALT, 2020)
    bronze_n, bronze_amt = con.execute(
        "SELECT count(*), sum(TRY_CAST(amt AS DECIMAL(18,2))) FROM bronze.transactions_raw").fetchall()[0]
    con.execute("CREATE TABLE governance.source_manifest (run_id VARCHAR, source VARCHAR, file VARCHAR, sha256 VARCHAR, "
                "file_bytes BIGINT, records BIGINT, amount_total DECIMAL(18,2))")
    con.execute("INSERT INTO governance.source_manifest VALUES ('T1', 'test', 'f.csv', '', 0, ?, ?)", [bronze_n, bronze_amt])


def test_gold_contains_no_raw_card_numbers_or_names(con, cfg):
    build_layers(con, cfg, [raw_row(i) for i in range(5)])
    for table in ("fact_transactions", "dim_customer", "dim_merchant"):
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold' AND table_name = ? "
            "AND data_type = 'VARCHAR'", [table]).fetchall()]
        for col in cols:
            leaked = con.execute(f"SELECT count(*) FROM gold.{table} WHERE {col} IN (?, 'Ann', 'Lee', '1 Main St')",
                                 [VALID_CARD]).fetchall()[0][0]
            assert leaked == 0, f"raw PII in gold.{table}.{col}"
    token, masked, first, birth_year = con.execute(
        "SELECT card_token, card_number_masked, first_name_masked, birth_year FROM gold.dim_customer").fetchall()[0]
    assert token == hashlib.sha256((SALT + VALID_CARD).encode()).hexdigest()
    assert masked == "************1111"
    assert first == "A**"
    assert birth_year == 1980


def test_reconciliation_ties_out_then_detects_a_break(con, cfg):
    rows = [raw_row(i) for i in range(10)]
    rows[4] = raw_row(4, amt="-3.00")  # quarantined: must still tie out (bronze = silver + quarantine)
    build_layers(con, cfg, rows)
    res = reconcile(con, "T1", cfg).set_index("control_id")
    assert (res["status"] == "PASS").all(), res[res.status != "PASS"]
    con.execute("DELETE FROM gold.fact_transactions WHERE trans_num = (SELECT min(trans_num) FROM gold.fact_transactions)")
    res = reconcile(con, "T1", cfg).set_index("control_id")
    assert res.loc["REC-05", "status"] == "BREAK"
    assert res.loc["REC-05", "difference"] == -1
    assert res.loc["REC-06", "status"] == "BREAK"


def test_steward_decision_is_recorded_and_loaded(tmp_path, cfg):
    suggestion = {"suggestion_id": "SUG-TEST01", "status": "pending", "proposed_rule": {
        "name": "Suggested: city not null", "table": "transactions", "column": "city", "dimension": "Completeness",
        "check": "not_null", "params": {}, "threshold_pct": 0.1, "severity": "medium", "is_cde": False,
        "action": "flag", "owner": "Transaction Data Steward", "reason_code": "SUGGESTED_NOT_NULL"}}
    (tmp_path / "rule_suggestions.json").write_text(json.dumps([suggestion]), encoding="utf-8")
    cfg["paths"]["reports_dir"] = str(tmp_path)
    cfg["dq"]["decisions_file"] = str(tmp_path / "decisions.yaml")

    with pytest.raises(ValueError):
        record_decision(cfg, "SUG-TEST01", "approved", "Muskaan", "")  # a reason is mandatory
    entry = record_decision(cfg, "SUG-TEST01", "approved", "Muskaan", "Matches the data standard")
    assert entry["decided_by"] == "Muskaan"
    loaded = [r for r in load_rules(cfg) if r["id"] == "DQ-SUG-TEST01"]
    assert loaded and loaded[0]["source"] == "steward-approved suggestion"
    with pytest.raises(ValueError):
        record_decision(cfg, "SUG-TEST01", "rejected", "Someone", "changed my mind")  # never overwritten
