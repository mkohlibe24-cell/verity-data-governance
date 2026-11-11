# Resume Bullets

All numbers come from the final full pipeline run (`reports/results.json`). Two consecutive
full runs produce identical results.

## Project line

**Verity – Data Quality, Governance & Transaction Monitoring Platform** | Python, SQL, DuckDB, LightGBM, GitHub Actions
github.com/mkohlibe24-cell/verity-data-governance

## Bullets (pick 3-4)

- Built a config-driven **data quality engine** in Python and SQL (38 rules across 6 DQ dimensions)
  on **1.85M card transactions**, quarantining **1,630** failing records with reason codes and
  catching **100% of 1,960 injected defects**; 8 reconciliation controls tied every record and
  dollar from source files to the curated layer.
- **Automated data discovery and governance**: profiling flagged **12 PII columns** and **6 genuine
  source-data issues** (e.g. 116K ZIP codes missing leading zeros, a missing leap day),
  auto-generated **21 DQ rule suggestions** for data-steward approval, and produced a data catalog
  (127 columns, 27 Critical Data Elements), 4 data contracts, lineage and a 64-control inventory.
- Built a **fraud-monitoring data product** on PII-masked data: 8 SQL monitoring rules (window
  functions, CTEs) plus a LightGBM model with a time-based split and cost-based threshold; the ML
  layer caught **89% of fraud missed by rules alone**, raising recall from **63% to 95%** (PR-AUC 0.96).
- Designed **8 KRIs** with red/amber/green thresholds and an automated Excel **governance report**
  (DQ scorecard, issue log with 5-Whys root causes, control inventory), backed by 51 pytest tests
  and GitHub Actions CI.

## Shorter versions (if space is tight)

- Engineered a YAML-driven DQ rules engine (38 SQL rules) with quarantine and reconciliation on
  1.85M transactions; detected 100% of injected defects and 6 real source-data issues.
- Automated profiling, PII detection and rule suggestion with human-in-the-loop steward approval;
  built a data catalog, data contracts, lineage and KRI reporting.

## Where each number comes from

| Claim | `reports/results.json` field |
|---|---|
| 1.85M transactions | `data.source_rows` = 1,852,394 (`data.bronze_rows` = 1,855,568 incl. tagged rows) |
| 38 rules, 6 dimensions | `dq.rules_total`, `dq.rules_by_dimension` |
| 1,630 quarantined | `data.quarantined_rows` |
| 100% of 1,960 injected defects | `dq.injected_defects`, `dq.injected_defect_detection_pct` |
| 8 reconciliation controls, 0 breaks | `reconciliation.controls`, `reconciliation.breaks` |
| 12 PII columns, 6 source-data issues | `profiling.pii_columns`; `dq.source_data_failures_by_rule` (5) + DQ-C09 missing day |
| 21 rule suggestions | `rule_suggestions.proposed` |
| 127 columns, 27 CDEs, 4 contracts, 64 controls | `catalog.*`, `contracts.products`, `controls.total` |
| 89% of rule misses caught by ML | `ml.overlap_test["Original (Kaggle) fraud"].pct_of_rule_misses_caught_by_ml` = 89.17 |
| Recall 63% to 95%, PR-AUC 0.96 | `ml.comparison_test` rules recall 0.6343, hybrid recall 0.9477, LightGBM pr_auc 0.9562 |
| 8 KRIs, 51 tests | `kris` (8 entries); `pytest` output |

## How to talk about it honestly

- Say it uses a **public simulated dataset** (Kaggle, CC0) plus tagged synthetic scenarios. Never
  imply it is real bank data.
- The 89% figure is measured on **original** fraud only, excluding the injected scenarios.
- The simulator's fraud is easier to learn than real fraud, so real-world model scores would be lower.
  Saying this unprompted shows good judgement.
