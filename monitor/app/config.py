"""Central configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", BASE_DIR / "artifacts"))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", BASE_DIR / "reports"))

MODEL_PATH = ARTIFACTS_DIR / "model.joblib"
REFERENCE_PATH = ARTIFACTS_DIR / "reference.csv"
METADATA_PATH = ARTIFACTS_DIR / "model_metadata.json"

TARGET = "malignant"            # 1 = malignant (positive class), 0 = benign
PREDICTION = "prediction"       # predicted label
PREDICTION_PROBA = "prediction_proba"  # P(malignant)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Thresholds:
    """Alerting thresholds. n8n can override any of these per run in the POST body."""

    drift_share: float = field(default_factory=lambda: _env_float("DRIFT_SHARE_THRESHOLD", 0.30))
    psi: float = field(default_factory=lambda: _env_float("PSI_THRESHOLD", 0.25))
    prediction_psi: float = field(default_factory=lambda: _env_float("PREDICTION_PSI_THRESHOLD", 0.20))
    accuracy_drop: float = field(default_factory=lambda: _env_float("ACCURACY_DROP_THRESHOLD", 0.05))
    recall_drop: float = field(default_factory=lambda: _env_float("RECALL_DROP_THRESHOLD", 0.10))
    missing_share: float = field(default_factory=lambda: _env_float("MISSING_SHARE_THRESHOLD", 0.05))
    target_drift_pvalue: float = 0.05

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "Thresholds":
        t = cls()
        for key, value in (overrides or {}).items():
            if hasattr(t, key) and value is not None:
                setattr(t, key, float(value))
        return t


DATABASE_URL = os.getenv("MONITORING_DATABASE_URL", "")
REPORT_BASE_URL = os.getenv("REPORT_BASE_URL", "http://localhost:8010").rstrip("/")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "400"))
REPORT_RETENTION = int(os.getenv("REPORT_RETENTION", "100"))  # newest N HTML reports kept
