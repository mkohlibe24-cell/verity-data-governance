-- TM-01  High value vs the cardholder's own baseline
-- Risk:  stolen card / account takeover used for unusually large purchases.
-- Logic: compare each transaction with the SAME card's previous {{lookback_days}} days.
--        EXCLUDE CURRENT ROW stops a large transaction from inflating its own baseline.
WITH history AS (
    SELECT
        trans_num, card_token, txn_ts, amount,
        COUNT(*)                     OVER w AS prior_txn_count,
        AVG(amount::DOUBLE)          OVER w AS prior_avg_amount,
        STDDEV_SAMP(amount::DOUBLE)  OVER w AS prior_std_amount
    FROM gold.fact_transactions
    WINDOW w AS (
        PARTITION BY card_token ORDER BY txn_ts
        RANGE BETWEEN INTERVAL {{lookback_days}} DAYS PRECEDING AND CURRENT ROW
        EXCLUDE CURRENT ROW
    )
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-01' AS rule_id,
    printf('Amount $%.2f vs 90-day average $%.2f (%.1f std devs above, %d prior txns)',
           amount::DOUBLE, prior_avg_amount,
           (amount::DOUBLE - prior_avg_amount) / NULLIF(prior_std_amount, 0), prior_txn_count) AS reason
FROM history
WHERE prior_txn_count >= {{min_history}}
  AND amount >= {{min_amount}}
  AND amount > prior_avg_amount + {{std_multiplier}} * COALESCE(prior_std_amount, 0)
