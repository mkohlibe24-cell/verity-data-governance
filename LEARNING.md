# Learning Guide

Every concept used in Verity, explained simply, with an example from this project. Read it
top to bottom once, then use it as a reference before interviews.

---

## Part 1 - Data management and governance

### Medallion architecture (bronze, silver, gold)
Data is refined in layers, like ore into metal:
- **Bronze** = exactly what arrived. Every column is text, nothing is fixed or dropped. If something
  goes wrong later, you can always go back to the original. (`bronze.transactions_raw`)
- **Silver** = validated and standardised. Types are correct, bad records are quarantined, and known
  format problems are fixed (ZIP codes padded back to 5 digits). (`silver.transactions`)
- **Gold** = ready for consumers. Modelled as facts and dimensions, with personal data masked.
  (`gold.fact_transactions`, `gold.dim_customer`, `gold.dim_merchant`)

### Data discovery and profiling
Before writing rules you must know what the data looks like. Profiling measures each column:
% nulls, number of distinct values, min/max, percentiles, outliers, and the "shape" of values.
Example: profiling showed the ZIP column's dominant shape is `99999` (5 digits) but 116,389 values
are 4 digits, which revealed that leading zeros had been lost upstream. (`reports/data_profile.md`)

### Data quality dimensions
Six questions you ask of any data:

| Dimension | Question | Example in Verity |
|---|---|---|
| Completeness | Is it there? | Amount must not be empty |
| Validity | Is it allowed? | Category must be one of 14 codes |
| Accuracy | Is it true / plausible? | Cardholder should be 18 or older |
| Uniqueness | Is it recorded once? | No two transactions with the same ID |
| Consistency | Does it agree with related data? | One date of birth per card |
| Timeliness | Is it current? | No transactions dated in the future |

### Critical Data Elements (CDEs)
Not all columns matter equally. A CDE is a column where an error would change a report, a decision
or a reconciliation, such as `amount`, `trans_num` and `is_fraud`. CDEs get the strictest rules
(0% tolerance), and their pass rate is a KRI.

### Quarantine vs drop
Never silently delete bad data. If you drop it, nobody knows it was there, totals stop matching, and
the problem never gets fixed at source. Verity moves failing records to `silver.dq_quarantine`
with a reason code (e.g. `NON_POSITIVE_AMOUNT`) and the full raw record, so they can be
investigated, corrected and re-processed.

### Preventive vs detective controls
- **Preventive** stops a problem before it happens: quarantine rules stop bad records reaching gold;
  steward approval stops an unreviewed rule going live.
- **Detective** finds a problem after it happens: flag rules, reconciliation, monitoring rules.
- **Corrective** fixes it afterwards: issue management and remediation.
- **Automated** vs **manual**: whether a system or a person performs the control.

### Reconciliation
Proving that nothing was lost or duplicated between two points, by comparing counts and totals.
Example: bronze has N records, so silver + quarantine must also have N, and the amount totals
must match to the cent. Verity also counts the source files with a different tool (pandas) so the
check is independent of the database being checked.

### Data catalog and metadata
Metadata is "data about data". Technical metadata (column name, type) can be read automatically.
Business metadata (what the column means, who owns it, how sensitive it is) needs a human.
The catalog combines both, so anyone can find and understand data without asking around.

### Data lineage
Where a piece of data came from and what happened to it on the way. Example:
`cc_num` (bronze) -> `card_number` (silver) -> `card_token` (gold, SHA-256 hashed). Lineage lets you
answer "if this source breaks, which reports are affected?" Verity builds lineage from the steps
the pipeline actually ran, so the diagram can never go out of date.

### Data contracts
A written agreement between the team producing a data product and the teams using it: the schema,
required fields, freshness, minimum volume, quality thresholds, and which columns must never appear
(raw PII). The contract is checked automatically on every run, so a breaking change is caught before
consumers see it.

### PII, pseudonymisation and hashing with salt
- **PII** = personally identifiable information (name, address, date of birth, card number).
- **Masking** hides part of a value: `************1111`.
- **Generalisation** makes it less precise: date of birth -> birth year.
- **Hashing** turns a value into a fixed-length fingerprint (SHA-256) that cannot be reversed. Card
  numbers have a limited format, though, so an attacker could hash every possible number and
  compare. A **salt** (a secret value added before hashing) stops that. The same card always gets
  the same token, so analytics still work. This is **pseudonymisation**: the data can still be
  linked, but not read.

### Root cause analysis: 5 Whys
Keep asking "why?" until you reach a cause you can actually fix. Example from this project:
ZIP fails the format rule -> it has 4 digits -> the leading zero is missing -> the extract stores
ZIP as an integer -> the source schema defines it as numeric -> there is no standard saying codes
must be text. The fix is a data standard, not just padding the value. (`reports/issue_log.md`)

### KRI vs KPI
- A **KPI** (Key Performance Indicator) measures how well you are doing: "alerts reviewed per day".
- A **KRI** (Key Risk Indicator) measures how much risk you are carrying, as an early warning:
  "% of alerts breaching SLA", "CDE pass rate".
- **RAG** = Red / Amber / Green against thresholds agreed in advance. Each KRI has an owner and an
  escalation action for when it turns red.

### Three Lines of Defense
- **1st line** owns and manages the risk day to day (data owners, operations).
- **2nd line** sets policy and independently challenges (risk, compliance, data governance office).
- **3rd line** gives independent assurance to the board (internal audit).

### BCBS 239
The Basel Committee's 14 principles for how banks should aggregate risk data and report it:
governance, architecture, accuracy, completeness, timeliness, adaptability, and the quality of the
reports themselves. See `DATA_GOVERNANCE.md` section 13 for how Verity maps to each.

### Design vs operating effectiveness (control testing)
- **Design effectiveness**: if the control works as designed, would it address the risk?
- **Operating effectiveness**: did it actually work, consistently, over the period? This is tested
  by sampling (e.g. pick 25 closed alerts and check each followed the procedure).

### Maker-checker and segregation of duties
One person makes a change (maker) and a different person approves it (checker). Segregation of
duties means no single person can both do and approve a sensitive action. In Verity, a steward must
approve a suggested rule before it goes live; the Tier 2 app enforces maker-checker on closing
high-priority alerts.

---

## Part 2 - SQL concepts

### CTEs (`WITH ... AS`)
A named temporary result you can build on step by step, which makes long queries readable:
```sql
WITH small AS (SELECT * FROM gold.fact_transactions WHERE amount < 5)
SELECT card_token, count(*) FROM small GROUP BY card_token;
```

### Window functions
They calculate across related rows **without collapsing them** (unlike `GROUP BY`).
```sql
-- For every transaction: how many transactions did this card make in the 10 minutes up to it?
COUNT(*) OVER (
    PARTITION BY card_token          -- restart the calculation for each card
    ORDER BY txn_ts                  -- in time order
    RANGE BETWEEN INTERVAL 10 MINUTES PRECEDING AND CURRENT ROW   -- time-based frame
)
```
- `ROWS` frames count rows ("the previous 3 rows"); `RANGE` frames use values ("the previous 10 minutes").
- `LAG(x)` gives the previous row's value (used for impossible travel and dormancy gaps).
- `ROW_NUMBER()` numbers rows within a group (used to keep the first copy of a duplicate).
- `EXCLUDE CURRENT ROW` keeps a transaction out of its own baseline, so a huge purchase
  cannot raise the average it is compared against.

### Anti-join / NOT EXISTS
"Rows in A with no match in B", used for referential integrity (every fact row must have a customer)
and to build silver as "bronze minus quarantine".

---

## Part 3 - Machine learning concepts

### Class imbalance and why accuracy misleads
Only about 0.4-0.5% of transactions are fraud. A model that says "not fraud" every time is
99.5% accurate and completely useless. So we use:
- **Precision** = of the transactions we flagged, how many were really fraud? (Low precision =
  analysts waste time on false alarms.)
- **Recall** = of all real fraud, how much did we catch? (Low recall = losses.)
- **F1** = the balance (harmonic mean) of the two.
- **PR-AUC** = average precision across all thresholds; a summary of the precision-recall
  curve. The no-skill baseline equals the fraud rate (about 0.004), so 0.95 is very strong.

### Time-based split and data leakage
Fraud models are used on future data, so they must be tested on future data. Train on transactions
before June 2020, choose the threshold on June 2020, test on July-December 2020. A random split would
let the model see "the future" during training and overstate its performance. All features also use
only each card's **past** transactions.

### Isolation Forest (unsupervised)
Randomly splits the data again and again. Unusual points get isolated in fewer splits, so a short
path means an anomaly. It needs no fraud labels, which is useful when labels are scarce, but it finds
"unusual", not "fraud". In this project its PR-AUC is far below LightGBM's, which shows the value of labels.

### Gradient boosting (LightGBM)
Builds many small decision trees one after another, each correcting the errors of the previous ones.
Strong on tabular data. `scale_pos_weight` makes the rare fraud class count more during training.

### SHAP explanations
For each prediction, SHAP splits the score into contributions from each feature: "flagged mainly
because the amount is 12x the card's average (+2.1) and it was at 23:00 (+0.8)". LightGBM computes
these natively (TreeSHAP via `pred_contrib`). They give each alert a plain-English reason, which
analysts and model validators need.

### Choosing the threshold with cost (and alert fatigue)
The model outputs a probability, and the threshold turns it into "alert / no alert".
- A missed fraud costs its amount (the loss).
- A false alarm costs analyst time (we assume $15 per review).
We pick the threshold with the lowest total cost on the validation month, subject to a capacity
limit (40 alerts a day), because too many alerts cause **alert fatigue** and real fraud gets missed
in the noise. The full trade-off curve is saved in `reports/ml_threshold_curve_*.csv`.

### Model stability and PSI
Population Stability Index measures how much a feature's distribution has shifted between two
periods (rule of thumb: below 0.1 stable, 0.1-0.25 watch, above 0.25 investigate). Example from
this project: "card history length" was the model's top driver, but it grows with time by
construction, so it would always drift. It was removed from the features.

### Rules vs ML vs hybrid
Rules are transparent and catch known patterns (impossible travel, structuring) even when they are
rare. ML catches subtle combinations no one wrote a rule for. A hybrid uses both. In this project
the hybrid catches more fraud than either alone. See `reports/monitoring_results.md`.

---

## Part 4 - How the code is organised

```
config/           every threshold, rule, contract and curated definition (no magic numbers in code)
pipeline/         one Python module per step, run in order by run_pipeline.py
  common.py       config, DuckDB connection, logging, SQL macros (Luhn, haversine)
  ingest.py       step 1   bronze load + source manifest
  generate_data.py         tagged synthetic scenarios and defects
  profiling.py    step 2   data discovery + PII scan
  rule_suggester.py step 3 rules suggested from the profile (pending steward approval)
  dq_engine.py    steps 4, 6  YAML rules -> SQL, quarantine, silver, scorecard
  gold.py         step 5   masked star schema
  reconciliation.py step 7
  monitoring.py   steps 8, 10  SQL rules -> alerts
  ml.py           step 9   features, LightGBM, Isolation Forest, comparison, SHAP
  catalog.py      steps 11, 12  contracts, catalog, lineage, control inventory
  issues.py       steps 13, 15  issue log
  kri.py          step 14
  governance_outputs.py step 16  Excel, markdown, results.json
  steward.py      CLI to approve/reject suggested rules
sql/monitoring/   the 8 monitoring rules as .sql files
tests/            pytest: small hand-made datasets with known answers
reports/, docs/   generated outputs (never edited by hand)
```

**How a run works:** `python pipeline/run_pipeline.py` creates a run ID, then calls each step with a
shared `RunContext` (config + DB connection + logger). Each step writes tables to DuckDB and records
lineage edges. At the end, the run is written to `governance.pipeline_runs` and all reports are
regenerated from the database.

**Tier 2 (planned):** a FastAPI service will read the same DuckDB tables and expose them as typed REST
endpoints (with roles enforced on the server). A React app will call that API for the
governance, issues and monitoring screens.
