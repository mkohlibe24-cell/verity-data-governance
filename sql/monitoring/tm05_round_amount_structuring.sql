-- TM-05  Round-amount structuring
-- Risk:  value split into round amounts just under a $1,000 review threshold.
-- Logic: candidate = whole multiple of ${{round_unit}} between ${{lower}} and ${{upper}};
--        alert when the card has {{min_txns}}+ candidates within {{window_hours}} hours.
WITH candidates AS (
    SELECT trans_num, card_token, txn_ts, amount
    FROM gold.fact_transactions
    WHERE amount BETWEEN {{lower}} AND {{upper}}
      AND amount % {{round_unit}} = 0
),
counted AS (
    SELECT *,
           COUNT(*) OVER (
               PARTITION BY card_token ORDER BY txn_ts
               RANGE BETWEEN INTERVAL {{window_hours}} HOURS PRECEDING AND CURRENT ROW
           ) AS round_txns_in_window
    FROM candidates
)
SELECT
    trans_num, card_token, txn_ts, amount,
    'TM-05' AS rule_id,
    printf('%d round-amount transactions between $%d and $%.2f within %d hours',
           round_txns_in_window, {{lower}}, {{upper}}, {{window_hours}}) AS reason
FROM counted
WHERE round_txns_in_window >= {{min_txns}}
