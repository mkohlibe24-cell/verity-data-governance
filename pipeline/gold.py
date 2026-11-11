"""Step 5 - GOLD layer: curated, PII-protected star schema for data consumers.

PII treatment (documented in the data catalog):
  card number  -> card_token = SHA-256(salt + card number); plus a masked display value
                  (************1234). The salt comes from an environment variable so the raw
                  card number cannot be recovered by hashing a list of possible numbers.
  first/last   -> masked to the first letter (J****** B****)
  date of birth-> generalised to birth year and age band
  street       -> removed (not needed by any consumer: data minimisation)
  ZIP          -> truncated to 3 digits; home lat/long rounded to 2 decimals (~1 km)
Distance from home is computed from full-precision silver coordinates BEFORE they are
generalised, so monitoring keeps the signal without exposing the precise home location.
"""

from __future__ import annotations

import os

from pipeline.common import RunContext


def hash_salt(ctx: RunContext) -> str:
    sec = ctx.cfg["security"]
    salt = os.environ.get(sec["hash_salt_env_var"])
    if not salt:
        ctx.log.warning("%s not set: using the demo salt (fine for a demo, never for production)",
                        sec["hash_salt_env_var"])
        salt = sec["demo_hash_salt"]
    return salt


def build_gold(con, salt: str, as_of_year: int) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE gold.dim_customer AS
        SELECT
            sha256(? || card_number)                                   AS card_token,
            repeat('*', length(card_number) - 4) || right(card_number, 4) AS card_number_masked,
            right(card_number, 4)                                       AS card_last4,
            mode(left(first_name, 1) || repeat('*', greatest(length(first_name) - 1, 0))) AS first_name_masked,
            mode(left(last_name, 1) || repeat('*', greatest(length(last_name) - 1, 0)))   AS last_name_masked,
            mode(gender)                                                AS gender,
            mode(year(dob))                                             AS birth_year,
            mode(city)                                                  AS city,
            mode(state)                                                 AS state,
            mode(left(zip, 3))                                          AS zip3,
            round(mode(home_lat), 2)                                    AS home_lat_2dp,
            round(mode(home_long), 2)                                   AS home_long_2dp,
            mode(city_pop)                                              AS city_pop,
            min(txn_ts)                                                 AS first_txn_ts,
            max(txn_ts)                                                 AS last_txn_ts,
            count(*)                                                    AS txn_count,
            bool_or(is_synthetic)                                       AS has_synthetic_rows
        FROM silver.transactions
        GROUP BY card_number
        """,
        [salt],
    )
    con.execute(
        f"""
        ALTER TABLE gold.dim_customer ADD COLUMN age_band VARCHAR;
        UPDATE gold.dim_customer SET age_band = CASE
            WHEN {as_of_year} - birth_year < 18 THEN 'Under 18'
            WHEN {as_of_year} - birth_year < 25 THEN '18-24'
            WHEN {as_of_year} - birth_year < 35 THEN '25-34'
            WHEN {as_of_year} - birth_year < 50 THEN '35-49'
            WHEN {as_of_year} - birth_year < 65 THEN '50-64'
            ELSE '65+' END;
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE gold.dim_merchant AS
        SELECT left(md5(merchant_name), 12) AS merchant_key,
               merchant_name,
               mode(category)               AS primary_category,
               count(DISTINCT category)     AS category_count,
               count(*)                     AS txn_count
        FROM silver.transactions
        GROUP BY merchant_name
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE gold.fact_transactions AS
        SELECT
            s.trans_num,
            s.txn_ts,
            CAST(s.txn_ts AS DATE)                AS txn_date,
            strftime(s.txn_ts, '%Y-%m')           AS txn_month,
            sha256(? || s.card_number)            AS card_token,
            left(md5(s.merchant_name), 12)        AS merchant_key,
            s.category,
            s.amount,
            s.merch_lat,
            s.merch_long,
            round(haversine_km(s.home_lat, s.home_long, s.merch_lat, s.merch_long), 2) AS distance_from_home_km,
            s.is_fraud,
            s.is_synthetic,
            s.synthetic_tag,
            s._source                              AS source_system,
            len(s.dq_flags)                        AS dq_flag_count,
            s.bronze_row_id
        FROM silver.transactions s
        """,
        [salt],
    )


def run(ctx: RunContext) -> None:
    build_gold(ctx.con, hash_salt(ctx), ctx.as_of.year)
    for target, how in [
        ("gold.dim_customer", "One row per card; PAN hashed + masked, names masked, DOB -> birth year, street removed"),
        ("gold.dim_merchant", "One row per merchant name; primary category"),
        ("gold.fact_transactions", "Transaction grain; card token, merchant key, distance from home"),
    ]:
        ctx.add_lineage("silver.transactions", target, "gold", how)
    n = ctx.scalar("SELECT count(*) FROM gold.fact_transactions")
    ctx.metrics["gold_fact_rows"] = int(n)
    ctx.metrics["gold_customers"] = int(ctx.scalar("SELECT count(*) FROM gold.dim_customer"))
    ctx.metrics["gold_merchants"] = int(ctx.scalar("SELECT count(*) FROM gold.dim_merchant"))
    ctx.log.info("Gold: %s fact rows, %d customers, %d merchants", f"{n:,}",
                 ctx.metrics["gold_customers"], ctx.metrics["gold_merchants"])
