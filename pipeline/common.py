"""Shared helpers used by every pipeline step.

Config loading, the DuckDB connection, logging and the run context live in one place so
every step behaves the same way and no step hard-codes a path or a threshold.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ("bronze", "silver", "gold", "governance", "monitoring")


def load_yaml(path: str | Path) -> Any:
    with open(resolve(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_config(path: str | Path | None = None) -> dict:
    return load_yaml(path or ROOT / "config" / "config.yaml")


def resolve(path: str | Path) -> Path:
    """Turn a repo-relative path from config into an absolute path."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def connect(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open DuckDB (in-memory when db_path is None, used by tests) and create the layer schemas."""
    if db_path is None or str(db_path) == ":memory:":
        con = duckdb.connect(":memory:")
    else:
        p = resolve(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(p))
    for schema in SCHEMAS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    register_macros(con)
    return con


def register_macros(con: duckdb.DuckDBPyConnection) -> None:
    """SQL helper functions shared by DQ rules and monitoring rules.

    luhn_valid: the check-digit algorithm every payment card number must pass.
    haversine_km: great-circle distance between two lat/long points.
    """
    con.execute(
        """
        CREATE OR REPLACE MACRO luhn_valid(s) AS (
            CASE WHEN s IS NULL OR NOT regexp_full_match(s, '[0-9]+') THEN FALSE
            ELSE list_sum(list_transform(range(1, length(s) + 1), lambda i:
                    CASE WHEN i % 2 = 0 THEN
                        (CASE WHEN TRY_CAST(substr(reverse(s), i, 1) AS INTEGER) * 2 > 9
                              THEN TRY_CAST(substr(reverse(s), i, 1) AS INTEGER) * 2 - 9
                              ELSE TRY_CAST(substr(reverse(s), i, 1) AS INTEGER) * 2 END)
                    ELSE TRY_CAST(substr(reverse(s), i, 1) AS INTEGER) END)) % 10 = 0
            END)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE MACRO haversine_km(lat1, lon1, lat2, lon2) AS (
            2 * 6371 * asin(sqrt(
                power(sin(radians(lat2 - lat1) / 2), 2)
                + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)
            ))
        )
        """
    )


_PARAM = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_sql(template: str, params: dict) -> str:
    """Replace {{ name }} placeholders in a .sql file with values from config.

    Failing loudly on a missing parameter is deliberate: a rule silently running with a
    blank threshold would be a broken control nobody notices.
    """

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"SQL template parameter '{key}' is missing from config")
        return str(params[key])

    return _PARAM.sub(substitute, template)


def sql_literal(value: Any) -> str:
    """Quote a Python value as a SQL literal (only used for values that come from config)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def setup_logging(run_id: str, logs_dir: str | Path) -> logging.Logger:
    """Log every step to the console and to logs/<run_id>.log (the run's evidence file)."""
    log_dir = resolve(logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("verity")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_dir / f"{run_id}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


@dataclass
class RunContext:
    """Everything a pipeline step needs: config, DB connection, logger and run metadata."""

    run_id: str
    cfg: dict
    con: duckdb.DuckDBPyConnection
    log: logging.Logger
    mode: str = "full"  # "full" (Kaggle zip) or "sample" (committed CC0 sample)
    started_at: dt.datetime = field(default_factory=dt.datetime.now)
    metrics: dict = field(default_factory=dict)
    lineage: list = field(default_factory=list)

    @property
    def as_of(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.cfg["project"]["batch_as_of"])

    def add_lineage(self, source: str, target: str, step: str, transformation: str) -> None:
        """Record a lineage edge. Lineage is built from what the pipeline actually did."""
        self.lineage.append(
            {"source": source, "target": target, "step": step, "transformation": transformation}
        )

    def scalar(self, sql: str, params: list | None = None) -> Any:
        rows = self.con.execute(sql, params or []).fetchall()  # fetch fully (DuckDB 2.0 requirement)
        return rows[0][0] if rows else None


def silent_logger() -> logging.Logger:
    """A logger that discards output (used by tests)."""
    logger = logging.getLogger("verity.test")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger
