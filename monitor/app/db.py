"""PostgreSQL persistence: prediction log, drift-run history, per-feature drift.

The schema is owned by the app and created idempotently on startup, so the
database container only has to provide an empty `monitoring` database.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT        NOT NULL,
    predicted_at     TIMESTAMPTZ NOT NULL,
    model_version    TEXT        NOT NULL,
    features         JSONB       NOT NULL,
    prediction       SMALLINT    NOT NULL,
    prediction_proba DOUBLE PRECISION NOT NULL,
    actual           SMALLINT,              -- ground truth, back-filled when labels arrive
    actual_logged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_predictions_run ON predictions (run_id);
CREATE INDEX IF NOT EXISTS idx_predictions_time ON predictions (predicted_at);

CREATE TABLE IF NOT EXISTS drift_runs (
    run_id                  TEXT PRIMARY KEY,
    run_ts                  TIMESTAMPTZ NOT NULL,
    model_version           TEXT        NOT NULL,
    n_reference             INT,
    n_current               INT,
    -- data drift
    dataset_drift           BOOLEAN,
    share_drifted_features  DOUBLE PRECISION,
    n_drifted_features      INT,
    max_psi                 DOUBLE PRECISION,
    max_psi_feature         TEXT,
    mean_psi                DOUBLE PRECISION,
    -- prediction drift (model output) and target drift (actual outcomes)
    prediction_psi          DOUBLE PRECISION,
    prediction_drift        BOOLEAN,
    pred_rate_ref           DOUBLE PRECISION,
    pred_rate_cur           DOUBLE PRECISION,
    target_drift            BOOLEAN,
    target_drift_p_value    DOUBLE PRECISION,
    target_rate_ref         DOUBLE PRECISION,
    target_rate_cur         DOUBLE PRECISION,
    -- model quality
    accuracy_ref            DOUBLE PRECISION,
    accuracy_cur            DOUBLE PRECISION,
    precision_ref           DOUBLE PRECISION,
    precision_cur           DOUBLE PRECISION,
    recall_ref              DOUBLE PRECISION,
    recall_cur              DOUBLE PRECISION,
    f1_ref                  DOUBLE PRECISION,
    f1_cur                  DOUBLE PRECISION,
    roc_auc_ref             DOUBLE PRECISION,
    roc_auc_cur             DOUBLE PRECISION,
    -- data quality
    max_missing_share       DOUBLE PRECISION,
    -- alerting
    alert_triggered         BOOLEAN,
    alert_severity          TEXT,
    alert_reasons           TEXT[],
    thresholds              JSONB,
    report_url              TEXT,
    -- ground truth of the simulation: for evaluating the LLM only, never sent to it
    sim_scenario            TEXT,
    sim_severity            DOUBLE PRECISION,
    -- written back by n8n after Ollama explains the alert
    llm_summary             TEXT,
    llm_model               TEXT,
    llm_summary_at          TIMESTAMPTZ,
    slack_notified_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_drift_runs_ts ON drift_runs (run_ts);

CREATE TABLE IF NOT EXISTS feature_drift (
    run_id             TEXT REFERENCES drift_runs (run_id) ON DELETE CASCADE,
    run_ts             TIMESTAMPTZ NOT NULL,
    feature            TEXT NOT NULL,
    stattest           TEXT,
    drift_score        DOUBLE PRECISION,
    drifted            BOOLEAN,
    psi                DOUBLE PRECISION,
    kl_div             DOUBLE PRECISION,
    ref_mean           DOUBLE PRECISION,
    cur_mean           DOUBLE PRECISION,
    mean_shift_pct     DOUBLE PRECISION,
    cur_missing_share  DOUBLE PRECISION,
    PRIMARY KEY (run_id, feature)
);
CREATE INDEX IF NOT EXISTS idx_feature_drift_ts ON feature_drift (run_ts);
"""


def enabled() -> bool:
    return bool(config.DATABASE_URL)


@contextmanager
def connect():
    with psycopg.connect(config.DATABASE_URL, connect_timeout=10) as conn:
        yield conn


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_SQL)


def count_runs() -> int:
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM drift_runs").fetchone()[0]


def log_predictions(run_id: str, ts: datetime, model_version: str, batch: pd.DataFrame,
                    features: list[str]) -> None:
    feature_records = json.loads(batch[features].to_json(orient="records"))  # NaN -> null
    rows = [
        (run_id, ts, model_version, Jsonb(f), int(p), float(pp), int(a), ts)
        for f, p, pp, a in zip(feature_records, batch[config.PREDICTION],
                               batch[config.PREDICTION_PROBA], batch[config.TARGET])
    ]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO predictions (run_id, predicted_at, model_version, features, prediction,
                                        prediction_proba, actual, actual_logged_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )


def log_run(report: dict) -> None:
    dd, pdrift, td = report["dataset_drift"], report["prediction_drift"], report["target_drift"]
    perf, dq, alert = report["performance"], report["data_quality"], report["alert"]
    ref, cur = perf["reference"], perf["current"]
    with connect() as conn, conn.cursor() as c:
        row = {
            "run_id": report["run_id"], "run_ts": report["run_ts"],
            "model_version": report["model_version"],
            "n_reference": report["data"]["n_reference"], "n_current": report["data"]["n_current"],
            "dataset_drift": dd["detected"], "share_drifted_features": dd["share_drifted"],
            "n_drifted_features": dd["n_drifted"], "max_psi": dd["max_psi"],
            "max_psi_feature": dd["max_psi_feature"], "mean_psi": dd["mean_psi"],
            "prediction_psi": pdrift["psi"], "prediction_drift": pdrift["detected"],
            "pred_rate_ref": pdrift["ref_positive_rate"], "pred_rate_cur": pdrift["cur_positive_rate"],
            "target_drift": td["detected"], "target_drift_p_value": td["p_value"],
            "target_rate_ref": td["ref_positive_rate"], "target_rate_cur": td["cur_positive_rate"],
            **{f"{m}_ref": ref[m] for m in ("accuracy", "precision", "recall", "f1", "roc_auc")},
            **{f"{m}_cur": cur[m] for m in ("accuracy", "precision", "recall", "f1", "roc_auc")},
            "max_missing_share": dq["max_missing_share"],
            "alert_triggered": alert["triggered"], "alert_severity": alert["severity"],
            "alert_reasons": list(alert["reasons"]), "thresholds": Jsonb(report["thresholds"]),
            "report_url": report["report_url"],
            "sim_scenario": report["simulation"]["scenario"],
            "sim_severity": report["simulation"]["severity"],
        }
        cols = ", ".join(row)
        params = ", ".join(f"%({k})s" for k in row)
        c.execute(f"INSERT INTO drift_runs ({cols}) VALUES ({params})", row)
        c.executemany(
            """INSERT INTO feature_drift (run_id, run_ts, feature, stattest, drift_score, drifted,
                                          psi, kl_div, ref_mean, cur_mean, mean_shift_pct,
                                          cur_missing_share)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (report["run_id"], report["run_ts"], f["feature"], f.get("stattest"),
                 f.get("drift_score"), f.get("drifted"), f.get("psi"), f.get("kl_div"),
                 f["ref_mean"], f["cur_mean"], f["mean_shift_pct"], f["cur_missing_share"])
                for f in report["feature_drift"]
            ],
        )


def save_summary(run_id: str, summary: str, llm_model: str | None, slack_notified: bool) -> bool:
    with connect() as conn:
        cur = conn.execute(
            """UPDATE drift_runs
                  SET llm_summary = %s, llm_model = %s, llm_summary_at = now(),
                      slack_notified_at = CASE WHEN %s THEN now() ELSE slack_notified_at END
                WHERE run_id = %s""",
            (summary, llm_model, slack_notified, run_id),
        )
        return cur.rowcount == 1


def recent_runs(limit: int = 20) -> list[dict]:
    with connect() as conn, conn.cursor(row_factory=dict_row) as c:
        c.execute(
            """SELECT run_id, run_ts, share_drifted_features, max_psi, accuracy_cur,
                      alert_triggered, alert_severity, report_url, llm_summary
                 FROM drift_runs ORDER BY run_ts DESC LIMIT %s""",
            (limit,),
        )
        return c.fetchall()
