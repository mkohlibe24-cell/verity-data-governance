-- TM-03  Impossible travel
-- Risk:  card details used in two distant places by different people.
-- Logic: LAG() gets the previous transaction on the same card; haversine_km() gives the
--        distance; distance / time = implied speed. A minimum elapsed time of
--        {{min_hours}} h (1 minute) avoids dividing by zero for simultaneous transactions.
WITH ordered AS (
    SELECT
        trans_num, card_token, txn_ts, amount, merch_lat, merch_long,
        LAG(txn_ts)     OVER p AS prev_ts,
        LAG(merch_lat)  OVER p AS prev_lat,
        LAG(merch_long) OVER p AS prev_long
    FROM gold.fact_transactions
    WHERE merch_lat IS NOT NULL AND merch_long IS NOT NULL
    WINDOW p AS (PARTITION BY card_token ORDER BY txn_ts)
),
legs AS (
    SELECT *,
           haversine_km(prev_lat, prev_long, merch_lat, merch_long)               AS distance_km,
           GREATEST(date_diff('second', prev_ts, txn_ts) / 3600.0, {{min_hours}}) AS hours_elapsed
    FROM ordered
    WHERE prev_ts IS NOT NULL
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-03' AS rule_id,
    printf('%.0f km from previous transaction in %.0f minutes (implied %.0f km/h)',
           distance_km, hours_elapsed * 60, distance_km / hours_elapsed) AS reason
FROM legs
WHERE distance_km >= {{min_distance_km}}
  AND distance_km / hours_elapsed > {{max_speed_kmh}}
