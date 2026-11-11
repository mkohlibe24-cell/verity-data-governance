# PLAN: Verity – Data Quality, Governance & Transaction Monitoring Platform

**Positioning.** Data governance and data quality are the headline. Transaction monitoring is
one data product built on top of well-governed data. Designed around the American Express
"ICS Governance & Control - Regulatory Data Products & Data Offices" intern role.

## Architecture (medallion layers in DuckDB)

```
Kaggle CSVs (CC0) + generator rows (tagged)
  -> BRONZE   raw, as landed, every column VARCHAR, row-level source tags
  -> profiling (data discovery) -> automated DQ rule suggestions -> steward approval queue
  -> DQ engine (YAML rules) -> quarantine (reason codes) | SILVER (typed, standardised)
  -> GOLD     PII masked/hashed star schema (fact_transactions, dim_customer, dim_merchant)
  -> reconciliation (raw -> bronze -> silver -> gold), data contracts, catalog, lineage
  -> monitoring data product: SQL rules + ML score -> alerts -> KRIs -> Excel report
```

## Folder structure

`config/` (config.yaml, dq_rules.yaml, catalog_curated.yaml, contracts/, steward_decisions.yaml) ·
`pipeline/` (Python steps, one module each) · `sql/monitoring/` (TM rules as .sql files) ·
`tests/` · `reports/` (generated outputs) · `docs/` (generated catalog, lineage, controls) ·
`data/` (raw is git-ignored, a small CC0 sample is committed) · `warehouse/` (DuckDB, git-ignored)

## Tiers

| Tier | Scope | Target |
|---|---|---|
| 1 | Ingestion + profiling, DQ engine (20+ rules) + quarantine + scorecard + rule suggestions, gold + masking, reconciliation, catalog, contracts, lineage, control inventory, issue log, run audit log, 8 TM rules, Isolation Forest + LightGBM, 8 KRIs, Excel report, tests, CI, docs | Done 11 Nov (run RUN-20261111-164200) |
| 2 | FastAPI + React app: Overview, Data Governance, Issues, Monitoring (maker-checker), Reports | 10 PM, 11 Nov |
| 3 | BigQuery sandbox, Power BI kit, PowerPoint, control testing workpapers + RCM, model card, Playwright E2E, Vercel + Render | after applying |

## Decisions and defaults

- Project folder renamed to `C:\Users\HP\Desktop\verity-data-governance` to match the repo name (shortcut updated).
- Python 3.13 venv (3.12 fallback if a library breaks). Amounts in USD. Batch as-of date
  2020-12-31 23:59:59, report month December 2020.
- Card numbers are hashed with salted SHA-256 (salt from the `VERITY_HASH_SALT` environment
  variable, demo default otherwise). Names masked, DOB generalised to birth year, street removed.
- Generator rows are tagged (`is_synthetic`, `synthetic_tag`). Injected risk scenarios are
  labelled `is_fraud = 1` for evaluation. Headline ML results are reported on the original
  Kaggle fraud separately, so injected scenarios don't inflate the results.
- ML: train before 2020-06-01, choose the threshold on June 2020, test on Jul-Dec 2020.
- No SHAP package: LightGBM's built-in TreeSHAP (`pred_contrib`) gives the same per-alert reasons.
- KRI thresholds were set before looking at results.
