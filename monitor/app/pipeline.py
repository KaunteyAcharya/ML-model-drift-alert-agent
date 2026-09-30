"""One monitoring cycle: simulate traffic -> score -> log -> detect drift -> report.

This is the single entry point used by both the CLI (`python -m app.cli run`) and
the HTTP API that n8n calls (`POST /run`).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from functools import lru_cache

import joblib
import pandas as pd

from . import config, db
from .config import Thresholds
from .drift import analyze, evaluate_alert
from .simulate import generate_batch, resolve_scenario

LOCAL_STATE = config.REPORTS_DIR / ".run_counter"


@lru_cache(maxsize=1)
def load_artifacts():
    if not config.MODEL_PATH.exists():
        raise FileNotFoundError("Model not found - run `python -m app.train` first.")
    model = joblib.load(config.MODEL_PATH)
    reference = pd.read_csv(config.REFERENCE_PATH)
    metadata = json.loads(config.METADATA_PATH.read_text())
    return model, reference, metadata


def _next_run_index(use_db: bool) -> int:
    if use_db:
        return db.count_runs()
    n = int(LOCAL_STATE.read_text()) if LOCAL_STATE.exists() else 0
    LOCAL_STATE.write_text(str(n + 1))
    return n


def run_monitoring(scenario: str = "auto", severity: float | None = None,
                   thresholds: dict | None = None, batch_size: int | None = None,
                   run_ts: datetime | None = None, seed: int | None = None,
                   use_db: bool | None = None) -> dict:
    use_db = db.enabled() if use_db is None else use_db
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    model, reference, metadata = load_artifacts()
    features = metadata["features"]
    thr = Thresholds.from_overrides(thresholds)

    if use_db:
        db.init_schema()
    run_index = _next_run_index(use_db)
    sim = resolve_scenario(scenario, severity, run_index)

    run_ts = run_ts or datetime.now(timezone.utc)
    run_id = f"run_{run_ts:%Y%m%dT%H%M%SZ}_{secrets.token_hex(2)}"

    # 1. "Production traffic" arrives and the model scores it.
    batch = generate_batch(reference, features, sim, batch_size or config.BATCH_SIZE, seed)
    batch[config.PREDICTION_PROBA] = model.predict_proba(batch[features])[:, 1]
    batch[config.PREDICTION] = (batch[config.PREDICTION_PROBA] >= 0.5).astype(int)

    # 2. Log predictions + actuals (in real life actuals are joined in later).
    if use_db:
        db.log_predictions(run_id, run_ts, metadata["model_version"], batch, features)

    # 3. Evidently drift analysis + HTML report.
    html_path = config.REPORTS_DIR / f"{run_id}.html"
    result = analyze(reference, batch, features, thr, html_path=html_path)
    alert = evaluate_alert(result, thr)

    report = {
        "run_id": run_id,
        "run_ts": run_ts.isoformat(),
        "model_version": metadata["model_version"],
        "model": {
            "name": "Breast-cancer biopsy classifier",
            "version": metadata["model_version"],
            "algorithm": metadata["algorithm"],
            "dataset": metadata["dataset"],
            "positive_class": metadata["positive_class"],
        },
        "data": {"n_reference": len(reference), "n_current": len(batch), "run_index": run_index},
        "alert": alert,
        "report_url": f"{config.REPORT_BASE_URL}/reports/{run_id}.html",
        "thresholds": thr.__dict__,
        **result,
        # Ground truth of the simulation - for evaluating the LLM, never sent to it.
        "simulation": sim.as_dict(),
    }
    report["top_drifted_features"] = [
        {k: f.get(k) for k in ("feature", "psi", "kl_div", "stattest", "drift_score", "drifted",
                               "ref_mean", "cur_mean", "mean_shift_pct", "cur_missing_share")}
        for f in result["feature_drift"][:5]
    ]

    (config.REPORTS_DIR / f"{run_id}.json").write_text(json.dumps(report, indent=2, default=str))
    if use_db:
        db.log_run(report)
    _prune_reports()
    return report


def _prune_reports() -> None:
    """Evidently HTML reports embed their JS (~6 MB each): keep only the newest N runs."""
    keep = config.REPORT_RETENTION
    htmls = sorted(config.REPORTS_DIR.glob("run_*.html"), key=lambda p: p.stat().st_mtime)
    for old in htmls[:-keep] if len(htmls) > keep else []:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
