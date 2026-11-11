# Interview Preparation

Model answers in my own voice for the American Express *ICS Governance & Control - Regulatory
Data Products & Data Offices* intern role. Numbers come from the final pipeline run
(`reports/results.json`). Don't memorise the answers word for word: understand them, then say them
in your own words.

---

## A. The project and the role

**1. Walk me through your project.**
"Verity is a data quality and governance platform for card transaction data, with transaction
monitoring built on top as one data product. I loaded 1.85 million card transactions into a
bronze-silver-gold warehouse in DuckDB. First I profiled the data to discover what it really
contains, including which columns hold personal data. Then a rules engine I built runs 38 data
quality checks defined in YAML. Records that fail critical checks are quarantined with a reason
code instead of being deleted, and reconciliation controls prove every record and every dollar ties
out from the source files to the final layer. The gold layer masks or hashes personal data, and
each data product has a data contract, catalog entries and auto-generated lineage. Every control
failure opens an issue with an owner and a due date, and eight KRIs with red/amber/green thresholds
summarise it all in an Excel report. On top of the governed data I built eight SQL monitoring rules
and a LightGBM model, and compared rules, ML and a hybrid. One command runs the whole thing, and
CI runs 42 tests on every push."

**2. Why lead with governance rather than fraud detection?**
"Because the role is about data products and data offices. A fraud model is only as good as the
data under it. I wanted to show the controls that make data trustworthy: quality rules, quarantine,
reconciliation, lineage, ownership and issue management. Then I show that a data product built on
that foundation works."

**3. How does this map to the team's work?**
"The job description mentions data discovery, ingestion, validation, reporting, documenting
standards, implementing and monitoring DQ controls, and automation. In Verity, discovery is the
profiler, ingestion is the bronze load with a source manifest, validation is the DQ engine, reporting
is the Excel governance report and KRIs, documentation is the catalog, contracts and
DATA_GOVERNANCE.md, and automation is the one-command pipeline plus automated rule suggestions."

## B. Data governance

**4. What is data governance, in your own words?**
"Deciding who is accountable for data, what 'good' looks like, and how you prove it, with evidence.
In practice that means owners and stewards, standards, metadata, quality controls, issue management
and reporting. In my project every rule and catalog column has an owner, and every run leaves an audit record."

**5. What is a Critical Data Element and how did you choose yours?**
"A CDE is a field where an error would directly change a report, a decision or a reconciliation.
I flagged 27 catalog entries as CDEs: things like transaction ID, amount, timestamp, the card token
and the fraud label. Each has a written reason. CDE rules get zero tolerance, and the CDE pass rate
is my first KRI."

**6. Data owner vs data steward?**
"The owner is accountable: they decide what quality level is acceptable and own the issues. The
steward does the day-to-day work: maintains definitions, reviews quality and, in my project,
approves or rejects the rules the system suggests."

**7. Why does a bank need a data catalog?**
"So people can find data, understand what it means and know how sensitive it is without asking
around. Mine combines technical metadata, read automatically from the warehouse, with curated
business metadata: definition, owner, sensitivity class and CDE reason. It covers 127 columns. 98.4%
have a curated definition; the rest are marked 'Not yet curated', so completeness is measurable."

**8. What is data lineage and how did you produce it?**
"Lineage shows where data came from and how it was transformed. Each pipeline step records its
inputs and outputs as it runs, and those edges become a Mermaid diagram. So the diagram always
matches the code. I also documented column-level lineage for personal data, for example card
number to salted SHA-256 token."

**9. What is a data contract?**
"An agreement between the producer of a data product and its consumers: schema, required fields,
unique keys, freshness, minimum volume, quality thresholds and forbidden columns. I have contracts
for four gold products, checked automatically every run. All 32 clauses passed in the final run."

**10. How did you handle personal data?**
"First I found it: the profiler flagged 12 columns as suspected PII, including a value-based check
that confirmed the card number column is Luhn-valid card numbers. Gold never contains raw PII. Card
numbers become a salted SHA-256 token, names are masked, date of birth becomes birth year and age
band, street is removed and ZIP is cut to three digits. I salt the hash because card numbers have a
limited format: without a salt, someone could hash every possible card number and reverse the
token. Distance from home is computed before the location is rounded, so monitoring keeps the
signal without exposing the address."

**11. What is BCBS 239?**
"The Basel Committee's 14 principles for risk data aggregation and reporting: governance,
architecture, accuracy and integrity, completeness, timeliness, adaptability, and the quality of
the reports themselves. My governance document maps each principle to a feature. For example,
accuracy and integrity are covered by the DQ rules and reconciliation, and completeness by the
completeness rules and record-count tie-outs."

**12. Three Lines of Defense: where does your project sit?**
"Running the pipeline and fixing data is first line. Setting standards, KRI thresholds and
challenging the model is second line, the data office and risk. Internal audit is third line: they
would test my controls using the evidence the project keeps, such as rule results, quarantine
reasons, steward decisions and run logs."

## C. Data quality

**13. Which DQ dimensions did you use?**
"Six: completeness (amount is populated), validity (category is one of 14 codes, card number
passes Luhn), accuracy (cardholder is plausibly an adult), uniqueness (no duplicate transaction IDs,
no double posting), consistency (one date of birth per card, unix_time agrees with the timestamp)
and timeliness (no future-dated records, the batch is fresh)."

**14. How did you set thresholds?**
"Each rule has a tolerance agreed with its owner. CDE completeness and validity rules tolerate
0%, because a missing amount is never acceptable. Format rules on non-critical fields tolerate
up to 1%. Severity drives the remediation SLA: 5 days for critical down to 60 for low."

**15. Why quarantine instead of deleting bad records?**
"If you delete records, totals stop reconciling, nobody can investigate, and the root cause never
gets fixed. Quarantined records keep their reason codes and the full raw record. In the final run
1,630 records were quarantined, and reconciliation proved bronze equals silver plus quarantine
exactly: 1,855,568 records and every cent."

**16. Preventive vs detective controls in your project?**
"Quarantine rules are preventive: bad records never reach gold. Steward approval is preventive and
manual. Flag rules, reconciliation, contract checks and the monitoring rules are detective. Issue
management is corrective. My control inventory lists 64 controls with type, frequency and owner."

**17. What real data quality issues did you find?**
"Six, in the original data, not the defects I injected:
- 116,389 ZIP codes had lost their leading zero, because ZIP was stored as a number.
- 7 cards (8,819 rows) had 11-digit card numbers, the same leading-zero problem. Interestingly, they
  still pass the Luhn check, because a leading zero doesn't change the checksum.
- The unix_time field is about 7 years behind the real timestamp on every row.
- 7 merchant names appear under two categories.
- 19,722 rows belong to cardholders under 18. That could be supplementary cards, so I flagged it
  rather than calling it an error.
- My daily-volume rule found that 29 February 2020 has zero transactions: the leap day is missing,
  probably because the source builds its calendar without leap-day handling.
Each one has a 5-Whys root cause in the issue log."

**18. How does automated rule suggestion work, and why human-in-the-loop?**
"The profiler knows what normal looks like, for example a column with zero nulls, or values
that all share one shape. From that it proposes rules: not-null, allowed values, ranges, formats,
uniqueness. In the final run it proposed 21. None goes live automatically: each one shows its
evidence and an impact preview of how many current records it would fail, and a steward approves
or rejects it with a reason. The decision is stored with who, when and why, under version control."

**19. Give an example of a suggestion you would reject.**
"The profiler suggested a range rule for merchant latitude, but the range it learned went up to
179.9. That came from corrupted records, so a rule learned from dirty data would approve dirty data.
It also suggested not-null on unix_time, a field we've decided not to use. That's why a human
must approve."

**20. What would you do if a reconciliation broke?**
"Stop publishing downstream, because the totals no longer agree. An issue opens automatically. I'd
trace the difference record by record using the bronze row ID, which every layer carries. Then
fix the transformation and re-run. All eight reconciliation controls passed in the final run, and I
have a unit test that deletes a gold row to prove the break is detected."

**21. Walk me through a 5-Whys.**
"ZIP fails the 5-digit rule. Why? The values have 4 digits. Why? The leading zero is missing. Why?
The extract writes ZIP as an integer. Why? The source schema defines it as numeric. Why? There's no
data standard saying codes must be text. So the real fix is a data standard. Padding in silver is
only an interim fix."

**22. KRI vs KPI? Which KRIs did you build?**
"A KPI measures performance; a KRI warns about risk. I have five data quality KRIs: CDE pass rate,
open critical/high issues, reconciliation breaks, quarantine rate and contract compliance. I have
three monitoring KRIs: alert false-positive rate, fraud detection rate and undetected fraud loss in
basis points. I set the thresholds before looking at results. KRI-02 is Red because I injected
defects on purpose, so 20 critical/high issues are open, and the issue log states which issues come
from injected test defects."

## D. SQL

**23. Window functions vs GROUP BY?**
"GROUP BY collapses rows into one per group. A window function keeps every row and adds a
calculation across related rows, such as a running count per card over the last 10 minutes.
Monitoring needs the per-transaction view, so most of my rules use windows."

**24. How do you find duplicates and keep one?**
"ROW_NUMBER over a partition of the duplicate key, ordered by something stable, then flag every row
where the number is greater than 1. That keeps the first occurrence and quarantines the rest
(query 2 below)."

**25. ROWS vs RANGE frames?**
"ROWS counts physical rows, like 'the previous 5 transactions'. RANGE works on the values in the
ORDER BY column, like 'everything in the previous 10 minutes'. For velocity rules RANGE is right,
because transactions don't arrive at fixed intervals."

## E. Monitoring and ML

**26. Why not just use accuracy?**
"Fraud is about 0.4% of test transactions, so a model that never flags anything is
99.57% accurate and catches nothing. I report precision, recall, F1 and PR-AUC instead."

**27. How did you choose the ML threshold?**
"By cost, on a validation month (June 2020) that the model didn't train on and that I didn't use
for testing. A false alarm costs analyst time, which I assumed at $15. A missed fraud costs its
amount. I picked the threshold with the lowest total cost, as long as it stays within 40 alerts
a day of analyst capacity, because too many alerts cause alert fatigue. The full trade-off curve
is saved for the threshold slider in the web app."

**28. What did rules vs ML vs hybrid show?**
"On the test period, July to December 2020 (525,909 transactions, 2,237 of them fraud):
- The 8 SQL rules alone caught 63.4% of fraud at 16.8% precision.
- LightGBM caught 93.2% at 80.5% precision, with a PR-AUC of 0.956.
- The hybrid, rules OR model, caught 94.8%, leaving USD 7,367 of fraud undetected versus USD 152,822
  with rules alone.

On the original Kaggle fraud only, so my injected scenarios don't flatter anything, the ML layer
caught 89.2% of the fraud the rules missed: 659 of 739 transactions. The rules still caught 22
original fraud cases the model missed. They also stay valuable for rare, well-understood patterns
like impossible travel and structuring, and they're fully transparent. Isolation Forest, which uses
no labels, only reached a PR-AUC of 0.360, which shows how valuable confirmed fraud outcomes are.
One honest caveat: this simulator's fraud is easy to learn, so real-world model numbers would be lower."

**29. How would you reduce false positives?**
"Tune the noisiest rules first: the rule performance table shows TM-01 and TM-06 produce most
false positives. Combine signals so one weak signal alone doesn't alert. Use the ML score to
prioritise, so P1 and P2 go first. And feed analyst outcomes back to retrain and re-tune. Every
change goes through the same approval and testing as any control change."

**30. How do you balance strong controls against customer experience?**
"Every false positive is a customer whose card might be declined or who gets a call. So I treat
thresholds as a cost trade-off, not 'catch everything'. I use priority tiers so the riskiest alerts
are handled first. I prefer step-up checks over hard declines where possible, and I track the
false-positive rate as a KRI so the trade-off stays visible to management."

**31. How did you avoid data leakage?**
"Time-based split: train before June 2020, validate on June, test on July to December. Every
feature uses only the card's past transactions; window frames end at the previous transaction. The
category baseline in rule TM-08 is computed only on pre-test data."

**32. Why did you remove 'card history length' from the model?**
"It was the top driver, but it just counts how many transactions the card has had. That grows with
time by construction, so its distribution shifts every month. It would always fail a stability check
(PSI) and it describes the card's age, not risky behaviour. Model risk management says features should
be stable and explainable, so I removed it."

**33. What does SHAP tell you?**
"How much each feature pushed a specific prediction up or down. Every ML alert carries its top three
reasons, such as 'spend in previous 24h' or 'amount vs the card's average'. An analyst can see why
it was flagged, and a model validator can check the model uses sensible signals."

## F. Automation, cloud, and reflection

**34. How is the process automated?**
"One command, `python pipeline/run_pipeline.py`, runs all 16 steps. Each step is timed and logged,
and the run is written to an audit table with row counts per layer, rules passed and failed,
reconciliation breaks and alerts. If a step fails, the run is still recorded as FAILED. Thresholds
live in config, and GitHub Actions runs lint, 42 tests and an end-to-end sample run on every push."

**35. How would you run this on Google Cloud?**
"Land files in Cloud Storage, load into BigQuery as bronze/silver/gold datasets, and run the same
SQL rules there. My DQ engine compiles rules to SQL, so they port easily. Dataplex could hold the
catalog, data quality scans and lineage. Cloud Composer or Workflows would orchestrate, Secret
Manager would hold the hashing salt, and Looker Studio or Power BI would do the reporting. Moving the
DQ and reconciliation checks to a BigQuery sandbox is my next step."

**36. Where can AI help in data governance?**
"Suggesting DQ rules from profiles, which I built, drafting catalog descriptions, classifying PII
and summarising issue trends. But always with a human approving, because governance needs
accountability. That's why my rule suggestions go to a steward queue."

**37. What was the hardest problem?**
"Reproducibility. Two identical runs gave different ML results. I traced it: LightGBM itself was
deterministic, but 84 card transactions shared the same second, so SQL window functions ordered
them arbitrarily, and floating-point sums varied. I added a tie-breaker column, exact decimal sums
and rounding, then proved two full runs produce identical metrics. For a control, reproducibility
matters: an auditor must get the same answer."

**38. What are the limitations?**
"The source is a simulation, and the injected scenarios and defects are synthetic. I tag them and
report original-data results separately so they don't inflate anything. The simulator's fraud is
fairly easy to learn, so the ML scores are higher than in real portfolios. Access control is
described but not enforced in the warehouse yet."

**39. Why maker-checker?**
"So no single person can both make and approve a sensitive change, which is segregation of duties.
In Verity, rule suggestions need a steward's approval before they go live, and the web app enforces
a second-person approval for closing high-priority alerts on the server, not just in the UI."

---

## SQL queries to know by heart

### Query 1 - Velocity rule (TM-02): time-based window
```sql
WITH velocity AS (
    SELECT trans_num, card_token, txn_ts, amount,
           COUNT(*) OVER w AS txns_in_window
    FROM gold.fact_transactions
    WINDOW w AS (
        PARTITION BY card_token
        ORDER BY txn_ts
        RANGE BETWEEN INTERVAL 10 MINUTES PRECEDING AND CURRENT ROW
    )
)
SELECT * FROM velocity WHERE txns_in_window >= 5;
```
- `WITH velocity AS (...)`: a CTE, a named step that keeps the query readable.
- `COUNT(*) OVER w`: a window function, so every transaction keeps its own row.
- `PARTITION BY card_token`: count separately for each card.
- `ORDER BY txn_ts`: look at each card's transactions in time order.
- `RANGE BETWEEN INTERVAL 10 MINUTES PRECEDING AND CURRENT ROW`: the frame is "everything in the
  10 minutes up to this transaction", based on time, not a row count.
- `WHERE txns_in_window >= 5`: alert from the 5th transaction in a burst onwards.

### Query 2 - Find duplicates and keep the first (DQ-U01)
```sql
SELECT record_id
FROM (
    SELECT bronze_row_id AS record_id,
           ROW_NUMBER() OVER (PARTITION BY trans_num ORDER BY bronze_row_id) AS rn
    FROM bronze.transactions_typed
    WHERE trans_num IS NOT NULL
)
WHERE rn > 1;
```
- `PARTITION BY trans_num`: group rows that share a transaction ID.
- `ORDER BY bronze_row_id`: the earliest-loaded copy gets number 1 (stable and repeatable).
- `ROW_NUMBER()`: numbers the copies 1, 2, 3...
- `WHERE trans_num IS NOT NULL`: nulls are a completeness problem, handled by another rule.
- `WHERE rn > 1`: every copy after the first fails the rule and is quarantined.

### Query 3 - Impossible travel (TM-03): LAG and distance
```sql
WITH ordered AS (
    SELECT trans_num, card_token, txn_ts, merch_lat, merch_long,
           LAG(txn_ts)     OVER p AS prev_ts,
           LAG(merch_lat)  OVER p AS prev_lat,
           LAG(merch_long) OVER p AS prev_long
    FROM gold.fact_transactions
    WINDOW p AS (PARTITION BY card_token ORDER BY txn_ts, trans_num)
),
legs AS (
    SELECT *,
           haversine_km(prev_lat, prev_long, merch_lat, merch_long) AS distance_km,
           GREATEST(date_diff('second', prev_ts, txn_ts) / 3600.0, 1 / 60.0) AS hours
    FROM ordered
    WHERE prev_ts IS NOT NULL
)
SELECT * FROM legs
WHERE distance_km >= 500 AND distance_km / hours > 800;
```
- `LAG(...) OVER p`: the previous transaction's time and location on the same card.
- `ORDER BY txn_ts, trans_num`: `trans_num` breaks ties when two transactions share a second.
- `haversine_km(...)`: a macro for the great-circle distance between two lat/long points.
- `GREATEST(..., 1/60.0)`: treat gaps under one minute as one minute, to avoid dividing by zero.
- `WHERE prev_ts IS NOT NULL`: the first transaction has no previous leg.
- `distance_km >= 500`: ignores geocode noise (merchants sit up to ~150 km from home in this data).
- `distance_km / hours > 800`: faster than an airliner.

### Query 4 - Reconciliation: bronze = silver + quarantine
```sql
SELECT
    (SELECT count(*) FROM bronze.transactions_raw)                          AS bronze_records,
    (SELECT count(*) FROM silver.transactions)
      + (SELECT count(*) FROM silver.dq_quarantine)                         AS silver_plus_quarantine,
    (SELECT sum(TRY_CAST(amt AS DECIMAL(18, 2))) FROM bronze.transactions_raw) AS bronze_amount,
    (SELECT sum(amount) FROM silver.transactions)
      + (SELECT sum(amount) FROM silver.dq_quarantine)                      AS silver_plus_quarantine_amount;
```
- Each scalar subquery in brackets returns one number.
- Counts: every bronze record must land in exactly one place, silver or quarantine.
- `TRY_CAST(... AS DECIMAL(18, 2))`: bronze is text; unparseable amounts become NULL instead of
  crashing the query, and `DECIMAL` keeps exact cents.
- If the two counts or the two totals differ, data was lost or duplicated, and the control reports BREAK.

### Query 5 - DQ scorecard by dimension
```sql
SELECT dimension,
       count(*)                                  AS rules,
       count(*) FILTER (WHERE status = 'PASS')   AS rules_passed,
       sum(records_failed)                       AS records_failed,
       round(100.0 * (1 - sum(records_failed) / sum(records_checked)), 4) AS pass_rate_pct
FROM governance.dq_rule_results
WHERE run_id = 'RUN-...'
GROUP BY dimension
ORDER BY dimension;
```
- `GROUP BY dimension`: one row per DQ dimension.
- `count(*) FILTER (WHERE ...)`: a conditional count, neater than `SUM(CASE WHEN ...)`.
- The pass rate is record-weighted: failed records over records checked across the dimension's rules.
- `WHERE run_id = ...`: results are kept for every run, so you can compare runs over time.
