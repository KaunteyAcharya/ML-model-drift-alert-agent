"""Simulate a batch of "production" traffic, optionally with a known drift pattern.

Each scenario mimics a real-world failure mode. The monitor (and the LLM) never
sees the scenario name - it has to infer the root cause from the metrics alone.
The ground truth is kept in the report under `simulation` so you can check how
well the LLM's explanation matched reality.

Scenarios
---------
none         Healthy traffic: resampled from the reference distribution + small noise.
calibration  Imaging software update: the segmentation step traces cell-nucleus
             boundaries larger (radius/perimeter/area inflate) and texture readings
             rise. Labels are unchanged, so the model silently gets worse.
population   Patient-mix shift: a new referral clinic sends many more malignant
             cases. Feature AND target distributions move; the model may still be fine.
data_quality Upstream pipeline bug: `mean_area` arrives in the wrong unit (/100)
             and `worst_concavity` is often null (silently median-imputed).
auto         A 12-step storyline that cycles through all of the above, so repeated
             scheduled runs show a realistic drift timeline in Grafana.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config

SCENARIOS = ("none", "calibration", "population", "data_quality", "auto")

TEXTURE_COLS = ["mean_texture", "worst_texture"]
LENGTH_COLS = ["mean_radius", "mean_perimeter", "worst_radius", "worst_perimeter"]
AREA_COLS = ["mean_area", "worst_area"]

# step -> (scenario, severity). Steps 0-2 healthy, 3-7 gradual calibration drift,
# 8-9 population shift, 10-11 data-quality incident, then the cycle repeats.
AUTO_STORYLINE = [
    ("none", 0.0), ("none", 0.0), ("none", 0.0),
    ("calibration", 0.2), ("calibration", 0.4), ("calibration", 0.6),
    ("calibration", 0.8), ("calibration", 1.0),
    ("population", 0.6), ("population", 1.0),
    ("data_quality", 0.7), ("data_quality", 1.0),
]

DESCRIPTIONS = {
    "none": "Healthy traffic sampled from the reference distribution.",
    "calibration": "Imaging software update traces nuclei larger: radius/perimeter/area and texture inflated; labels unchanged.",
    "population": "Referral mix changed: far more malignant cases arriving (prior/covariate shift).",
    "data_quality": "Upstream bug: mean_area in wrong unit (/100) and worst_concavity frequently null.",
}


@dataclass
class Simulation:
    scenario: str
    severity: float
    step: int | None
    description: str

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "severity": round(self.severity, 2),
            "auto_step": self.step,
            "ground_truth": self.description,
        }


def resolve_scenario(scenario: str, severity: float | None, run_index: int) -> Simulation:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose one of {SCENARIOS}.")
    if scenario == "auto":
        step = run_index % len(AUTO_STORYLINE)
        name, sev = AUTO_STORYLINE[step]
        return Simulation(name, sev, step, DESCRIPTIONS[name])
    sev = 0.0 if scenario == "none" else (1.0 if severity is None else float(np.clip(severity, 0, 1)))
    return Simulation(scenario, sev, None, DESCRIPTIONS[scenario])


def generate_batch(reference: pd.DataFrame, features: list[str], sim: Simulation,
                   n: int, seed: int | None = None) -> pd.DataFrame:
    """Return `n` rows of raw features + ground-truth label (no predictions yet)."""
    rng = np.random.default_rng(seed)
    base = reference[features + [config.TARGET]]

    if sim.scenario == "population" and sim.severity > 0:
        # Oversample malignant cases: prevalence moves from ~37% towards ~37% + 45%*severity.
        target_rate = min(0.95, base[config.TARGET].mean() + 0.45 * sim.severity)
        is_pos = base[config.TARGET] == 1
        weights = np.where(is_pos, target_rate / is_pos.mean(), (1 - target_rate) / (~is_pos).mean())
        idx = rng.choice(len(base), size=n, replace=True, p=weights / weights.sum())
    else:
        idx = rng.choice(len(base), size=n, replace=True)

    batch = base.iloc[idx].reset_index(drop=True).copy()

    # Small multiplicative jitter so resampled rows are not exact duplicates.
    stds = reference[features].std()
    noise = rng.normal(0, 0.03, size=(n, len(features))) * stds.values
    batch[features] = np.clip(batch[features].values + noise, 0, None)

    s = sim.severity
    if sim.scenario == "calibration" and s > 0:
        scale = 1 + 0.25 * s                   # boundaries traced up to 25% larger
        for col in LENGTH_COLS:
            batch[col] *= scale
        for col in AREA_COLS:
            batch[col] *= scale ** 2           # area grows with the square of the radius
        for col in TEXTURE_COLS:
            batch[col] *= 1 + 0.30 * s
    elif sim.scenario == "data_quality" and s > 0:
        unit_bug = rng.random(n) < 0.8 * s
        batch.loc[unit_bug, "mean_area"] = batch.loc[unit_bug, "mean_area"] / 100.0
        null_mask = rng.random(n) < 0.35 * s
        batch.loc[null_mask, "worst_concavity"] = np.nan

    return batch
