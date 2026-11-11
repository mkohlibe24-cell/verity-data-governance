"""Monitoring rules: each SQL rule fires on a hand-made pattern and stays quiet on normal behaviour."""

from __future__ import annotations

import datetime as dt

from conftest import load_fact

from pipeline.monitoring import rule_sql

T0 = dt.datetime(2020, 8, 1, 12, 0, 0)


def hits(con, cfg, rule_id) -> list[tuple]:
    return con.execute(f"SELECT card_token, amount FROM ({rule_sql(cfg, rule_id)}) ORDER BY txn_ts").fetchall()


def normal_history(card: str, n: int = 30, amount: float = 50.0, hour: int = 12) -> list[dict]:
    """One modest purchase a day at the same hour: the baseline every rule compares against."""
    return [{"card_token": card, "txn_ts": (T0 - dt.timedelta(days=n - i)).replace(hour=hour),
             "amount": amount + (i % 5)} for i in range(n)]


def test_tm01_high_value_vs_baseline(con, cfg):
    rows = normal_history("A") + [{"card_token": "A", "txn_ts": T0, "amount": 2000.0},
                                  {"card_token": "A", "txn_ts": T0 + dt.timedelta(days=1), "amount": 60.0}]
    load_fact(con, rows)
    assert hits(con, cfg, "TM-01") == [("A", 2000)]


def test_tm02_velocity(con, cfg):
    burst = [{"card_token": "B", "txn_ts": T0 + dt.timedelta(minutes=2 * i), "amount": 80.0} for i in range(6)]
    load_fact(con, normal_history("B") + burst)
    assert len(hits(con, cfg, "TM-02")) == 2  # the 5th and 6th transactions inside 10 minutes


def test_tm03_impossible_travel(con, cfg):
    rows = [{"card_token": "C", "txn_ts": T0, "amount": 40.0, "merch_lat": 40.71, "merch_long": -74.0},      # New York
            {"card_token": "C", "txn_ts": T0 + dt.timedelta(minutes=30), "amount": 900.0,
             "merch_lat": 34.05, "merch_long": -118.24}]                                                   # Los Angeles
    load_fact(con, rows)
    assert hits(con, cfg, "TM-03") == [("C", 900)]


def test_tm03_ignores_nearby_geocode_noise(con, cfg):
    rows = [{"card_token": "C", "txn_ts": T0, "amount": 40.0, "merch_lat": 40.71, "merch_long": -74.0},
            {"card_token": "C", "txn_ts": T0 + dt.timedelta(minutes=5), "amount": 30.0,
             "merch_lat": 41.5, "merch_long": -74.9}]  # ~120 km in 5 minutes: noise, below the 500 km floor
    load_fact(con, rows)
    assert hits(con, cfg, "TM-03") == []


def test_tm04_card_testing(con, cfg):
    tiny = [{"card_token": "D", "txn_ts": T0 + dt.timedelta(minutes=3 * i), "amount": 1.5} for i in range(5)]
    load_fact(con, normal_history("D") + tiny)
    assert hits(con, cfg, "TM-04") == [("D", 1.5)]  # alert on the 5th small transaction


def test_tm05_round_amount_structuring(con, cfg):
    rows = [{"card_token": "E", "txn_ts": T0 + dt.timedelta(hours=10 * i), "amount": a}
            for i, a in enumerate([900.0, 950.0, 990.0])]
    load_fact(con, rows)
    assert hits(con, cfg, "TM-05") == [("E", 990)]


def test_tm06_unusual_hour(con, cfg):
    rows = normal_history("F") + [{"card_token": "F", "txn_ts": T0.replace(hour=23), "amount": 400.0}]
    load_fact(con, rows)
    assert hits(con, cfg, "TM-06") == [("F", 400)]


def test_tm07_dormant_reactivation(con, cfg):
    rows = normal_history("G", n=5) + [
        {"card_token": "G", "txn_ts": T0 + dt.timedelta(days=90), "amount": 500.0},
        {"card_token": "G", "txn_ts": T0 + dt.timedelta(days=90, hours=3), "amount": 50.0},   # below $200
        {"card_token": "G", "txn_ts": T0 + dt.timedelta(days=90, hours=5), "amount": 700.0},
        {"card_token": "G", "txn_ts": T0 + dt.timedelta(days=95), "amount": 700.0},           # outside 48h
    ]
    load_fact(con, rows)
    assert hits(con, cfg, "TM-07") == [("G", 500), ("G", 700)]


def test_tm08_category_outlier(con, cfg):
    baseline = [{"card_token": f"H{i % 7}", "txn_ts": dt.datetime(2020, 3, 1) + dt.timedelta(hours=i),
                 "amount": 10.0 + (i % 100), "category": "travel"} for i in range(300)]  # all below the $300 floor
    rows = baseline + [{"card_token": "H1", "txn_ts": T0, "amount": 5000.0, "category": "travel"}]
    load_fact(con, rows)
    assert hits(con, cfg, "TM-08") == [("H1", 5000)]


def test_normal_behaviour_triggers_nothing(con, cfg):
    load_fact(con, normal_history("Z", n=60))
    for rid in cfg["monitoring"]["rules"]:
        assert hits(con, cfg, rid) == [], rid
