-- TM-02  Transaction velocity burst
-- Risk:  a compromised card being drained quickly before it is blocked.
-- Logic: count the card's transactions in the {{window_minutes}} minutes up to and
--        including this one (a time-based RANGE window, not a fixed number of rows).
WITH velocity AS (
    SELECT
        trans_num, card_token, txn_ts, amount,
        COUNT(*)    OVER w AS txns_in_window,
        SUM(amount) OVER w AS amount_in_window
    FROM gold.fact_transactions
    WINDOW w AS (
        PARTITION BY card_token ORDER BY txn_ts
        RANGE BETWEEN INTERVAL {{window_minutes}} MINUTES PRECEDING AND CURRENT ROW
    )
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-02' AS rule_id,
    printf('%d transactions totalling $%.2f within %d minutes',
           txns_in_window, amount_in_window::DOUBLE, {{window_minutes}}) AS reason
FROM velocity
WHERE txns_in_window >= {{min_txns}}
