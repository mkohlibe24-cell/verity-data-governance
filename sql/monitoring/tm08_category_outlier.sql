-- TM-08  Merchant-category amount outlier (peer comparison)
-- Risk:  a purchase far above what is normal for that type of merchant.
-- Logic: the {{percentile}} quantile per category is computed ONLY on data before
--        {{baseline_end}}, so the threshold never learns from the evaluation period.
WITH baseline AS (
    SELECT category, quantile_cont(amount::DOUBLE, {{percentile}}) AS category_threshold
    FROM gold.fact_transactions
    WHERE txn_ts < TIMESTAMP '{{baseline_end}}'
    GROUP BY category
)
SELECT
    f.trans_num, f.card_token, f.txn_ts, f.amount,
    'TM-08' AS rule_id,
    printf('$%.2f is above the %s category threshold of $%.2f',
           f.amount::DOUBLE, f.category, b.category_threshold) AS reason
FROM gold.fact_transactions f
JOIN baseline b USING (category)
WHERE f.amount > b.category_threshold
  AND f.amount >= {{min_amount}}
