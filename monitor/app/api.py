"""HTTP API that n8n calls, plus static hosting for the Evidently HTML reports.

  GET  /health                     liveness + model info
  POST /run                        run one monitoring cycle -> full JSON report
  POST /runs/{run_id}/summary      n8n writes the LLM explanation back for the audit trail
  GET  /runs?limit=20              recent run history (from Postgres)
  GET  /reports/{run_id}.html      interactive Evidently report (linked from Slack)
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .pipeline import load_artifacts, run_monitoring
from .simulate import SCENARIOS

log = logging.getLogger("drift-monitor")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_artifacts()
    if db.enabled():
        db.init_schema()
        log.info("Postgres schema ready")
    yield


app = FastAPI(title="Drift Monitor", version="1.0.0", lifespan=lifespan)
config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=config.REPORTS_DIR), name="reports")
_run_lock = threading.Lock()


class RunRequest(BaseModel):
    scenario: str = Field("auto", description=f"One of {SCENARIOS}")
    severity: float | None = Field(None, ge=0, le=1)
    batch_size: int | None = Field(None, ge=50, le=5000)
    thresholds: dict[str, float] | None = None


class SummaryRequest(BaseModel):
    summary: str
    llm_model: str | None = None
    slack_notified: bool = False


@app.get("/health")
def health() -> dict:
    _, reference, meta = load_artifacts()
    return {"status": "ok", "model_version": meta["model_version"],
            "n_reference": len(reference), "database": db.enabled()}


@app.post("/run")
def run(req: RunRequest) -> dict:
    if req.scenario not in SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {SCENARIOS}")
    # Sync endpoint -> runs in FastAPI's threadpool. The lock stops overlapping
    # schedules from double-counting the auto storyline.
    if not _run_lock.acquire(timeout=300):
        raise HTTPException(409, "Another monitoring run is still in progress")
    try:
        return run_monitoring(req.scenario, req.severity, req.thresholds, req.batch_size)
    finally:
        _run_lock.release()


@app.post("/runs/{run_id}/summary")
def save_summary(run_id: str, req: SummaryRequest) -> dict:
    if not db.enabled():
        return {"saved": False, "reason": "database disabled"}
    if not db.save_summary(run_id, req.summary, req.llm_model, req.slack_notified):
        raise HTTPException(404, f"run {run_id} not found")
    return {"saved": True, "run_id": run_id}


@app.get("/runs")
def runs(limit: int = 20) -> list[dict]:
    if not db.enabled():
        return []
    return db.recent_runs(min(limit, 200))
