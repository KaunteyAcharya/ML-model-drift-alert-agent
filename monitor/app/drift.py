"""Drift analysis with Evidently AI (0.7.x API).

Computes, for current batch vs. reference:
  * per-feature drift: Evidently's default statistical test (K-S for numeric
    columns at this sample size), PSI and KL divergence
  * dataset drift: share of drifted features (Evidently DriftedColumnsCount)
  * prediction drift (PSI + K-S on predicted probability)
  * target drift (label distribution, once actuals are known)
  * model quality: accuracy / precision / recall / F1 / ROC-AUC, reference vs current
  * data quality: missing-value share per feature
and renders a full interactive Evidently HTML report.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from evidently import BinaryClassification, DataDefinition, Dataset, Report
from evidently.metrics import DriftedColumnsCount, ValueDrift
from evidently.presets import ClassificationPreset, DataDriftPreset, DataSummaryPreset

from . import config
from .config import Thresholds
from .metrics_utils import classification_metrics


def _num(x) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, 5)


def _is_pvalue(method: str) -> bool:
    return "p_value" in method


def _data_definition(features: list[str]) -> DataDefinition:
    return DataDefinition(
        numerical_columns=features + [config.PREDICTION_PROBA],
        categorical_columns=[config.TARGET, config.PREDICTION],
        classification=[
            BinaryClassification(
                target=config.TARGET,
                prediction_labels=config.PREDICTION,
                prediction_probas=config.PREDICTION_PROBA,
                pos_label=1,
            )
        ],
    )


def _metric_values(snapshot) -> list[tuple[dict, object]]:
    return [(m["config"], m["value"]) for m in snapshot.dict()["metrics"]]


def analyze(reference: pd.DataFrame, current: pd.DataFrame, features: list[str],
            thresholds: Thresholds, html_path: Path | None = None) -> dict:
    definition = _data_definition(features)
    ref_ds = Dataset.from_pandas(reference, data_definition=definition)
    cur_ds = Dataset.from_pandas(current, data_definition=definition)

    # ---- 1. Numeric drift metrics (machine-readable) ---------------------------------
    metrics = [DriftedColumnsCount(columns=features, drift_share=thresholds.drift_share)]
    for col in features + [config.PREDICTION_PROBA]:
        metrics += [ValueDrift(column=col), ValueDrift(column=col, method="psi"),
                    ValueDrift(column=col, method="kl_div")]
    metrics.append(ValueDrift(column=config.TARGET))
    snapshot = Report(metrics).run(current_data=cur_ds, reference_data=ref_ds)

    per_col: dict[str, dict] = {}
    dataset_drift = {}
    for cfg, value in _metric_values(snapshot):
        if cfg["type"].endswith("DriftedColumnsCount"):
            dataset_drift = {"n_drifted": int(value["count"]), "share_drifted": _num(value["share"])}
            continue
        col, method, thr = cfg["column"], cfg["method"], cfg["threshold"]
        entry = per_col.setdefault(col, {})
        if method == "psi":
            entry["psi"] = _num(value)
        elif method == "kl_div":
            entry["kl_div"] = _num(value)
        else:
            entry["stattest"] = method
            entry["stattest_threshold"] = thr
            entry["drift_score"] = _num(value)
            entry["drifted"] = bool(value < thr) if _is_pvalue(method) else bool(value >= thr)

    # ---- 2. Per-feature summary with human-readable context --------------------------
    feature_rows = []
    for col in features:
        r, c = reference[col], current[col]
        ref_mean, cur_mean = r.mean(), c.mean()
        feature_rows.append({
            "feature": col,
            **per_col[col],
            "ref_mean": _num(ref_mean),
            "cur_mean": _num(cur_mean),
            "mean_shift_pct": _num((cur_mean - ref_mean) / ref_mean * 100) if ref_mean else None,
            "ref_missing_share": _num(r.isna().mean()),
            "cur_missing_share": _num(c.isna().mean()),
        })
    feature_rows.sort(key=lambda f: (f.get("psi") or 0), reverse=True)

    n_features = len(features)
    dataset_drift.update({
        "n_features": n_features,
        "detected": (dataset_drift["share_drifted"] or 0) >= thresholds.drift_share,
        "max_psi": feature_rows[0].get("psi"),
        "max_psi_feature": feature_rows[0]["feature"],
        "mean_psi": _num(np.mean([f.get("psi") or 0 for f in feature_rows])),
        "n_features_psi_above_threshold": sum((f.get("psi") or 0) >= thresholds.psi for f in feature_rows),
    })

    pred = per_col[config.PREDICTION_PROBA]
    prediction_drift = {
        "psi": pred.get("psi"),
        "kl_div": pred.get("kl_div"),
        "stattest": pred.get("stattest"),
        "drift_score": pred.get("drift_score"),
        "detected": (pred.get("psi") or 0) >= thresholds.prediction_psi,
        "ref_mean_proba": _num(reference[config.PREDICTION_PROBA].mean()),
        "cur_mean_proba": _num(current[config.PREDICTION_PROBA].mean()),
        "ref_positive_rate": _num(reference[config.PREDICTION].mean()),
        "cur_positive_rate": _num(current[config.PREDICTION].mean()),
    }

    tgt = per_col[config.TARGET]
    target_drift = {
        "stattest": tgt.get("stattest"),
        "p_value": tgt.get("drift_score"),
        "detected": tgt.get("drift_score") is not None
                    and bool(tgt["drift_score"] < thresholds.target_drift_pvalue),
        "ref_positive_rate": _num(reference[config.TARGET].mean()),
        "cur_positive_rate": _num(current[config.TARGET].mean()),
    }

    # ---- 3. Model quality (needs actuals) --------------------------------------------
    ref_perf = classification_metrics(reference[config.TARGET], reference[config.PREDICTION],
                                      reference[config.PREDICTION_PROBA])
    cur_perf = classification_metrics(current[config.TARGET], current[config.PREDICTION],
                                      current[config.PREDICTION_PROBA])
    delta = {k: _num(cur_perf[k] - ref_perf[k]) for k in ("accuracy", "precision", "recall", "f1", "roc_auc")
             if cur_perf.get(k) is not None and ref_perf.get(k) is not None}
    performance = {
        "reference": ref_perf,
        "current": cur_perf,
        "delta": delta,
        "degraded": (-(delta.get("accuracy") or 0) >= thresholds.accuracy_drop)
                    or (-(delta.get("recall") or 0) >= thresholds.recall_drop),
    }

    # ---- 4. Data quality -------------------------------------------------------------
    missing = current[features].isna().mean().sort_values(ascending=False)
    data_quality = {
        "max_missing_share": _num(missing.iloc[0]),
        "columns_with_missing": {k: _num(v) for k, v in missing[missing > 0].items()},
        "issue_detected": bool(missing.iloc[0] >= thresholds.missing_share),
    }

    # ---- 5. Full interactive HTML report ----------------------------------------------
    if html_path is not None:
        html_report = Report([
            DataDriftPreset(columns=features + [config.PREDICTION_PROBA, config.TARGET],
                            drift_share=thresholds.drift_share),
            ClassificationPreset(),
            DataSummaryPreset(),
        ])
        html_report.run(current_data=cur_ds, reference_data=ref_ds).save_html(str(html_path))

    return {
        "dataset_drift": dataset_drift,
        "prediction_drift": prediction_drift,
        "target_drift": target_drift,
        "performance": performance,
        "data_quality": data_quality,
        "feature_drift": feature_rows,
    }


def evaluate_alert(result: dict, thresholds: Thresholds) -> dict:
    """Turn raw metrics into an alert decision with human-readable reasons."""
    reasons = []
    dd, pd_, td = result["dataset_drift"], result["prediction_drift"], result["target_drift"]
    perf, dq = result["performance"], result["data_quality"]

    if dd["detected"]:
        reasons.append(f"Dataset drift: {dd['n_drifted']}/{dd['n_features']} features drifted "
                       f"({dd['share_drifted']:.0%} >= {thresholds.drift_share:.0%})")
    top = [f for f in result["feature_drift"] if (f.get("psi") or 0) >= thresholds.psi]
    if top:
        names = ", ".join(f"{f['feature']} (PSI {f['psi']:.2f})" for f in top[:3])
        reasons.append(f"{len(top)} feature(s) with PSI >= {thresholds.psi}: {names}")
    if pd_["detected"]:
        reasons.append(f"Prediction drift: PSI {pd_['psi']:.2f} on P(malignant); mean score "
                       f"{pd_['ref_mean_proba']:.2f} -> {pd_['cur_mean_proba']:.2f}")
    if td["detected"]:
        reasons.append(f"Target drift: malignant rate {td['ref_positive_rate']:.0%} -> "
                       f"{td['cur_positive_rate']:.0%} (p={td['p_value']:.1e})")
    if perf["degraded"]:
        d = perf["delta"]
        reasons.append(f"Performance degraded vs reference: accuracy {d.get('accuracy', 0):+.3f}, "
                       f"precision {d.get('precision', 0):+.3f}, recall {d.get('recall', 0):+.3f}")
    if dq["issue_detected"]:
        col, share = next(iter(dq["columns_with_missing"].items()))
        reasons.append(f"Data quality: {col} is {share:.0%} null")

    if perf["degraded"] or dq["issue_detected"]:
        severity = "critical"
    elif reasons:
        severity = "warning"
    else:
        severity = "ok"
    return {"triggered": bool(reasons), "severity": severity, "reasons": reasons}
