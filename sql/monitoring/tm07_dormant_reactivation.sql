-- TM-07  Dormant account reactivation
-- Risk:  takeover of an inactive account the owner is not watching.
-- Logic: 1) gap since the previous transaction on the card (LAG);
--        2) carry forward when the card last "woke up" after {{dormant_days}}+ silent days;
--        3) alert on transactions of ${{min_amount}}+ within {{window_hours}} hours of waking up.
WITH gaps AS (
    SELECT trans_num, card_token, txn_ts, amount,
           date_diff('day', LAG(txn_ts) OVER (PARTITION BY card_token ORDER BY txn_ts, trans_num), txn_ts) AS days_since_prev
    FROM gold.fact_transactions
),
wakeups AS (
    SELECT *,
           MAX(CASE WHEN days_since_prev >= {{dormant_days}} THEN txn_ts END) OVER (
               PARTITION BY card_token ORDER BY txn_ts, trans_num ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS reactivated_at,
           MAX(CASE WHEN days_since_prev >= {{dormant_days}} THEN days_since_prev END) OVER (
               PARTITION BY card_token ORDER BY txn_ts, trans_num ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS dormant_days
    FROM gaps
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-07' AS rule_id,
    printf('$%.2f within %d hours of reactivation after %d dormant days',
           amount::DOUBLE, {{window_hours}}, dormant_days) AS reason
FROM wakeups
WHERE reactivated_at IS NOT NULL
  AND txn_ts <= reactivated_at + INTERVAL {{window_hours}} HOURS
  AND amount >= {{min_amount}}
