"""Shared test fixtures: an in-memory DuckDB and helpers to build tiny hand-made datasets.

Every test uses a few rows with a KNOWN answer, so a failing test points at one rule.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import connect, load_config  # noqa: E402
from pipeline.generate_data import RAW_COLUMNS  # noqa: E402
from pipeline.ingest import create_typed_view  # noqa: E402

VALID_CARD = "4111111111111111"  # Luhn-valid test number


def trans_id(i: int) -> str:
    return hashlib.md5(str(i).encode()).hexdigest()


def raw_row(i: int, **overrides) -> dict:
    """A valid raw (all-text) transaction; overrides break it in one specific way."""
    ts = dt.datetime(2020, 6, 1, 10, 0, 0) + dt.timedelta(hours=i)
    row = {
        "trans_date_trans_time": ts.strftime("%Y-%m-%d %H:%M:%S"), "cc_num": VALID_CARD,
        "merchant": "fraud_Test Shop", "category": "grocery_pos", "amt": "25.00", "first": "Ann", "last": "Lee",
        "gender": "F", "street": "1 Main St", "city": "Town", "state": "NY", "zip": "10001", "lat": "40.7",
        "long": "-74.0", "city_pop": "1000", "job": "Engineer", "dob": "1980-01-01", "trans_num": trans_id(i),
        "unix_time": str(calendar.timegm(ts.timetuple()) - 220881600), "merch_lat": "40.8", "merch_long": "-74.1",
        "is_fraud": "0",
    }
    row.update(overrides)
    return row


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def con():
    c = connect(None)
    yield c
    c.close()


def load_bronze(con, rows: list[dict], synthetic: bool = False) -> None:
    """Create bronze.transactions_raw from raw dicts and the typed view on top of it."""
    df = pd.DataFrame(rows, columns=RAW_COLUMNS).astype(object)
    df.insert(0, "bronze_row_id", range(1, len(df) + 1))
    df.insert(1, "_source", "test")
    df.insert(2, "_source_row_num", range(len(df)))
    df["is_synthetic"] = synthetic
    df["synthetic_tag"] = None
    df["_run_id"] = "TEST"
    df["_ingested_at"] = pd.Timestamp("2020-12-31")
    con.register("raw_df", df)
    con.execute("CREATE OR REPLACE TABLE bronze.transactions_raw AS SELECT * FROM raw_df")
    con.unregister("raw_df")
    create_typed_view(con)


def load_fact(con, rows: list[dict]) -> None:
    """Create a minimal gold.fact_transactions for monitoring-rule tests."""
    df = pd.DataFrame(rows)
    defaults = {"category": "grocery_pos", "merch_lat": 40.7, "merch_long": -74.0, "is_fraud": 0}
    for k, v in defaults.items():
        if k not in df:
            df[k] = v
        df[k] = df[k].fillna(v) if k in df else v
    df["trans_num"] = [trans_id(10_000 + i) for i in range(len(df))]
    con.register("fact_df", df)
    con.execute("""CREATE OR REPLACE TABLE gold.fact_transactions AS
                   SELECT trans_num, card_token, CAST(txn_ts AS TIMESTAMP) AS txn_ts,
                          CAST(amount AS DECIMAL(18, 2)) AS amount, category,
                          CAST(merch_lat AS DOUBLE) AS merch_lat, CAST(merch_long AS DOUBLE) AS merch_long,
                          CAST(is_fraud AS INTEGER) AS is_fraud
                   FROM fact_df""")
    con.unregister("fact_df")
