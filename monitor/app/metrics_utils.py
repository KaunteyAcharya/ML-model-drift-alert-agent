"""Small shared helpers for model-quality metrics."""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


def _clean(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


def classification_metrics(y_true, y_pred, y_proba) -> dict:
    y_true = np.asarray(y_true)
    has_both_classes = len(np.unique(y_true)) > 1
    return {
        "accuracy": _clean(accuracy_score(y_true, y_pred)),
        "precision": _clean(precision_score(y_true, y_pred, zero_division=0)),
        "recall": _clean(recall_score(y_true, y_pred, zero_division=0)),
        "f1": _clean(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": _clean(roc_auc_score(y_true, y_proba)) if has_both_classes else None,
        "positive_rate_actual": _clean(float(np.mean(y_true))),
        "positive_rate_predicted": _clean(float(np.mean(y_pred))),
    }
