# Data Governance Framework

This document describes how Verity governs the card transaction data it processes: who owns
what, which standards apply, how quality is measured and controlled, and how issues are
escalated. It is written as a governance framework for a small data product. Everything
described here is implemented in the code; the file or table that provides the evidence is
named in each section.

## 1. Scope

| In scope | Evidence |
|---|---|
| Card transaction data from the source extract (Kaggle Sparkov simulation, CC0) plus tagged generator rows | `bronze.transactions_raw` |
| Bronze, silver and gold layers of the warehouse | `pipeline/ingest.py`, `dq_engine.py`, `gold.py` |
| Four gold data products: `fact_transactions`, `dim_customer`, `dim_merchant`, `monitoring.alerts` | `config/contracts/` |
| Data quality, reconciliation, lineage, catalog, contracts, issues, KRIs | `governance.*` tables |

## 2. Roles and responsibilities

| Role | Responsibility in Verity | Where it shows up |
|---|---|---|
| **Data Owner** (e.g. Card Transactions Data Owner) | Accountable for a data domain; approves quality tolerances; owns issues on their data | `owner` on every DQ rule and catalog column |
| **Data Steward** (Transaction, Customer, Merchant) | Day-to-day quality; reviews and approves suggested DQ rules; maintains business definitions | `config/steward_decisions.yaml`, `config/catalog_curated.yaml` |
| **Data Product Owner** | Publishes gold data products and keeps their data contracts | `config/contracts/*.yaml` |
| **Data Engineering Lead** (custodian) | Runs the pipeline, reconciliations and the run audit log | `governance.pipeline_runs`, `reconciliation_results` |
| **Control Owner** (e.g. Fraud Monitoring Control Owner) | Operates a control (monitoring rule, KRI) and acts on breaches | `config.yaml -> monitoring.rules, kris` |
| **Head of Data Governance** | Sets this framework, chairs issue and KRI reviews, owns PII controls | `GOV-01`, `GOV-04`, `GOV-05` in the control inventory |
| **2nd line (Risk / Compliance)** | Independently challenges controls, thresholds and the model | KRI definitions, model metrics, rule catalogue |
| **3rd line (Internal Audit)** | Tests that controls are designed and operating effectively | Run audit log, issue log, git history as evidence |

## 3. Three Lines of Defense

| Line | Who | What they do with Verity |
|---|---|---|
| **1st line: own and manage the risk** | Data owners, stewards, engineering, fraud operations | Run the pipeline and controls daily, fix issues, review alerts |
| **2nd line: oversee and challenge** | Data governance office, risk, compliance | Set standards and KRI thresholds, review breaches, challenge the ML model and rule tuning |
| **3rd line: independent assurance** | Internal audit | Sample controls and check the evidence: rule results, quarantine reasons, steward decisions, run logs |

Escalation path: control fails -> issue opened automatically with an owner and due date ->
owner remediates -> overdue or Red KRI -> Head of Data Governance -> Data Governance Council
(2nd line) -> senior management. Each KRI states its own escalation action (`reports/kri_report.md`).

## 4. Data standards

Standards adopted for this data product (several come directly from issues found in discovery):

| Standard | Reason |
|---|---|
| Identifiers and codes (card number, ZIP, IDs) are stored as **text**, never numbers | Numbers drop leading zeros: 116,389 ZIPs and 7 card numbers were damaged this way at source |
| Timestamps are ISO-8601 (`YYYY-MM-DD HH:MM:SS`) and there is one authoritative time field | The source `unix_time` field disagrees with the timestamp by about 7 years |
| Amounts are `DECIMAL(18,2)` in USD | Exact cents for reconciliation; no floating-point drift |
| Code sets (category, state, gender, fraud label) are validated against reference lists | Prevents free-text values entering analytics |
| Unknown values are `NULL`, never a default such as `1970-01-01` | Default dates look valid and corrupt time-based analysis |
| Merchants need a unique ID, not just a name | 7 merchant names appear under two categories, so a name alone is not a reliable key |

## 5. Metadata management

- **Data catalog** (`docs/data_catalog.md`, `governance.data_catalog`): technical metadata is read
  automatically from the warehouse; business metadata (definition, owner, sensitivity, CDE reason)
  is curated in `config/catalog_curated.yaml`. Columns without a curated definition are shown as
  "Not yet curated", so metadata completeness is measurable.
- **Critical Data Elements**: a column is a CDE if an error in it would directly change a
  regulatory or management report, a fraud decision, or a reconciliation total. 27 catalog entries
  are CDEs (for example `trans_num`, `amount`, `txn_ts`, `card_token`, `is_fraud`), and each has a
  written reason. CDE rules have the tightest tolerances, and their pass rate is KRI-01.
- **Lineage** (`docs/lineage.md`): generated from edges that each pipeline step records while it
  runs, so the diagram always matches the code. Column-level lineage is documented for personal data.

## 6. Data classification and privacy

| Class | Meaning | Examples | Where allowed |
|---|---|---|---|
| Restricted | PCI data or direct identifiers | card number, name, street | bronze and silver only (restricted access) |
| Confidential | Quasi-identifiers or pseudonymised data | card token, DOB, ZIP, home location | silver; generalised forms in gold |
| Internal | Business data without personal content | amount, category, merchant | all layers |

Controls: card numbers are replaced in gold by a **salted SHA-256 token** (the salt comes from an
environment variable, so tokens cannot be reversed by hashing every possible card number); names are
masked; date of birth is generalised to year and age band; street is removed (data minimisation);
ZIP is truncated to 3 digits; home coordinates are rounded to about 1 km. Distance from home is
computed **before** rounding, so monitoring keeps the signal without exposing the location.
Every data contract forbids raw PII columns, and rule DQ-G05 checks the masking on every run.

## 7. Data quality management

**Dimensions** (definitions used across the project):

| Dimension | Question it answers | Example rule |
|---|---|---|
| Completeness | Is the value there? | DQ-C03 amount populated |
| Validity | Is it in the allowed format / range / code set? | DQ-V10 card number passes Luhn |
| Accuracy | Does it reflect reality (plausibility)? | DQ-A01 cardholder at least 18 |
| Uniqueness | Is each real-world event recorded once? | DQ-U02 no double posting |
| Consistency | Does it agree with related data? | DQ-K02 one DOB per card |
| Timeliness | Is it current and inside the expected period? | DQ-T02 batch freshness |

**Rule lifecycle.** Rules live in `config/dq_rules.yaml` (38 rules) with an owner, tolerance,
severity and action. New rules come from two routes: authored by a steward, or **suggested
automatically** from the data profile and approved by a steward (human-in-the-loop). Suggestions
are never applied automatically. Each one shows its evidence and an impact preview (how many
current records it would fail). Every decision is stored with who, when and why in
`config/steward_decisions.yaml`, under version control.

**Preventive vs detective.** Rules with action `quarantine` are **preventive**: failing records
never reach silver or gold. They are moved to `silver.dq_quarantine` with reason codes and the full
raw record as evidence. Rules with action `flag` are **detective**: the record continues, carrying
the rule ID in `dq_flags`. Nothing is silently dropped, and reconciliation REC-03/04 proves it.

**Tolerance.** Each rule's `threshold_pct` is the failure rate the owner accepts. CDE completeness
and validity rules tolerate 0%. Format rules on non-critical fields tolerate up to 1%.

**Scorecards.** Pass rates by table, by CDE, by dimension and by month (`reports/dq_scorecard.md`).

## 8. Reconciliation

Eight controls prove that record counts and amount totals tie out from the source files (counted
independently with pandas, plus file checksums) to bronze, from bronze to silver + quarantine,
and from silver to gold (`pipeline/reconciliation.py`). Tolerances: 0 records and USD 0.01. Any break
opens an issue and drives KRI-03.

## 9. Data contracts

Each gold data product has a versioned contract (`config/contracts/`): owner, consumers,
classification, schema with types and required fields, unique keys, freshness, minimum volume,
forbidden PII columns and quality thresholds. Contracts are checked on every run
(`docs/data_contracts.md`). A breach opens an issue and drives KRI-05.

## 10. Issue management

Issues are opened automatically from failed DQ rules, reconciliation breaks, contract breaches and
Red KRIs (`governance.issue_log`). Each issue has a severity, an owner (the control owner), a root
cause, a remediation plan and a due date set by the severity SLA (critical 5 days, high 10, medium 30,
low 60). Re-runs update an open issue instead of duplicating it. When the control passes again, the
issue closes as "Closed - auto-verified". Root causes for genuine source-data issues use **5 Whys**
(`config/issue_rca.yaml`). Issues caused by generator-injected defects say so explicitly.

## 11. Key Risk Indicators

Eight KRIs (five data quality, three monitoring) with amber and red thresholds set **before** the
results were seen, plus owners and escalation actions (`config.yaml -> kris`, `reports/kri_report.md`).
A Red KRI opens an issue.

**Threshold review.** Thresholds are reviewed when they fail to flag a material change. On 11 Nov 2026,
KRI-06 (alert false-positive rate) was recalibrated from amber 90% / red 97% (generic rule-only
levels) to amber 82% / red 85%, using only Jan 2019 - Jun 2020, before the reporting period: amber is
about the 90th percentile of monthly rates and red is above the worst month on record. The old
thresholds kept an 11-point December 2020 jump Green. The reason is recorded in the KRI definition.
KRI-02 now excludes KRI-breach issues, so it never counts its own breach.

## 12. Change management and evidence

All rules, thresholds, contracts, curated metadata and steward decisions are configuration files
under git, so every change is attributable and reviewable. Every run is recorded in
`governance.pipeline_runs` (run ID, start/end, row counts per layer, rules passed/failed,
reconciliation breaks, alerts, open issues) and logged to `logs/<run_id>.log`.

## 13. Alignment with BCBS 239

BCBS 239 (the Basel Committee's *Principles for effective risk data aggregation and risk
reporting*) sets out how banks should govern the data behind risk reports. Verity maps to it as follows:

| Principle | How Verity addresses it |
|---|---|
| 1 Governance | Named owners and stewards, this framework, KRIs with escalation, issue management |
| 2 Data architecture and IT infrastructure | Medallion layers, data catalog, lineage, contracts |
| 3 Accuracy and integrity | 38 DQ rules, reconciliation to the cent, quarantine with evidence |
| 4 Completeness | Completeness rules, daily-volume check, record-count reconciliation |
| 5 Timeliness | Freshness and future/stale-date rules, freshness clauses in contracts |
| 6 Adaptability | Config-driven rules and thresholds; new rules via suggestion and approval |
| 7 Accuracy (of reports) | Reports and KRIs computed from reconciled gold data, never typed by hand |
| 8 Comprehensiveness | Scorecards across tables, CDEs and dimensions; monitoring and DQ KRIs together |
| 9 Clarity and usefulness | Plain-English rule names, reason codes, Excel report for management |
| 10 Frequency | Every control runs on every pipeline run; KRIs are reviewed monthly |
| 11 Distribution | Outputs are versioned in the repository; access to restricted layers is separated |
| 12-14 Supervisory review, remediation, cooperation | Supervisor-facing; the evidence trail (run log, issue log, git history) supports them |

## 14. Known limitations

- The source is a simulation, and the generator rows are synthetic (always tagged). Results show what
  the controls can do, not real-world fraud rates.
- The default hash salt is a public demo value; production would hold it in a secrets manager.
- Role-based access is described here, not enforced: the warehouse is a single DuckDB file.
  The Tier 2 API adds enforced roles for case actions.
- Several DQ rules fail because generator defects were injected on purpose. KRI-02 (open
  critical/high issues) is therefore Red by design, and the issue log states which issues come
  from injected defects.
