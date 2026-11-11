"""Steps 8 and 10 - Transaction monitoring data product.

run_rules:    executes every SQL rule in sql/monitoring/ against the GOLD layer (masked
              data: monitoring never needs the real card number) -> monitoring.rule_hits
build_alerts: combines rule hits with the ML score into one alert per transaction, with
              a combined score and a P1-P4 priority -> monitoring.alerts, plus per-rule
              performance -> monitoring.rule_performance
"""

from __future__ import annotations

import pandas as pd

from pipeline.common import RunContext, render_sql, resolve


def rule_sql(cfg: dict, rule_id: str) -> str:
    """Read a rule's .sql file and fill its thresholds from config."""
    r = cfg["monitoring"]["rules"][rule_id]
    template = (resolve(cfg["monitoring"]["rules_dir"]) / r["file"]).read_text(encoding="utf-8")
    return render_sql(template, r["params"])


def execute_rules(con, cfg: dict) -> pd.DataFrame:
    con.execute(
        """CREATE OR REPLACE TABLE monitoring.rule_hits (
               rule_id VARCHAR, trans_num VARCHAR, card_token VARCHAR, txn_ts TIMESTAMP,
               amount DECIMAL(18, 2), reason VARCHAR, severity VARCHAR, base_score INTEGER)"""
    )
    stats = []
    for rid, r in cfg["monitoring"]["rules"].items():
        con.execute(
            f"""INSERT INTO monitoring.rule_hits
                SELECT rule_id, trans_num, card_token, txn_ts, amount, reason, '{r['severity']}', {int(r['base_score'])}
                FROM ({rule_sql(cfg, rid)})"""
        )
        n = con.execute("SELECT count(*) FROM monitoring.rule_hits WHERE rule_id = ?", [rid]).fetchall()[0][0]
        stats.append({"rule_id": rid, "name": r["name"], "hits": int(n)})
    catalogue = pd.DataFrame([{
        "rule_id": rid, "name": r["name"], "risk_addressed": r["risk"], "logic": r["logic"],
        "threshold_rationale": r["threshold_rationale"], "parameters": str(r["params"]),
        "control_type": "Detective", "automation": "Automated", "severity": r["severity"],
        "base_score": r["base_score"], "owner": r["owner"], "review_frequency": r["frequency"],
        "sql_file": f"sql/monitoring/{r['file']}",
    } for rid, r in cfg["monitoring"]["rules"].items()])
    con.register("cat_df", catalogue)
    con.execute("CREATE OR REPLACE TABLE monitoring.rule_catalogue AS SELECT * FROM cat_df")
    con.unregister("cat_df")
    return pd.DataFrame(stats)


def run_rules(ctx: RunContext) -> None:
    stats = execute_rules(ctx.con, ctx.cfg)
    for s in stats.itertuples():
        ctx.log.info("  %s %-36s %s hits", s.rule_id, s.name, f"{s.hits:,}")
    ctx.add_lineage("gold.fact_transactions", "monitoring.rule_hits", "monitoring", "8 SQL detective rules")
    ctx.metrics["rule_hits"] = int(stats["hits"].sum())


def build_alerts(ctx: RunContext) -> None:
    con, cfg = ctx.con, ctx.cfg
    pr = cfg["monitoring"]["alert_priority"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE monitoring.alerts AS
        WITH r AS (
            SELECT trans_num,
                   list(rule_id ORDER BY rule_id)  AS rules_fired,
                   list(reason ORDER BY rule_id)   AS rule_reasons,
                   LEAST(100, max(base_score) + 10 * (count(*) - 1)) AS rule_score
            FROM monitoring.rule_hits GROUP BY trans_num
        ),
        m AS (SELECT trans_num, ml_score, ml_flag, ml_reasons FROM monitoring.ml_scores WHERE split = 'test'),
        joined AS (
            SELECT f.trans_num, f.card_token, f.merchant_key, f.txn_ts, f.txn_month, f.amount, f.category,
                   COALESCE(r.rules_fired, CAST([] AS VARCHAR[])) AS rules_fired, r.rule_reasons,
                   COALESCE(r.rule_score, 0) AS rule_score,
                   m.ml_score, COALESCE(m.ml_flag, FALSE) AS ml_flag, m.ml_reasons,
                   r.trans_num IS NOT NULL AS rule_flag,
                   f.is_fraud, f.is_synthetic, f.synthetic_tag
            FROM gold.fact_transactions f
            LEFT JOIN r USING (trans_num)
            LEFT JOIN m USING (trans_num)
            WHERE r.trans_num IS NOT NULL OR COALESCE(m.ml_flag, FALSE)
        ),
        scored AS (
            SELECT *,
                   CASE WHEN rule_flag AND ml_flag THEN 'Rules + ML' WHEN rule_flag THEN 'Rules only' ELSE 'ML only' END
                       AS detection_source,
                   LEAST(100, GREATEST(rule_score, round(100 * COALESCE(ml_score, 0)))
                              + CASE WHEN rule_flag AND ml_flag THEN 10 ELSE 0 END) AS combined_score
            FROM joined
        )
        SELECT 'ALT-' || lpad(CAST(row_number() OVER (ORDER BY txn_ts, trans_num) AS VARCHAR), 7, '0') AS alert_id,
               *,
               CASE WHEN combined_score >= {pr['P1']} THEN 'P1' WHEN combined_score >= {pr['P2']} THEN 'P2'
                    WHEN combined_score >= {pr['P3']} THEN 'P3' ELSE 'P4' END AS priority
        FROM scored
        """
    )
    # Per-rule performance on the test period (Jul-Dec 2020), when labels are known
    test_start = cfg["ml"]["validation_end"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE monitoring.rule_performance AS
        WITH hits AS (
            SELECT DISTINCT h.rule_id, h.trans_num, f.is_fraud, f.is_synthetic, f.txn_ts
            FROM monitoring.rule_hits h JOIN gold.fact_transactions f USING (trans_num)
        ),
        rule_count AS (SELECT trans_num, count(DISTINCT rule_id) AS n_rules FROM hits GROUP BY 1),
        test_fraud AS (
            SELECT count(*) AS n FROM gold.fact_transactions WHERE is_fraud = 1 AND txn_ts >= TIMESTAMP '{test_start}'
        )
        SELECT h.rule_id, c.name,
               count(*)                                                      AS hits_all_periods,
               count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}')  AS hits_test,
               count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}' AND h.is_fraud = 1) AS fraud_hits_test,
               round(100.0 * count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}' AND h.is_fraud = 1)
                     / NULLIF(count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}'), 0), 2) AS precision_test_pct,
               round(100.0 * count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}' AND h.is_fraud = 1)
                     / NULLIF(max(t.n), 0), 2) AS recall_test_pct,
               count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}' AND h.is_fraud = 1 AND rc.n_rules = 1)
                                                                             AS unique_fraud_catches_test,
               count(*) FILTER (WHERE h.txn_ts >= TIMESTAMP '{test_start}' AND h.is_fraud = 1 AND NOT h.is_synthetic)
                                                                             AS source_fraud_hits_test
        FROM hits h
        JOIN rule_count rc USING (trans_num)
        JOIN monitoring.rule_catalogue c USING (rule_id)
        CROSS JOIN test_fraud t
        GROUP BY h.rule_id, c.name ORDER BY h.rule_id
        """
    )
    n = ctx.scalar("SELECT count(*) FROM monitoring.alerts")
    ctx.metrics["alerts"] = int(n)
    ctx.metrics["alerts_by_priority"] = dict(con.execute(
        "SELECT priority, count(*) FROM monitoring.alerts GROUP BY 1 ORDER BY 1").fetchall())
    ctx.add_lineage("monitoring.rule_hits", "monitoring.alerts", "monitoring", "One alert per transaction")
    ctx.add_lineage("monitoring.ml_scores", "monitoring.alerts", "monitoring", "ML score + reasons (test period)")
    ctx.log.info("Alerts: %s total, by priority %s", f"{n:,}", ctx.metrics["alerts_by_priority"])
