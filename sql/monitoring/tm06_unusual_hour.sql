-- TM-06  Unusual-hour high spend
-- Risk:  card used at night when the genuine cardholder is less likely to be active.
-- Logic: night window ({{night_start}}:00 to {{night_end}}:00) AND a spend spike vs the card's
--        own 90-day average. The card's historical night-time share is shown in the reason.
WITH history AS (
    SELECT
        trans_num, card_token, txn_ts, amount, hour(txn_ts) AS txn_hour,
        COUNT(*)            OVER w AS prior_txn_count,
        AVG(amount::DOUBLE) OVER w AS prior_avg_amount,
        AVG(CASE WHEN hour(txn_ts) >= {{night_start}} OR hour(txn_ts) < {{night_end}} THEN 1.0 ELSE 0.0 END)
                            OVER w AS prior_night_share
    FROM gold.fact_transactions
    WINDOW w AS (
        PARTITION BY card_token ORDER BY txn_ts
        RANGE BETWEEN INTERVAL {{lookback_days}} DAYS PRECEDING AND CURRENT ROW
        EXCLUDE CURRENT ROW
    )
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-06' AS rule_id,
    printf('$%.2f at %02d:00, %.1fx the 90-day average; %.0f%% of past transactions were at night',
           amount::DOUBLE, txn_hour, amount::DOUBLE / NULLIF(prior_avg_amount, 0), 100 * prior_night_share) AS reason
FROM history
WHERE (txn_hour >= {{night_start}} OR txn_hour < {{night_end}})
  AND prior_txn_count >= {{min_history}}
  AND amount >= {{min_amount}}
  AND amount > {{multiplier}} * prior_avg_amount
