"""Command-line entry point.

Examples (inside the container:  docker compose exec drift-monitor <command>):
  python -m app.cli run                              # next step of the auto storyline
  python -m app.cli run --scenario calibration --severity 0.8
  python -m app.cli run --scenario none --no-db      # no Postgres needed
  python -m app.cli backfill --steps 12              # 12 days of history for Grafana
The full JSON report is printed to stdout (add --summary for a short version).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from .pipeline import run_monitoring
from .simulate import SCENARIOS


def _short(r: dict) -> dict:
    return {
        "run_id": r["run_id"],
        "run_ts": r["run_ts"],
        "simulation": f"{r['simulation']['scenario']} @ {r['simulation']['severity']}",
        "share_drifted": r["dataset_drift"]["share_drifted"],
        "max_psi": r["dataset_drift"]["max_psi"],
        "prediction_psi": r["prediction_drift"]["psi"],
        "target_drift": r["target_drift"]["detected"],
        "accuracy": f"{r['performance']['reference']['accuracy']} -> {r['performance']['current']['accuracy']}",
        "alert": r["alert"]["severity"],
        "reasons": r["alert"]["reasons"],
        "report_url": r["report_url"],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="drift-monitor")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run one monitoring cycle and print the JSON report")
    run.add_argument("--scenario", default="auto", choices=SCENARIOS)
    run.add_argument("--severity", type=float, default=None, help="0..1 (ignored for auto/none)")
    run.add_argument("--batch-size", type=int, default=None)
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--no-db", action="store_true", help="Skip Postgres logging")
    run.add_argument("--summary", action="store_true", help="Print a short summary instead")

    bf = sub.add_parser("backfill", help="Generate historical runs (auto storyline) for dashboards")
    bf.add_argument("--steps", type=int, default=12)
    bf.add_argument("--interval-hours", type=float, default=24)

    args = p.parse_args(argv)

    if args.cmd == "run":
        report = run_monitoring(args.scenario, args.severity, batch_size=args.batch_size,
                                seed=args.seed, use_db=False if args.no_db else None)
        print(json.dumps(_short(report) if args.summary else report, indent=2, default=str))
        return 0

    start = datetime.now(timezone.utc) - timedelta(hours=args.interval_hours * args.steps)
    for i in range(args.steps):
        report = run_monitoring("auto", run_ts=start + timedelta(hours=args.interval_hours * i), seed=i)
        print(json.dumps(_short(report), default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
