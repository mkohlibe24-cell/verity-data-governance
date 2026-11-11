"""Create the small committed sample (data/sample/kaggle_sample.csv.gz) from the full Kaggle files.

The Kaggle files are about 500 MB, too large for GitHub. The dataset is CC0 (public domain),
so a sample can be redistributed. Whole cards are sampled (all their transactions), not random
rows, because monitoring rules need each card's full history.

    python pipeline/make_sample.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from pipeline.common import load_config, resolve  # noqa: E402
from pipeline.generate_data import RAW_COLUMNS  # noqa: E402
from pipeline.ingest import ensure_extracted  # noqa: E402

N_CARDS = 30


def main() -> None:
    cfg = load_config()
    files = ensure_extracted(cfg)
    out = resolve(cfg["paths"]["sample_file"])
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f'"{c}"' for c in RAW_COLUMNS)
    union = " UNION ALL ".join(
        f"SELECT 'kaggle_{f.stem}' AS _source, column00 AS _source_row_num, {cols} "
        f"FROM read_csv('{f.as_posix()}', all_varchar = true, header = true)" for f in files)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE src AS {union}")
    # Deterministic card choice: order cards by the MD5 of the card number
    con.execute(f"CREATE TABLE cards AS SELECT cc_num FROM (SELECT DISTINCT cc_num FROM src) "
                f"ORDER BY md5(cc_num) LIMIT {N_CARDS}")
    con.execute(f"""COPY (SELECT s.* FROM src s JOIN cards USING (cc_num)
                          ORDER BY _source, CAST(_source_row_num AS BIGINT))
                    TO '{out.as_posix()}' (HEADER, DELIMITER ',', COMPRESSION gzip)""")
    n, fraud = con.execute(f"SELECT count(*), sum(CAST(is_fraud AS INT)) FROM read_csv('{out.as_posix()}', all_varchar = true)").fetchall()[0]
    print(f"Wrote {out} with {n:,} rows ({fraud} fraud) from {N_CARDS} cards, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
