-- TM-04  Card testing pattern
-- Risk:  stolen card numbers tested with tiny purchases before a large fraudulent one.
-- Logic: keep only small transactions, then count them per card in a rolling
--        {{window_minutes}}-minute window.
WITH small AS (
    SELECT trans_num, card_token, txn_ts, amount
    FROM gold.fact_transactions
    WHERE amount < {{small_amount}}
),
counted AS (
    SELECT *,
           COUNT(*) OVER (
               PARTITION BY card_token ORDER BY txn_ts
               RANGE BETWEEN INTERVAL {{window_minutes}} MINUTES PRECEDING AND CURRENT ROW
           ) AS small_txns_in_window
    FROM small
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-04' AS rule_id,
    printf('%d transactions under $%d within %d minutes',
           small_txns_in_window, {{small_amount}}, {{window_minutes}}) AS reason
FROM counted
WHERE small_txns_in_window >= {{min_small_txns}}
