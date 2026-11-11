"""Step 2 - Data discovery: automated profiling of the bronze data.

For every column: type, null %, distinct count, min/max, percentiles, outliers (3 x IQR
fences), the dominant text pattern, top values and suspected PII. Results go to
governance.profile_columns, reports/data_profile.json and a readable reports/data_profile.md.

The profile also feeds the automated DQ rule suggester (rule_suggester.py).
"""

from __future__ import annotations

import json

import pandas as pd

from pipeline.common import RunContext, resolve

RELATION = "bronze.transactions_typed"

# Business columns to profile (typed names from the bronze view)
PROFILE_COLUMNS = [
    "trans_num", "cc_num", "merchant", "category", "txn_ts_raw", "txn_ts", "amount", "first_name",
    "last_name", "gender", "street", "city", "state", "zip", "home_lat", "home_long", "city_pop", "job",
    "dob_raw", "dob", "unix_time", "merch_lat", "merch_long", "is_fraud_raw",
]
NUMERIC_TYPES = ("DECIMAL", "DOUBLE", "BIGINT", "INTEGER", "FLOAT")

# Suspected PII by column name. Value-based checks below confirm card numbers.
PII_BY_NAME = {
    "cc_num": "PCI - primary account number (card number)",
    "first_name": "Direct identifier - name",
    "last_name": "Direct identifier - name",
    "street": "Direct identifier - street address",
    "dob_raw": "Quasi-identifier - date of birth",
    "dob": "Quasi-identifier - date of birth",
    "zip": "Quasi-identifier - location",
    "city": "Quasi-identifier - location",
    "home_lat": "Quasi-identifier - precise home location",
    "home_long": "Quasi-identifier - precise home location",
    "gender": "Quasi-identifier - demographic",
    "job": "Quasi-identifier - demographic",
}


def _pattern_sql(col: str) -> str:
    """Generalise a value into a shape: digits -> 9, letters -> A (e.g. '28654' -> '99999')."""
    return f"regexp_replace(regexp_replace(CAST({col} AS VARCHAR), '[0-9]', '9', 'g'), '[A-Za-z]', 'A', 'g')"


def profile_columns(con, relation: str = RELATION, columns: list[str] | None = None) -> pd.DataFrame:
    columns = columns or PROFILE_COLUMNS
    total = con.execute(f"SELECT count(*) FROM {relation}").fetchall()[0][0]
    types = dict(con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ?", relation.split(".")).fetchall())
    rows = []
    for col in columns:
        dtype = types[col]
        q = f'"{col}"'
        nulls, distinct, mn, mx = con.execute(
            f"SELECT count(*) FILTER (WHERE {q} IS NULL), count(DISTINCT {q}), "
            f"CAST(min({q}) AS VARCHAR), CAST(max({q}) AS VARCHAR) FROM {relation}").fetchall()[0]
        rec = {
            "column": col, "data_type": dtype, "rows": total, "null_count": nulls,
            "null_pct": round(100 * nulls / total, 4) if total else 0.0, "distinct_count": distinct,
            "min": mn, "max": mx, "mean": None, "std": None, "percentiles": None, "outlier_count": None,
            "min_length": None, "max_length": None, "top_pattern": None, "top_pattern_share": None,
            "top_values": None, "suspected_pii": PII_BY_NAME.get(col),
        }
        if dtype.startswith(NUMERIC_TYPES):
            mean, std, pct = con.execute(
                f"SELECT avg({q}), stddev_samp({q}), "
                f"quantile_cont({q}, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]) FROM {relation}").fetchall()[0]
            rec.update(mean=round(float(mean), 4) if mean is not None else None,
                       std=round(float(std), 4) if std is not None else None)
            if pct:
                p = [float(v) for v in pct]
                rec["percentiles"] = dict(zip(["p01", "p05", "p25", "p50", "p75", "p95", "p99"],
                                              [round(v, 4) for v in p], strict=True))
                iqr = p[4] - p[2]
                lo, hi = p[2] - 3 * iqr, p[4] + 3 * iqr
                rec["outlier_count"] = con.execute(
                    f"SELECT count(*) FROM {relation} WHERE {q} < {lo} OR {q} > {hi}").fetchall()[0][0]
        elif dtype in ("VARCHAR",):
            lmin, lmax = con.execute(f"SELECT min(length({q})), max(length({q})) FROM {relation}").fetchall()[0]
            rec.update(min_length=lmin, max_length=lmax)
            pat = con.execute(
                f"SELECT {_pattern_sql(q)} AS p, count(*) AS n FROM {relation} WHERE {q} IS NOT NULL "
                f"GROUP BY 1 ORDER BY n DESC LIMIT 1").fetchall()
            pat = pat[0] if pat else None
            if pat:
                non_null = total - nulls
                rec.update(top_pattern=pat[0], top_pattern_share=round(pat[1] / non_null, 6) if non_null else None)
            # Value-based PII detection: mostly Luhn-valid 12-19 digit strings = card numbers
            luhn_share = con.execute(
                f"SELECT avg(CASE WHEN regexp_full_match({q}, '[0-9]{{11,19}}') AND luhn_valid({q}) "
                f"THEN 1.0 ELSE 0.0 END) FROM {relation} WHERE {q} IS NOT NULL").fetchall()[0][0]
            if luhn_share and luhn_share > 0.5:
                rec["suspected_pii"] = (f"PCI - primary account number (value check: {luhn_share:.1%} "
                                        "of values are Luhn-valid card numbers)")
        if distinct <= 60:
            tv = con.execute(
                f"SELECT CAST({q} AS VARCHAR) AS v, count(*) AS n FROM {relation} "
                f"GROUP BY 1 ORDER BY n DESC LIMIT 60").fetchall()
            rec["top_values"] = [{"value": v, "count": n} for v, n in tv]
        rows.append(rec)
    return pd.DataFrame(rows)


def discovery_findings(con) -> list[dict]:
    """Targeted discovery queries: facts about the SOURCE data worth telling the data owner."""
    f = []
    total = con.execute("SELECT count(*) FROM bronze.transactions_typed WHERE NOT is_synthetic").fetchall()[0][0]

    def add(title, value, detail):
        f.append({"finding": title, "value": value, "detail": detail})

    n = con.execute("SELECT count(*) FROM bronze.transactions_typed WHERE NOT is_synthetic AND length(zip) < 5").fetchall()[0][0]
    add("ZIP codes missing leading zero", n,
        f"{n:,} of {total:,} source rows ({n / total:.1%}) have ZIPs shorter than 5 digits: stored as numbers upstream.")
    off = con.execute(
        "SELECT median(epoch(txn_ts) - unix_time) / 86400.0 FROM bronze.transactions_typed "
        "WHERE NOT is_synthetic AND txn_ts IS NOT NULL AND unix_time IS NOT NULL").fetchall()[0][0]
    add("unix_time offset from timestamp", round(off, 1),
        f"unix_time is consistently about {off:,.0f} days (about {off / 365.25:.1f} years) behind trans_date_trans_time.")
    pref = con.execute(
        "SELECT avg(CASE WHEN merchant LIKE 'fraud\\_%' ESCAPE '\\' THEN 1.0 ELSE 0.0 END) "
        "FROM bronze.transactions_typed WHERE NOT is_synthetic").fetchall()[0][0]
    add("Merchant names prefixed 'fraud_'", round(100 * pref, 2),
        f"{pref:.1%} of merchant names start with 'fraud_' (a simulator artefact, not a fraud label). Stripped in silver.")
    short = con.execute(
        "SELECT count(DISTINCT cc_num), count(*) FROM bronze.transactions_typed WHERE NOT is_synthetic AND length(cc_num) < 12"
    ).fetchall()[0]
    add("Card numbers shorter than 12 digits", short[0],
        f"{short[0]} cards ({short[1]:,} rows) have 11-digit card numbers, likely a leading zero lost upstream.")
    minors = con.execute(
        "SELECT count(DISTINCT cc_num), count(*) FROM bronze.transactions_typed WHERE NOT is_synthetic "
        "AND date_diff('year', dob, CAST(txn_ts AS DATE)) < 18").fetchall()[0]
    add("Cardholders under 18 at transaction", minors[0],
        f"{minors[0]} cards ({minors[1]:,} rows) belong to cardholders under 18: supplementary cards or DOB errors.")
    multi = con.execute(
        "SELECT count(*) FROM (SELECT merchant FROM bronze.transactions_typed WHERE NOT is_synthetic "
        "GROUP BY merchant HAVING count(DISTINCT category) > 1)").fetchall()[0][0]
    add("Merchants mapped to more than one category", multi,
        f"{multi} merchant names appear under two categories: names are not unique keys.")
    gap = con.execute(
        "SELECT max(g) FROM (SELECT date_diff('day', lag(txn_ts) OVER (PARTITION BY cc_num ORDER BY txn_ts), txn_ts) g "
        "FROM bronze.transactions_typed WHERE NOT is_synthetic)").fetchall()[0][0]
    add("Longest gap between a card's transactions (days)", gap,
        f"No card is silent for more than {gap} days, so the source has no dormant accounts (generator adds them).")
    dist = con.execute(
        "SELECT max(haversine_km(home_lat, home_long, merch_lat, merch_long)) FROM bronze.transactions_typed WHERE NOT is_synthetic"
    ).fetchall()[0][0]
    add("Max merchant distance from home (km)", round(dist, 1),
        f"Merchants are always within {dist:,.0f} km of home, so impossible travel cannot occur in the source.")
    return f


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def write_markdown(ctx: RunContext, prof: pd.DataFrame, findings: list[dict], summary: dict) -> None:
    lines = [
        "# Data Profile - bronze.transactions (data discovery)",
        "",
        f"Generated by `pipeline/profiling.py` on run `{ctx.run_id}` ({ctx.mode} mode).",
        "",
        "## Dataset summary",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Records profiled | {summary['rows']:,} |",
        f"| Of which original source (Kaggle) | {summary['source_rows']:,} |",
        f"| Of which generator-tagged | {summary['synthetic_rows']:,} |",
        f"| Columns profiled | {len(prof)} |",
        f"| Columns with suspected PII | {int(prof['suspected_pii'].notna().sum())} |",
        f"| Date range (parsed timestamps) | {summary['min_ts']} to {summary['max_ts']} |",
        "",
        "## Discovery findings (source data, generator rows excluded)",
        "",
        "| Finding | Value | Detail |", "|---|---|---|",
    ]
    lines += [f"| {x['finding']} | {_fmt(x['value'])} | {x['detail']} |" for x in findings]
    lines += [
        "", "## Suspected PII", "", "| Column | Classification |", "|---|---|",
    ]
    lines += [f"| `{r.column}` | {r.suspected_pii} |" for r in prof.itertuples() if r.suspected_pii]
    lines += [
        "", "## Column profile", "",
        "| Column | Type | Null % | Distinct | Min | Max | Outliers (3xIQR) | Top pattern (share) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in prof.itertuples():
        pattern = f"`{r.top_pattern}` ({r.top_pattern_share:.2%})" if r.top_pattern and len(str(r.top_pattern)) <= 24 else ""
        mn = str(r.min)[:24] if r.min is not None else ""
        mx = str(r.max)[:24] if r.max is not None else ""
        lines.append(f"| `{r.column}` | {r.data_type} | {r.null_pct:.4f} | {r.distinct_count:,} | {mn} | {mx} | "
                     f"{_fmt(r.outlier_count)} | {pattern} |")
    lines += ["", "## Numeric distributions", "", "| Column | Mean | Std | p01 | p25 | p50 | p75 | p99 |", "|---|---|---|---|---|---|---|---|"]
    for r in prof.itertuples():
        if r.percentiles:
            p = r.percentiles
            lines.append(f"| `{r.column}` | {_fmt(r.mean)} | {_fmt(r.std)} | {_fmt(p['p01'])} | {_fmt(p['p25'])} | "
                         f"{_fmt(p['p50'])} | {_fmt(p['p75'])} | {_fmt(p['p99'])} |")
    lines += ["", "## Low-cardinality columns (top values)", ""]
    for r in prof.itertuples():
        if r.top_values and r.distinct_count <= 20:
            vals = ", ".join(f"`{v['value']}` ({v['count']:,})" for v in r.top_values[:20])
            lines.append(f"- **{r.column}**: {vals}")
    path = resolve(ctx.cfg["paths"]["reports_dir"]) / "data_profile.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(ctx: RunContext) -> pd.DataFrame:
    con = ctx.con
    prof = profile_columns(con)
    findings = discovery_findings(con)
    summary = {
        "rows": ctx.scalar(f"SELECT count(*) FROM {RELATION}"),
        "source_rows": ctx.scalar(f"SELECT count(*) FROM {RELATION} WHERE NOT is_synthetic"),
        "synthetic_rows": ctx.scalar(f"SELECT count(*) FROM {RELATION} WHERE is_synthetic"),
        "min_ts": str(ctx.scalar(f"SELECT min(txn_ts) FROM {RELATION}")),
        "max_ts": str(ctx.scalar(f"SELECT max(txn_ts) FROM {RELATION}")),
    }
    stored = prof.copy()
    for col in ("percentiles", "top_values"):
        stored[col] = stored[col].apply(lambda v: json.dumps(v) if v is not None else None)
    stored.insert(0, "run_id", ctx.run_id)
    con.register("profile_df", stored)
    con.execute("CREATE OR REPLACE TABLE governance.profile_columns AS SELECT * FROM profile_df")
    con.unregister("profile_df")
    findings_df = pd.DataFrame(findings).assign(run_id=ctx.run_id)
    findings_df["value"] = findings_df["value"].astype(str)
    con.register("findings_df", findings_df)
    con.execute("CREATE OR REPLACE TABLE governance.discovery_findings AS SELECT * FROM findings_df")
    con.unregister("findings_df")

    reports = resolve(ctx.cfg["paths"]["reports_dir"])
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "data_profile.json").write_text(
        json.dumps({"run_id": ctx.run_id, "summary": summary, "findings": findings,
                    "columns": prof.to_dict("records")}, indent=2, default=str), encoding="utf-8")
    write_markdown(ctx, prof, findings, summary)
    ctx.add_lineage("bronze.transactions_typed", "governance.profile_columns", "profiling", "Column statistics and PII scan")
    ctx.metrics["profiled_columns"] = len(prof)
    ctx.metrics["pii_columns"] = int(prof["suspected_pii"].notna().sum())
    ctx.metrics["discovery_findings"] = findings
    ctx.log.info("Profiling: %d columns profiled, %d with suspected PII, %d discovery findings",
                 len(prof), ctx.metrics["pii_columns"], len(findings))
    return prof
