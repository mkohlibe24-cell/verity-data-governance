"""Step 1 - Ingestion into the BRONZE layer.

Bronze keeps the data exactly as it arrived: every column is text (VARCHAR), nothing is
cleaned and nothing is dropped. Every row gets lineage columns: which file it came from,
its row number in that file, the pipeline run that loaded it, and whether it is synthetic.

A source manifest (file checksum, record count, amount total) is captured independently
of DuckDB. The reconciliation step later checks that bronze ties back to it.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pandas as pd

from pipeline.common import RunContext, resolve
from pipeline.generate_data import RAW_COLUMNS, Generator

RAW_SELECT = ", ".join(f'"{c}"' for c in RAW_COLUMNS)


def ensure_extracted(cfg: dict) -> list[Path]:
    """Extract the Kaggle zip once (the CSVs are git-ignored, the zip stays in data/raw)."""
    out_dir = resolve(cfg["paths"]["raw_extract_dir"])
    files = [out_dir / name for name in cfg["paths"]["raw_files"]]
    if not all(f.exists() for f in files):
        zip_path = resolve(cfg["paths"]["raw_zip"])
        if not zip_path.exists():
            raise FileNotFoundError(
                f"{zip_path} not found. Download the Kaggle dataset (see README) "
                "or run the pipeline with --sample."
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(out_dir)
    return files


def file_manifest(path: Path, source: str, amount_col: str = "amt") -> dict:
    """Checksum, record count and amount total computed with pandas (independent of DuckDB)."""
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    amounts = pd.read_csv(path, usecols=[amount_col], dtype=str)[amount_col]
    numeric = pd.to_numeric(amounts, errors="coerce")
    # Sum in integer cents to avoid floating-point drift over millions of rows
    cents = (numeric.dropna() * 100).round().astype("int64").sum()
    return {
        "source": source,
        "file": path.name,
        "sha256": sha.hexdigest(),
        "file_bytes": path.stat().st_size,
        "records": int(len(amounts)),
        "amount_total": round(int(cents) / 100, 2),
    }


def run(ctx: RunContext) -> None:
    cfg, con = ctx.cfg, ctx.con
    loaded_at = ctx.started_at.strftime("%Y-%m-%d %H:%M:%S")
    manifest = []

    # 1) Land the Kaggle data (full files, or the committed sample in --sample mode)
    if ctx.mode == "full":
        files = ensure_extracted(cfg)
        selects = []
        for f in files:
            source = f"kaggle_{f.stem}"
            selects.append(
                f"SELECT '{source}' AS _source, CAST(column00 AS BIGINT) AS _source_row_num, {RAW_SELECT} "
                f"FROM read_csv('{f.as_posix()}', all_varchar = true, header = true)"
            )
            manifest.append(file_manifest(f, source))
            ctx.add_lineage(f"source: {f.name}", "bronze.transactions_raw", "ingest", "Load as text, add lineage columns")
    else:
        sample = resolve(cfg["paths"]["sample_file"])
        selects = [
            f"SELECT _source, CAST(_source_row_num AS BIGINT) AS _source_row_num, {RAW_SELECT} "
            f"FROM read_csv('{sample.as_posix()}', all_varchar = true, header = true)"
        ]
        manifest.append(file_manifest(sample, "kaggle_sample"))
        ctx.add_lineage(f"source: {sample.name}", "bronze.transactions_raw", "ingest", "Load as text, add lineage columns")

    con.execute(
        f"""
        CREATE OR REPLACE TABLE bronze.transactions_raw AS
        SELECT row_number() OVER (ORDER BY _source, _source_row_num) AS bronze_row_id,
               *,
               FALSE AS is_synthetic,
               CAST(NULL AS VARCHAR) AS synthetic_tag,
               '{ctx.run_id}' AS _run_id,
               TIMESTAMP '{loaded_at}' AS _ingested_at
        FROM ({" UNION ALL ".join(selects)})
        """
    )
    kaggle_rows = ctx.scalar("SELECT count(*) FROM bronze.transactions_raw")
    ctx.log.info("Bronze: landed %s source records", f"{kaggle_rows:,}")

    # 2) Generate tagged synthetic rows (scenarios + data quality defects) and append them
    scale = cfg["generator"]["sample_scale"] if ctx.mode == "sample" else 1.0
    generated = Generator(con, cfg, scale).generate()
    gen_path = resolve(cfg["paths"]["generated_file"])
    gen_path.parent.mkdir(parents=True, exist_ok=True)
    generated.to_csv(gen_path, index=False)
    manifest.append(file_manifest(gen_path, "generator"))
    ctx.add_lineage("source: generate_data.py (seeded)", "bronze.transactions_raw", "ingest",
                    "Tagged synthetic scenarios and DQ defects")

    con.execute(
        f"""
        INSERT INTO bronze.transactions_raw BY NAME
        SELECT {kaggle_rows} + row_number() OVER (ORDER BY CAST(_source_row_num AS BIGINT)) AS bronze_row_id,
               _source, CAST(_source_row_num AS BIGINT) AS _source_row_num, {RAW_SELECT},
               TRUE AS is_synthetic, synthetic_tag,
               '{ctx.run_id}' AS _run_id, TIMESTAMP '{loaded_at}' AS _ingested_at
        FROM read_csv('{gen_path.as_posix()}', all_varchar = true, header = true)
        """
    )
    total = ctx.scalar("SELECT count(*) FROM bronze.transactions_raw")
    ctx.log.info("Bronze: appended %s generated rows (total %s)", f"{len(generated):,}", f"{total:,}")

    # 3) Source manifest (ingestion evidence) - persisted per run
    con.execute(
        """CREATE TABLE IF NOT EXISTS governance.source_manifest (
               run_id VARCHAR, source VARCHAR, file VARCHAR, sha256 VARCHAR, file_bytes BIGINT,
               records BIGINT, amount_total DECIMAL(18,2))"""
    )
    con.execute("DELETE FROM governance.source_manifest WHERE run_id = ?", [ctx.run_id])
    for m in manifest:
        con.execute(
            "INSERT INTO governance.source_manifest VALUES (?, ?, ?, ?, ?, ?, ?)",
            [ctx.run_id, m["source"], m["file"], m["sha256"], m["file_bytes"], m["records"], m["amount_total"]],
        )

    # 4) Typed view over bronze: TRY_CAST never fails, it returns NULL, so DQ rules can see
    #    exactly which raw values could not be parsed.
    con.execute(
        """
        CREATE OR REPLACE VIEW bronze.transactions_typed AS
        SELECT
            bronze_row_id, _source, _source_row_num, is_synthetic, synthetic_tag, _run_id, _ingested_at,
            NULLIF(trim(trans_num), '')                                   AS trans_num,
            NULLIF(trim(cc_num), '')                                      AS cc_num,
            NULLIF(trim(merchant), '')                                    AS merchant,
            NULLIF(trim(category), '')                                    AS category,
            NULLIF(trim(trans_date_trans_time), '')                       AS txn_ts_raw,
            TRY_STRPTIME(NULLIF(trim(trans_date_trans_time), ''), '%Y-%m-%d %H:%M:%S') AS txn_ts,
            NULLIF(trim(amt), '')                                         AS amt_raw,
            TRY_CAST(NULLIF(trim(amt), '') AS DECIMAL(18, 2))             AS amount,
            NULLIF(trim("first"), '')                                     AS first_name,
            NULLIF(trim("last"), '')                                      AS last_name,
            NULLIF(trim(gender), '')                                      AS gender,
            NULLIF(trim(street), '')                                      AS street,
            NULLIF(trim(city), '')                                        AS city,
            NULLIF(trim(state), '')                                       AS state,
            NULLIF(trim(zip), '')                                         AS zip,
            TRY_CAST(lat AS DOUBLE)                                       AS home_lat,
            TRY_CAST("long" AS DOUBLE)                                    AS home_long,
            TRY_CAST(city_pop AS BIGINT)                                  AS city_pop,
            NULLIF(trim(job), '')                                         AS job,
            NULLIF(trim(dob), '')                                         AS dob_raw,
            TRY_CAST(NULLIF(trim(dob), '') AS DATE)                       AS dob,
            TRY_CAST(unix_time AS BIGINT)                                 AS unix_time,
            TRY_CAST(merch_lat AS DOUBLE)                                 AS merch_lat,
            TRY_CAST(merch_long AS DOUBLE)                                AS merch_long,
            NULLIF(trim(is_fraud), '')                                    AS is_fraud_raw,
            TRY_CAST(NULLIF(trim(is_fraud), '') AS INTEGER)               AS is_fraud
        FROM bronze.transactions_raw
        """
    )
    ctx.add_lineage("bronze.transactions_raw", "bronze.transactions_typed", "ingest", "TRY_CAST to types (view)")
    ctx.metrics["bronze_rows"] = int(total)
    ctx.metrics["kaggle_rows"] = int(kaggle_rows)
    ctx.metrics["synthetic_rows"] = int(len(generated))
    ctx.metrics["synthetic_scenario_rows"] = int((generated["_source"] == "generator_scenarios").sum())
    ctx.metrics["synthetic_defect_rows"] = int((generated["_source"] == "generator_dq_defects").sum())
