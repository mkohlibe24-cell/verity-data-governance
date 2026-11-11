"""Step 9 - ML risk scoring (kept deliberately light).

  * Features are behavioural and use ONLY past data for each card (window frames end at
    the previous transaction), so nothing leaks from the future.
  * Time-based split: train before 2020-06-01, validation = June 2020 (used only to pick the
    threshold), test = Jul-Dec 2020 (never used for any choice).
  * Models: Isolation Forest (unsupervised anomaly score) and LightGBM (supervised).
  * Metrics: precision, recall, F1, PR-AUC, confusion matrix. Accuracy is not used: with
    ~0.5% fraud, a model that flags nothing is 99.5% "accurate" and useless.
  * Threshold: chosen on validation by COST = false alarms x analyst review cost + amount of
    missed fraud. The whole trade-off curve is saved.
  * Explanations: LightGBM's built-in TreeSHAP (pred_contrib) gives each alert its top reasons.
"""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, precision_score, recall_score

from pipeline.common import RunContext, resolve

FEATURE_SQL = """
SELECT
    f.trans_num, f.txn_ts, f.txn_month, f.category, f.amount::DOUBLE AS amount,
    f.is_fraud, f.is_synthetic, f.synthetic_tag,
    hour(f.txn_ts)                                    AS hour_of_day,
    dayofweek(f.txn_ts)                               AS day_of_week,
    f.distance_from_home_km,
    year(f.txn_ts) - c.birth_year                     AS age,
    ln(1 + c.city_pop)                                AS log_city_pop,
    date_diff('second', LAG(f.txn_ts) OVER (PARTITION BY f.card_token ORDER BY f.txn_ts), f.txn_ts) / 3600.0
                                                      AS hours_since_prev,
    COUNT(*) OVER (PARTITION BY f.card_token ORDER BY f.txn_ts
                   RANGE BETWEEN INTERVAL 1 HOUR PRECEDING AND CURRENT ROW EXCLUDE CURRENT ROW)   AS txn_count_1h,
    COUNT(*) OVER (PARTITION BY f.card_token ORDER BY f.txn_ts
                   RANGE BETWEEN INTERVAL 24 HOURS PRECEDING AND CURRENT ROW EXCLUDE CURRENT ROW) AS txn_count_24h,
    COALESCE(SUM(f.amount::DOUBLE) OVER (PARTITION BY f.card_token ORDER BY f.txn_ts
                   RANGE BETWEEN INTERVAL 24 HOURS PRECEDING AND CURRENT ROW EXCLUDE CURRENT ROW), 0) AS amount_sum_24h,
    AVG(f.amount::DOUBLE) OVER prior                  AS prior_avg_amount,
    STDDEV_SAMP(f.amount::DOUBLE) OVER prior          AS prior_std_amount,
    AVG(CASE WHEN hour(f.txn_ts) >= 22 OR hour(f.txn_ts) < 4 THEN 1.0 ELSE 0.0 END) OVER prior AS prior_night_share,
    COUNT(*) OVER prior                               AS prior_txn_count,
    COUNT(*) OVER (PARTITION BY f.card_token, f.category ORDER BY f.txn_ts
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)                             AS prior_txn_in_category
FROM gold.fact_transactions f
JOIN gold.dim_customer c USING (card_token)
WINDOW prior AS (PARTITION BY f.card_token ORDER BY f.txn_ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
ORDER BY f.txn_ts, f.trans_num  -- fixed row order so training is reproducible run to run
"""

FEATURES = [
    "amount", "log_amount", "hour_of_day", "day_of_week", "is_night", "distance_from_home_km", "age",
    "log_city_pop", "hours_since_prev", "txn_count_1h", "txn_count_24h", "amount_sum_24h",
    "amount_to_prior_avg", "amount_zscore", "prior_night_share", "is_new_category", "category",
]
# Not used as a feature: prior_txn_count (card history length). It grows with time by construction,
# so its distribution drifts every month (guaranteed PSI breach) and it describes the card's age,
# not risky behaviour. It is still computed because the amount baselines need it.
IF_FEATURES = [f for f in FEATURES if f != "category"]

READABLE = {
    "amount": "Transaction amount", "log_amount": "Transaction amount (log)", "hour_of_day": "Hour of day",
    "day_of_week": "Day of week", "is_night": "Night-time transaction", "distance_from_home_km": "Distance from home",
    "age": "Cardholder age", "log_city_pop": "City population", "hours_since_prev": "Time since previous transaction",
    "txn_count_1h": "Transactions in previous hour", "txn_count_24h": "Transactions in previous 24h",
    "amount_sum_24h": "Spend in previous 24h", "amount_to_prior_avg": "Amount vs card's average",
    "amount_zscore": "Amount z-score vs card history", "prior_night_share": "Card's usual night-time share",
    "is_new_category": "First purchase in this category", "prior_txn_count": "Card history length",
    "category": "Merchant category",
}


def build_features(con) -> pd.DataFrame:
    df = con.execute(FEATURE_SQL).df()
    df["log_amount"] = np.log1p(df["amount"])
    df["is_night"] = ((df["hour_of_day"] >= 22) | (df["hour_of_day"] < 4)).astype(int)
    df["amount_to_prior_avg"] = (df["amount"] / df["prior_avg_amount"]).replace([np.inf, -np.inf], np.nan)
    df["amount_zscore"] = ((df["amount"] - df["prior_avg_amount"]) / df["prior_std_amount"]).replace(
        [np.inf, -np.inf], np.nan)
    df["is_new_category"] = (df["prior_txn_in_category"] == 0).astype(int)
    cats = sorted(df["category"].dropna().unique())
    df["category"] = pd.Categorical(df["category"], categories=cats)
    for c in FEATURES:
        if c != "category":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def evaluate(y: np.ndarray, flag: np.ndarray, amount: np.ndarray, score: np.ndarray | None = None) -> dict:
    tn, fp, fn, tp = confusion_matrix(y, flag, labels=[0, 1]).ravel()
    out = {
        "alerts": int(flag.sum()), "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        "precision": round(float(precision_score(y, flag, zero_division=0)), 4),
        "recall": round(float(recall_score(y, flag, zero_division=0)), 4),
        "f1": round(float(f1_score(y, flag, zero_division=0)), 4),
        "accuracy": round(float((tp + tn) / len(y)), 6),
        "fraud_amount_caught": round(float(amount[(y == 1) & flag].sum()), 2),
        "fraud_amount_missed": round(float(amount[(y == 1) & ~flag].sum()), 2),
    }
    if score is not None:
        out["pr_auc"] = round(float(average_precision_score(y, score)), 4)
    return out


def threshold_curve(y, score, amount, thresholds, cost_fp: float) -> pd.DataFrame:
    rows = []
    for t in thresholds:
        flag = score >= t
        tp = int(((y == 1) & flag).sum())
        fp = int(((y == 0) & flag).sum())
        fn = int(((y == 1) & ~flag).sum())
        missed = float(amount[(y == 1) & ~flag].sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        rows.append({
            "threshold": round(float(t), 6), "alerts": int(flag.sum()), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
            "review_cost": round(fp * cost_fp, 2), "missed_fraud_amount": round(missed, 2),
            "total_cost": round(fp * cost_fp + missed, 2),
        })
    return pd.DataFrame(rows)


def top_reasons(contrib: np.ndarray, X: pd.DataFrame, k: int) -> list[str]:
    """For each row, the k features pushing the score UP the most, in readable form."""
    names = list(X.columns)
    out = []
    for i in range(contrib.shape[0]):
        row = contrib[i, :-1]  # last column is the bias term
        idx = np.argsort(-row)[:k]
        parts = []
        for j in idx:
            if row[j] <= 0:
                continue
            val = X.iat[i, j]
            shown = val if isinstance(val, str) else (f"{val:,.2f}" if pd.notna(val) else "n/a")
            parts.append(f"{READABLE[names[j]]} = {shown} (+{row[j]:.2f})")
        out.append("; ".join(parts))
    return out


def run(ctx: RunContext) -> None:
    con, cfg = ctx.con, ctx.cfg
    mcfg = cfg["ml"]
    df = build_features(con)
    df = df[df["is_fraud"].notna()].copy()  # unlabelled records cannot be used to train or evaluate
    df["is_fraud"] = df["is_fraud"].astype(int)
    train_end, val_end = pd.Timestamp(mcfg["train_end"]), pd.Timestamp(mcfg["validation_end"])
    df["split"] = np.where(df["txn_ts"] < train_end, "train", np.where(df["txn_ts"] < val_end, "validation", "test"))
    tr, va, te = (df[df["split"] == s] for s in ("train", "validation", "test"))
    ctx.log.info("ML split: train %s (%s fraud), validation %s (%s), test %s (%s)",
                 f"{len(tr):,}", f"{tr.is_fraud.sum():,}", f"{len(va):,}", f"{va.is_fraud.sum():,}",
                 f"{len(te):,}", f"{te.is_fraud.sum():,}")

    # --- Supervised: LightGBM ------------------------------------------------------------
    model = lgb.LGBMClassifier(objective="binary", verbose=-1, **mcfg["lightgbm"])
    model.fit(tr[FEATURES], tr["is_fraud"])
    df["ml_score"] = model.predict_proba(df[FEATURES])[:, 1]
    va, te = df[df["split"] == "validation"], df[df["split"] == "test"]

    cost_fp = float(mcfg["cost_false_positive"])
    grid = np.round(np.concatenate([np.arange(0.001, 0.01, 0.001), np.arange(0.01, 1.0, 0.01)]), 3)
    val_curve = threshold_curve(va.is_fraud.values, va.ml_score.values, va.amount.values, grid, cost_fp)
    # Alert fatigue: only thresholds whose daily alert volume fits analyst capacity are allowed
    val_days = max((va["txn_ts"].max() - va["txn_ts"].min()).days + 1, 1)
    val_curve["alerts_per_day"] = (val_curve["alerts"] / val_days).round(1)
    capacity = float(mcfg["analyst_capacity_alerts_per_day"])
    feasible = val_curve[val_curve["alerts_per_day"] <= capacity]
    best = (feasible if not feasible.empty else val_curve).sort_values(["total_cost", "threshold"]).iloc[0]
    unconstrained = float(val_curve.sort_values(["total_cost", "threshold"]).iloc[0]["threshold"])
    threshold = float(best["threshold"])
    test_curve = threshold_curve(te.is_fraud.values, te.ml_score.values, te.amount.values, grid, cost_fp)
    f1_best = float(val_curve.loc[val_curve["f1"].idxmax(), "threshold"])
    ctx.log.info("ML threshold chosen on validation by cost within %s alerts/day: %.3f "
                 "(unconstrained cost optimum %.3f, F1-optimal %.3f)", capacity, threshold, unconstrained, f1_best)

    # --- Unsupervised: Isolation Forest ----------------------------------------------------
    icfg = mcfg["isolation_forest"]
    fill = tr[IF_FEATURES].median()
    fit_rows = tr.sample(n=min(icfg["fit_sample_rows"], len(tr)), random_state=icfg["random_state"])
    iso = IsolationForest(n_estimators=icfg["n_estimators"], max_samples=icfg["max_samples"],
                          random_state=icfg["random_state"], n_jobs=-1)
    iso.fit(fit_rows[IF_FEATURES].fillna(fill))
    df["if_score"] = -iso.score_samples(df[IF_FEATURES].fillna(fill))  # higher = more anomalous
    va, te = df[df["split"] == "validation"], df[df["split"] == "test"]
    if_grid = np.quantile(va.if_score, [1 - k for k in (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1)])
    if_curve = threshold_curve(va.is_fraud.values, va.if_score.values, va.amount.values, if_grid, cost_fp)
    if_threshold = float(if_curve.loc[if_curve["total_cost"].idxmin(), "threshold"])

    # --- Compare rules vs ML vs hybrid on the TEST period ---------------------------------
    rule_txns = set(con.execute("SELECT DISTINCT trans_num FROM monitoring.rule_hits").df()["trans_num"])
    te = te.assign(rule_flag=te["trans_num"].isin(rule_txns), ml_flag=te["ml_score"] >= threshold,
                   if_flag=te["if_score"] >= if_threshold)
    te["hybrid_flag"] = te["rule_flag"] | te["ml_flag"]
    y, amt = te["is_fraud"].values, te["amount"].values
    comparison = {
        "Rules only (8 SQL rules)": evaluate(y, te["rule_flag"].values, amt),
        "Isolation Forest only": evaluate(y, te["if_flag"].values, amt, te["if_score"].values),
        "LightGBM only": evaluate(y, te["ml_flag"].values, amt, te["ml_score"].values),
        "Hybrid (rules OR LightGBM)": evaluate(y, te["hybrid_flag"].values, amt),
    }
    fraud = te["is_fraud"] == 1
    src = fraud & ~te["is_synthetic"]

    def overlap(mask) -> dict:
        missed_by_rules = mask & ~te["rule_flag"]
        missed_by_ml = mask & ~te["ml_flag"]
        return {
            "fraud_transactions": int(mask.sum()),
            "caught_by_rules": int((mask & te["rule_flag"]).sum()),
            "caught_by_ml": int((mask & te["ml_flag"]).sum()),
            "caught_by_hybrid": int((mask & te["hybrid_flag"]).sum()),
            "caught_by_both": int((mask & te["rule_flag"] & te["ml_flag"]).sum()),
            "ml_only": int((missed_by_rules & te["ml_flag"]).sum()),
            "rules_only": int((missed_by_ml & te["rule_flag"]).sum()),
            "missed_by_rules": int(missed_by_rules.sum()),
            "pct_of_rule_misses_caught_by_ml": round(100 * (missed_by_rules & te["ml_flag"]).sum()
                                                     / max(int(missed_by_rules.sum()), 1), 2),
            "pct_of_ml_misses_caught_by_rules": round(100 * (missed_by_ml & te["rule_flag"]).sum()
                                                      / max(int(missed_by_ml.sum()), 1), 2),
        }

    segments = {"All fraud": fraud, "Original (Kaggle) fraud": src}
    for tag in sorted(te.loc[fraud & te["is_synthetic"], "synthetic_tag"].dropna().unique()):
        segments[f"Injected: {tag.split(':', 1)[1]}"] = fraud & (te["synthetic_tag"] == tag)
    overlaps = {name: overlap(mask) for name, mask in segments.items()}

    # --- Explanations (TreeSHAP) for test-period transactions that are flagged -------------
    flagged = te[te["ml_flag"] | te["rule_flag"]]
    contrib = model.predict(flagged[FEATURES], pred_contrib=True)
    reasons = top_reasons(np.asarray(contrib), flagged[FEATURES], mcfg["top_reasons"])
    sample = te.sample(n=min(50000, len(te)), random_state=42)
    global_contrib = np.abs(np.asarray(model.predict(sample[FEATURES], pred_contrib=True))[:, :-1]).mean(axis=0)
    importance = (pd.DataFrame({"feature": FEATURES, "mean_abs_shap": global_contrib})
                  .assign(feature_label=lambda d: d["feature"].map(READABLE))
                  .sort_values("mean_abs_shap", ascending=False))

    # --- Persist ----------------------------------------------------------------------------
    scores = df[["trans_num", "split", "ml_score", "if_score"]].copy()
    scores["ml_flag"] = scores["ml_score"] >= threshold
    scores["if_flag"] = scores["if_score"] >= if_threshold
    scores = scores.merge(pd.DataFrame({"trans_num": flagged["trans_num"].values, "ml_reasons": reasons}),
                          on="trans_num", how="left")
    con.register("scores_df", scores)
    con.execute("CREATE OR REPLACE TABLE monitoring.ml_scores AS SELECT * FROM scores_df")
    con.unregister("scores_df")

    reports = resolve(cfg["paths"]["reports_dir"])
    val_curve.assign(split="validation").to_csv(reports / "ml_threshold_curve_validation.csv", index=False)
    test_curve.assign(split="test").to_csv(reports / "ml_threshold_curve_test.csv", index=False)
    importance.to_csv(reports / "ml_feature_importance.csv", index=False)
    for name, tbl in (("ml_threshold_curve", pd.concat([val_curve.assign(split="validation"),
                                                         test_curve.assign(split="test")])),
                      ("ml_feature_importance", importance)):
        con.register("tmp_df", tbl)
        con.execute(f"CREATE OR REPLACE TABLE monitoring.{name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")

    metrics = {
        "split": {s: {"rows": int((df.split == s).sum()), "fraud": int(df.loc[df.split == s, "is_fraud"].sum())}
                  for s in ("train", "validation", "test")},
        "threshold": {"chosen_by_cost_on_validation": threshold, "f1_optimal_on_validation": f1_best,
                      "unconstrained_cost_optimum": unconstrained, "analyst_capacity_alerts_per_day": capacity,
                      "validation_alerts_per_day_at_threshold": float(best["alerts_per_day"]),
                      "cost_false_positive_usd": cost_fp, "isolation_forest_threshold": round(if_threshold, 6)},
        "validation_at_threshold": best.to_dict(),
        "comparison_test": comparison,
        "overlap_test": overlaps,
        "top_drivers": importance.head(10)[["feature_label", "mean_abs_shap"]].round(4).to_dict("records"),
        "accuracy_note": "A model that never flags fraud would score "
                         f"{100 * (1 - te['is_fraud'].mean()):.2f}% accuracy on the test period.",
    }
    (reports / "model_metrics.json").write_text(json.dumps(metrics, indent=2, default=float), encoding="utf-8")
    ctx.metrics["ml"] = metrics
    ctx.add_lineage("gold.fact_transactions", "monitoring.ml_scores", "ml", "Behavioural features -> LightGBM + Isolation Forest")
    c = comparison
    ctx.log.info("Test period: rules P=%.3f R=%.3f | LightGBM P=%.3f R=%.3f PR-AUC=%.3f | IF PR-AUC=%.3f | hybrid P=%.3f R=%.3f",
                 c["Rules only (8 SQL rules)"]["precision"], c["Rules only (8 SQL rules)"]["recall"],
                 c["LightGBM only"]["precision"], c["LightGBM only"]["recall"], c["LightGBM only"]["pr_auc"],
                 c["Isolation Forest only"]["pr_auc"], c["Hybrid (rules OR LightGBM)"]["precision"],
                 c["Hybrid (rules OR LightGBM)"]["recall"])
