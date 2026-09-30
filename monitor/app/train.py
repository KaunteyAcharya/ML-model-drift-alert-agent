"""Train the stand-in "production model" and freeze a reference dataset.

Dataset: UCI Breast Cancer Wisconsin (Diagnostic), bundled with scikit-learn, so
the build works offline and is fully reproducible.

Outputs (in ARTIFACTS_DIR):
  model.joblib         - sklearn Pipeline (imputer -> random forest)
  reference.csv        - held-out rows + model predictions = the "known good" baseline
  model_metadata.json  - version, features, holdout metrics

Run:  python -m app.train
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import joblib
import pandas as pd
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from . import config
from .metrics_utils import classification_metrics

RANDOM_STATE = 42


def load_dataset() -> pd.DataFrame:
    raw = load_breast_cancer(as_frame=True)
    df = raw.frame.copy()
    df.columns = [c.replace(" ", "_") for c in df.columns]
    # sklearn encodes 0 = malignant, 1 = benign. Flip it so the positive class is the
    # one the business cares about catching.
    df[config.TARGET] = (df.pop("target") == 0).astype(int)
    return df


def main() -> None:
    config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    df = load_dataset()
    features = [c for c in df.columns if c != config.TARGET]

    train_df, ref_df = train_test_split(
        df, test_size=0.4, random_state=RANDOM_STATE, stratify=df[config.TARGET]
    )

    model = Pipeline(
        [
            # Production pipelines must survive missing values; median imputation is the
            # "silent" fix that lets data-quality bugs slip through - which the monitor catches.
            ("imputer", SimpleImputer(strategy="median")),
            ("clf", RandomForestClassifier(n_estimators=300, max_depth=6, random_state=RANDOM_STATE)),
        ]
    )
    model.fit(train_df[features], train_df[config.TARGET])

    reference = ref_df.copy()
    reference[config.PREDICTION_PROBA] = model.predict_proba(reference[features])[:, 1]
    reference[config.PREDICTION] = (reference[config.PREDICTION_PROBA] >= 0.5).astype(int)
    reference.to_csv(config.REFERENCE_PATH, index=False)

    holdout = classification_metrics(
        reference[config.TARGET], reference[config.PREDICTION], reference[config.PREDICTION_PROBA]
    )
    version = "rf-bcw-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    metadata = {
        "model_version": version,
        "algorithm": "RandomForestClassifier(n_estimators=300, max_depth=6)",
        "dataset": "UCI Breast Cancer Wisconsin (Diagnostic) via sklearn",
        "positive_class": "malignant",
        "features": features,
        "n_train": len(train_df),
        "n_reference": len(reference),
        "holdout_metrics": holdout,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    joblib.dump(model, config.MODEL_PATH)
    config.METADATA_PATH.write_text(json.dumps(metadata, indent=2))
    print(json.dumps({"status": "trained", **metadata}, indent=2))


if __name__ == "__main__":
    main()
