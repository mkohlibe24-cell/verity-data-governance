"""Synthetic generator: adds TAGGED rows on top of the Kaggle data.

Why it exists:
  * The Kaggle simulator has no dormant accounts, no impossible travel, no card testing
    and no structuring, so some monitoring rules would have nothing to find.
  * The Kaggle files are almost perfectly clean, so the data quality controls would have
    nothing to catch either.

Every generated row carries is_synthetic = TRUE and a synthetic_tag such as
'scenario:card_testing' or 'dq:negative_amount'. Results can therefore always be split into
"original data" and "injected", which keeps reported numbers honest.

The generator is reproducible: a fixed seed gives exactly the same rows every run.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math

import duckdb
import numpy as np
import pandas as pd

# Column order of the Kaggle source files (the unnamed index column is handled in ingest)
RAW_COLUMNS = [
    "trans_date_trans_time", "cc_num", "merchant", "category", "amt", "first", "last", "gender",
    "street", "city", "state", "zip", "lat", "long", "city_pop", "job", "dob", "trans_num",
    "unix_time", "merch_lat", "merch_long", "is_fraud",
]

# Made-up names for synthetic dormant-account cardholders (no real people)
FIRST_NAMES = ["Avery", "Jordan", "Riley", "Morgan", "Casey", "Quinn", "Rowan", "Harper", "Emerson", "Sage"]
LAST_NAMES = ["Hollis", "Marlow", "Brennan", "Calder", "Whitfield", "Ashby", "Lindqvist", "Okafor", "Delgado", "Pryce"]

POS_CATEGORIES = ["shopping_pos", "grocery_pos", "misc_pos", "gas_transport", "food_dining"]
TS_FMT = "%Y-%m-%d %H:%M:%S"


def luhn_check_digit(partial: str) -> str:
    """Return the Luhn check digit that makes partial + digit a valid card number."""
    total = 0
    for i, ch in enumerate(reversed(partial)):
        d = int(ch)
        if i % 2 == 0:  # these positions are doubled once the check digit is appended
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


class Generator:
    """Builds scenario rows and defect rows in the same raw (all-text) format as the source."""

    def __init__(self, con: duckdb.DuckDBPyConnection, cfg: dict, scale: float = 1.0):
        gcfg = cfg["generator"]
        self.cfg = cfg
        self.con = con
        self.scale = scale
        self.rng = np.random.default_rng(gcfg["seed"])
        self.offset = int(gcfg["unix_time_offset_seconds"])
        self.start = dt.datetime.fromisoformat(cfg["project"]["period_start"])
        self.end = dt.datetime.fromisoformat(cfg["project"]["batch_as_of"])
        # Card profiles: one row per card. min() is deterministic and attributes are constant per card.
        self.profiles = con.execute(
            """
            SELECT cc_num, min("first") AS "first", min("last") AS "last", min(gender) AS gender,
                   min(street) AS street, min(city) AS city, min(state) AS state, min(zip) AS zip,
                   min(lat) AS lat, min("long") AS "long", min(city_pop) AS city_pop, min(job) AS job,
                   min(dob) AS dob
            FROM bronze.transactions_raw WHERE NOT is_synthetic
            GROUP BY cc_num ORDER BY cc_num
            """
        ).df().to_dict("records")
        merchants = con.execute(
            """
            SELECT merchant, min(category) AS category FROM bronze.transactions_raw
            WHERE NOT is_synthetic GROUP BY merchant HAVING count(DISTINCT category) = 1 ORDER BY merchant
            """
        ).df()
        self.merchants = merchants.groupby("category")["merchant"].apply(list).to_dict()
        self.existing_cards = {p["cc_num"] for p in self.profiles}

    # ------------------------------------------------------------------ helpers
    def n(self, count: int) -> int:
        return max(1, round(count * self.scale))

    def trans_num(self) -> str:
        return "".join(self.rng.choice(list("0123456789abcdef"), 32))

    def rand_ts(self, start: dt.datetime, end: dt.datetime) -> dt.datetime:
        seconds = int((end - start).total_seconds())
        return start + dt.timedelta(seconds=int(self.rng.integers(0, seconds)))

    def profile(self) -> dict:
        return self.profiles[int(self.rng.integers(0, len(self.profiles)))]

    def merchant(self, category: str) -> str:
        options = self.merchants.get(category) or next(iter(self.merchants.values()))
        return options[int(self.rng.integers(0, len(options)))]

    def near(self, lat: float, lon: float, max_deg: float = 0.8) -> tuple[float, float]:
        return lat + self.rng.uniform(-max_deg, max_deg), lon + self.rng.uniform(-max_deg, max_deg)

    def new_card(self) -> str:
        while True:
            partial = "4" + "".join(str(d) for d in self.rng.integers(0, 10, 14))
            card = partial + luhn_check_digit(partial)
            if card not in self.existing_cards:
                self.existing_cards.add(card)
                return card

    def row(self, p: dict, ts: dt.datetime, amount: float, category: str, is_fraud: int, tag: str,
            lat: float | None = None, lon: float | None = None, **overrides) -> dict:
        if lat is None:
            lat, lon = self.near(float(p["lat"]), float(p["long"]))
        r = {
            "trans_date_trans_time": ts.strftime(TS_FMT), "cc_num": p["cc_num"],
            "merchant": self.merchant(category), "category": category, "amt": f"{amount:.2f}",
            "first": p["first"], "last": p["last"], "gender": p["gender"], "street": p["street"],
            "city": p["city"], "state": p["state"], "zip": p["zip"], "lat": p["lat"], "long": p["long"],
            "city_pop": p["city_pop"], "job": p["job"], "dob": p["dob"], "trans_num": self.trans_num(),
            "unix_time": str(calendar.timegm(ts.timetuple()) - self.offset),
            "merch_lat": f"{lat:.6f}", "merch_long": f"{lon:.6f}", "is_fraud": str(is_fraud),
            "synthetic_tag": f"scenario:{tag}", "_source": "generator_scenarios",
        }
        r.update(overrides)
        return r

    # ------------------------------------------------------------------ risk scenarios
    def card_testing(self, count: int) -> list[dict]:
        """Several tiny purchases in minutes, then one large online purchase."""
        rows = []
        for _ in range(self.n(count)):
            p = self.profile()
            t = self.rand_ts(self.start + dt.timedelta(days=30), self.end - dt.timedelta(days=1))
            for _ in range(int(self.rng.integers(6, 13))):
                t += dt.timedelta(seconds=int(self.rng.integers(20, 150)))
                rows.append(self.row(p, t, self.rng.uniform(0.5, 4.99),
                                     str(self.rng.choice(["misc_net", "shopping_net"])), 1, "card_testing"))
            t += dt.timedelta(seconds=int(self.rng.integers(120, 1200)))
            rows.append(self.row(p, t, self.rng.uniform(600, 1500), "shopping_net", 1, "card_testing"))
        return rows

    def velocity_burst(self, count: int) -> list[dict]:
        """Many in-store purchases within about 10 minutes."""
        rows = []
        for _ in range(self.n(count)):
            p = self.profile()
            t = self.rand_ts(self.start + dt.timedelta(days=30), self.end - dt.timedelta(days=1))
            for _ in range(int(self.rng.integers(6, 11))):
                t += dt.timedelta(seconds=int(self.rng.integers(30, 90)))
                rows.append(self.row(p, t, self.rng.uniform(40, 400),
                                     str(self.rng.choice(POS_CATEGORIES)), 1, "velocity_burst"))
        return rows

    def impossible_travel(self, count: int) -> list[dict]:
        """A genuine purchase near home, then a purchase 1,500+ km away within 20-90 minutes."""
        rows = []
        for _ in range(self.n(count)):
            p = self.profile()
            far = [q for q in self.profiles
                   if haversine_km(float(p["lat"]), float(p["long"]), float(q["lat"]), float(q["long"])) > 1500]
            q = far[int(self.rng.integers(0, len(far)))]
            t0 = self.rand_ts(self.start + dt.timedelta(days=30), self.end - dt.timedelta(days=1))
            rows.append(self.row(p, t0, self.rng.uniform(20, 150), str(self.rng.choice(POS_CATEGORIES)),
                                 0, "impossible_travel"))
            t1 = t0 + dt.timedelta(minutes=int(self.rng.integers(20, 90)))
            lat, lon = self.near(float(q["lat"]), float(q["long"]), 0.3)
            rows.append(self.row(p, t1, self.rng.uniform(200, 1200),
                                 str(self.rng.choice(["shopping_pos", "travel", "misc_pos"])), 1,
                                 "impossible_travel", lat=lat, lon=lon))
        return rows

    def round_amount_structuring(self, count: int) -> list[dict]:
        """3-5 round amounts just under $1,000 within two days."""
        rows = []
        for _ in range(self.n(count)):
            p = self.profile()
            t = self.rand_ts(self.start + dt.timedelta(days=30), self.end - dt.timedelta(days=3))
            for _ in range(int(self.rng.integers(3, 6))):
                t += dt.timedelta(minutes=int(self.rng.integers(60, 600)))
                amount = float(self.rng.choice([850, 900, 940, 950, 970, 980, 990]))
                rows.append(self.row(p, t, amount,
                                     str(self.rng.choice(["shopping_pos", "misc_pos", "travel", "shopping_net"])),
                                     1, "round_amount_structuring"))
        return rows

    def dormant_reactivation(self, count: int) -> list[dict]:
        """A new card used a little, silent for months, then high-value spend in a short burst."""
        rows = []
        for _ in range(self.n(count)):
            base = self.profile()
            p = dict(base, cc_num=self.new_card(),
                     first=str(self.rng.choice(FIRST_NAMES)), last=str(self.rng.choice(LAST_NAMES)))
            t = self.rand_ts(self.start + dt.timedelta(days=10), dt.datetime(2019, 9, 30))
            for _ in range(int(self.rng.integers(5, 11))):
                t += dt.timedelta(hours=int(self.rng.integers(12, 60)))
                rows.append(self.row(p, t, self.rng.uniform(10, 120),
                                     str(self.rng.choice(POS_CATEGORIES)), 0, "dormant_reactivation"))
            gap_days = int(self.rng.integers(90, 300))
            t = min(t + dt.timedelta(days=gap_days), self.end - dt.timedelta(days=2))
            for _ in range(int(self.rng.integers(3, 7))):
                t += dt.timedelta(minutes=int(self.rng.integers(30, 480)))
                rows.append(self.row(p, t, self.rng.uniform(250, 2000),
                                     str(self.rng.choice(["shopping_net", "misc_net", "travel", "shopping_pos"])),
                                     1, "dormant_reactivation"))
        return rows

    # ------------------------------------------------------------------ data quality defects
    def dq_defects(self, counts: dict) -> list[dict]:
        """Copy random real rows and break them in one specific way each."""
        plan = [(defect, self.n(c)) for defect, c in counts.items()]
        total = sum(c for _, c in plan)
        kaggle_rows = self.con.execute(
            "SELECT count(*) FROM bronze.transactions_raw WHERE NOT is_synthetic").fetchall()[0][0]
        ids = self.rng.choice(np.arange(1, kaggle_rows + 1), size=total, replace=False)
        id_df = pd.DataFrame({"bronze_row_id": ids, "pick_order": np.arange(total)})
        self.con.register("defect_ids", id_df)
        base = self.con.execute(
            f"""SELECT {", ".join(f'b."{c}"' for c in RAW_COLUMNS)}
                FROM defect_ids d JOIN bronze.transactions_raw b USING (bronze_row_id)
                ORDER BY d.pick_order"""
        ).df()
        self.con.unregister("defect_ids")
        records = base.astype(object).where(base.notna(), None).to_dict("records")

        rows, i = [], 0
        for defect, c in plan:
            for _ in range(c):
                r = dict(records[i])
                i += 1
                if defect != "duplicate_record":
                    r["trans_num"] = self.trans_num()
                if defect not in ("duplicate_record", "double_posting"):
                    # Shift the copy a few minutes so it is a NEW transaction with one defect, not also a
                    # double posting of the record it was copied from (that is its own defect type)
                    ts = dt.datetime.strptime(r["trans_date_trans_time"], TS_FMT)
                    r["trans_date_trans_time"] = (ts + dt.timedelta(minutes=int(self.rng.integers(3, 58)))).strftime(TS_FMT)
                self.mutate(r, defect)
                r["synthetic_tag"] = f"dq:{defect}"
                r["_source"] = "generator_dq_defects"
                rows.append(r)
        return rows

    def mutate(self, r: dict, defect: str) -> None:
        rng = self.rng
        ts = dt.datetime.strptime(r["trans_date_trans_time"], TS_FMT)
        if defect == "null_trans_num":
            r["trans_num"] = None
        elif defect == "null_card_number":
            r["cc_num"] = None
        elif defect == "null_amount":
            r["amt"] = None
        elif defect == "null_timestamp":
            r["trans_date_trans_time"] = None
        elif defect == "null_merchant":
            r["merchant"] = None
        elif defect == "null_category":
            r["category"] = None
        elif defect == "missing_merchant_coords":
            r["merch_lat"], r["merch_long"] = None, None
        elif defect == "missing_fraud_label":
            r["is_fraud"] = None
        elif defect in ("duplicate_record", "double_posting"):
            pass  # an exact copy (same ID) or the same business transaction with a new ID
        elif defect == "negative_amount":
            r["amt"] = f"-{r['amt']}"
        elif defect == "extreme_amount":
            r["amt"] = f"{rng.uniform(60000, 250000):.2f}"
        elif defect == "bad_timestamp_format":
            r["trans_date_trans_time"] = str(rng.choice(
                [ts.strftime("%d/%m/%Y %H:%M"), "2020-13-45 25:61:00", "N/A", ts.strftime("%Y%m%d")]))
        elif defect == "future_timestamp":
            r["trans_date_trans_time"] = self.rand_ts(dt.datetime(2021, 1, 15), dt.datetime(2021, 6, 30)).strftime(TS_FMT)
        elif defect == "stale_timestamp":
            r["trans_date_trans_time"] = str(rng.choice(
                ["1970-01-01 00:00:00", self.rand_ts(dt.datetime(2005, 1, 1), dt.datetime(2009, 12, 31)).strftime(TS_FMT)]))
        elif defect == "invalid_category":
            r["category"] = str(rng.choice(["unknown", "GROCERY-POS", "misc net", "test_category"]))
        elif defect == "invalid_state":
            r["state"] = str(rng.choice(["XX", "Calif", "ZZ", "99"]))
        elif defect == "invalid_coordinates":
            if rng.random() < 0.5:
                r["merch_lat"] = f"{rng.uniform(95, 180):.6f}"
            else:
                r["merch_long"] = f"{rng.uniform(-400, -190):.6f}"
        elif defect == "invalid_card_number":
            cc = r["cc_num"]
            r["cc_num"] = cc[:-1] + str((int(cc[-1]) + int(rng.integers(1, 10))) % 10)
        elif defect == "dob_after_transaction":
            r["dob"] = (ts.date() + dt.timedelta(days=int(rng.integers(365, 1825)))).isoformat()
        elif defect == "inconsistent_customer":
            r["dob"] = (dt.date(1950, 1, 1) + dt.timedelta(days=int(rng.integers(0, 18000)))).isoformat()
        elif defect == "invalid_gender":
            r["gender"] = str(rng.choice(["U", "X", "Male", "0"]))
        elif defect == "invalid_fraud_flag":
            r["is_fraud"] = str(rng.choice(["2", "Y", "-1"]))
        else:
            raise ValueError(f"Unknown defect type in config: {defect}")

    # ------------------------------------------------------------------ main
    def generate(self) -> pd.DataFrame:
        sc = self.cfg["generator"]["scenarios"]
        rows = (
            self.card_testing(sc["card_testing"])
            + self.velocity_burst(sc["velocity_burst"])
            + self.impossible_travel(sc["impossible_travel"])
            + self.round_amount_structuring(sc["round_amount_structuring"])
            + self.dormant_reactivation(sc["dormant_reactivation"])
            + self.dq_defects(self.cfg["generator"]["dq_defects"])
        )
        df = pd.DataFrame(rows, columns=["_source", "synthetic_tag", *RAW_COLUMNS])
        df.insert(1, "_source_row_num", np.arange(len(df)))
        return df
