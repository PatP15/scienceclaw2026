"""In-silico "ground-truth world" for the agent benchmark (provenance: simulated).

A *world* is a hidden parameter set for the fermentation kinetic model. Agents never see it;
they only receive noisy observations (pH spot checks + a rise curve) of the schedules they run.

Everything here is deterministic given a seed. Observation noise uses common random numbers:
the noise draw depends on (world seed, schedule id, replicate index) and NOT on the policy,
so two policies that run the same schedule in the same world see the same data (paired design).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from fermentai.agents import schedules as S
from fermentai.fermentation import kinetics as K
from fermentai.fermentation.simulation import (
    TemperatureSchedule,
    _time_grid,
    integrate,
    sample_parameters,
)

PROVENANCE = "simulated"
BENCHMARK_LABEL = "IN-SILICO BENCHMARK — simulated worlds"

# Parameters the agents' models calibrate. Chosen because they set the acidification rate
# (mu_max_lab), its temperature optimum (t_opt_lab), the gas-production rate (qs_yeast), its
# temperature optimum (t_opt_yeast) and the peak rise height (expansion_max) — the
# temperature-dependence terms are only identifiable by running *different* temperatures.
FREE_PARAMS: tuple[str, ...] = ("mu_max_lab", "t_opt_lab", "qs_yeast", "t_opt_yeast", "expansion_max")

# Measurement design per experiment (mirrors PLAN §5/§7: one shared pH probe used for spot
# checks, a side camera for rise).
PH_TIMES_H: tuple[float, ...] = (4.0, 8.0)  # + the schedule end
RISE_EVERY_H = 1.0
NOISE_SD = {"ph": 0.05, "expansion_ratio": 0.05}  # pH units; V/V0 (camera height ratio)


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class World:
    seed: int
    true_params: dict[str, float]  # full parameter dict (hidden from agents)
    misspecified: bool  # True -> non-free parameters were also drawn (agent model is wrong)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def true_free(self) -> dict[str, float]:
        return {n: self.true_params[n] for n in FREE_PARAMS}


def draw_world(seed: int, *, misspecified: bool = False, free_params: Sequence[str] = FREE_PARAMS) -> World:
    """Hidden truth. Well-specified (default): only ``free_params`` are drawn from their configured
    distributions (``ParameterSpec.sample``), everything else stays nominal, so the agents' model
    class contains the truth. ``misspecified=True``: *all* sampled parameters are drawn
    (``simulation.sample_parameters``) while agents still fit only ``free_params``."""
    names = list(free_params) if not misspecified else None
    row = sample_parameters(1, seed=10_000 + int(seed), sample_names=names).iloc[0].to_dict()
    p = K.resolve_params({k: float(v) for k, v in row.items()})
    return World(seed=int(seed), true_params=p, misspecified=misspecified)


# ---------------------------------------------------------------------------
# Forward model (fast path: ODE solved only at the requested times)
# ---------------------------------------------------------------------------
def to_schedule(points: Sequence[Sequence[float]]) -> TemperatureSchedule:
    return TemperatureSchedule([(float(t), float(c)) for t, c in points], mode="piecewise_linear")


def observation_times(points: Sequence[Sequence[float]]) -> dict[str, np.ndarray]:
    end = float(points[-1][0])
    ph = sorted({*[t for t in PH_TIMES_H if t < end - 0.25], round(end, 3)})
    n = math.floor(end / RISE_EVERY_H + 1e-9)
    rise = sorted({*[RISE_EVERY_H * k for k in range(1, n + 1)], round(end, 3)})
    return {"ph": np.array(ph, float), "expansion_ratio": np.array(rise, float)}


def forward(points: Sequence[Sequence[float]], params: Mapping[str, float],
            times: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Noise-free model outputs (pH, expansion ratio) at the requested times.

    Uses ``fermentation.simulation.integrate`` + ``kinetics.ph_from_acids`` /
    ``expansion_from_state`` exactly as ``simulate`` and ``calibrate`` do, but evaluates only at
    the observation times (a few ms per call)."""
    sched = to_schedule(points)
    p = params if "mu_max_yeast" in params else K.resolve_params(params)
    t_all = np.unique(np.concatenate([[0.0], *[np.asarray(v, float) for v in times.values()]]))
    Y = integrate(sched, p, t_all)
    idx = {n: i for i, n in enumerate(K.STATE_NAMES)}
    out: dict[str, np.ndarray] = {}
    pos = {t: i for i, t in enumerate(t_all)}
    for var, tt in times.items():
        rows = [pos[t] for t in np.asarray(tt, float)]
        if var == "ph":
            out[var] = np.array([K.ph_from_acids(Y[i, idx["lactic_acid"]], Y[i, idx["acetic_acid"]], p) for i in rows])
        elif var == "expansion_ratio":
            T = np.asarray(sched.temperature_at(t_all[rows]), float).reshape(-1)
            out[var] = np.array([K.expansion_from_state(Y[i, idx["gas_retained"]], Y[i, idx["gluten_integrity"]],
                                                        float(Ti), p)[0] for i, Ti in zip(rows, T)])
        else:
            raise KeyError(var)
    return out


def _noise_seed(world_seed: int, schedule_id: str, replicate: int) -> int:
    h = hashlib.sha256(f"{world_seed}|{schedule_id}|{replicate}".encode()).hexdigest()
    return int(h[:12], 16)


def run_experiment(world: World, schedule_id: str, points: Sequence[Sequence[float]], replicate: int = 0
                   ) -> dict[str, Any]:
    """Run one in-silico experiment: true model + Gaussian measurement noise."""
    times = observation_times(points)
    truth = forward(points, world.true_params, times)
    rng = np.random.default_rng(_noise_seed(world.seed, schedule_id, replicate))
    obs = {v: (truth[v] + rng.normal(0.0, NOISE_SD[v], size=len(truth[v]))) for v in ("ph", "expansion_ratio")}
    return {
        "schedule_id": schedule_id,
        "points": [list(map(float, p)) for p in points],
        "replicate": replicate,
        "times": {k: v.tolist() for k, v in times.items()},
        "observed": {k: v.tolist() for k, v in obs.items()},
        "provenance": PROVENANCE,
    }


# ---------------------------------------------------------------------------
# Held-out scenarios (validated, NOT in the agents' library and not near-identical to any library
# schedule -> never run by any policy)
# ---------------------------------------------------------------------------
HELD_OUT: dict[str, tuple[tuple[float, float], ...]] = {
    "ho_steady_21_16h": S.steady_schedule(21.0, 16.0),
    "ho_steady_23_10h": S.steady_schedule(23.0, 10.0),
    "ho_steady_25_14h": S.steady_schedule(25.0, 14.0),
    "ho_warm_28_6h_cool_18": S._steps((28.0, 6.0), (18.0, 6.0)),
    "ho_cool_19_6h_warm_25": S._steps((19.0, 6.0), (25.0, 6.0)),
    "ho_hot_31_2h_22": S._steps((31.0, 2.0), (22.0, 9.0), ramp_h=1.0),
}
HELD_OUT_DT_H = 0.5


def held_out_times(points) -> dict[str, np.ndarray]:
    end = float(points[-1][0])
    g = _time_grid(end, HELD_OUT_DT_H)[1:]
    return {"ph": g, "expansion_ratio": g}


@lru_cache(maxsize=4096)
def _truth_cached(key: tuple) -> tuple:
    params_items, name = key
    pts = HELD_OUT[name]
    out = forward(pts, dict(params_items), held_out_times(pts))
    return tuple(out["ph"]), tuple(out["expansion_ratio"])


def held_out_rmse(params: Mapping[str, float], true_params: Mapping[str, float]) -> dict[str, float]:
    """RMSE of predicted vs. true (noise-free) pH and expansion over all held-out schedules."""
    se = {"ph": [], "expansion_ratio": []}
    p = K.resolve_params(params)
    for name, pts in HELD_OUT.items():
        tr_ph, tr_ex = _truth_cached((tuple(sorted(true_params.items())), name))
        pr = forward(pts, p, held_out_times(pts))
        se["ph"].extend((pr["ph"] - np.array(tr_ph)) ** 2)
        se["expansion_ratio"].extend((pr["expansion_ratio"] - np.array(tr_ex)) ** 2)
    return {"ph": float(np.sqrt(np.mean(se["ph"]))), "expansion_ratio": float(np.sqrt(np.mean(se["expansion_ratio"])))}


# ---------------------------------------------------------------------------
# Schedule similarity / redundancy
# ---------------------------------------------------------------------------
NEAR_IDENTICAL_RMS_C = 1.0  # temperature-profile RMS difference (°C) at or below which ...
NEAR_IDENTICAL_DURATION_H = 1.0  # ... and duration difference (h) at or below which two runs are redundant


def profile_rms_c(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]], dt: float = 0.1) -> float:
    """RMS temperature difference over the common window (0 .. shorter duration)."""
    end = min(float(a[-1][0]), float(b[-1][0]))
    t = np.arange(0.0, end + 1e-9, dt)
    ta = np.interp(t, [p[0] for p in a], [p[1] for p in a])
    tb = np.interp(t, [p[0] for p in b], [p[1] for p in b])
    return float(np.sqrt(np.mean((ta - tb) ** 2)))


def near_identical(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> bool:
    if abs(float(a[-1][0]) - float(b[-1][0])) > NEAR_IDENTICAL_DURATION_H + 1e-9:
        return False
    return profile_rms_c(a, b) <= NEAR_IDENTICAL_RMS_C + 1e-9


def redundancy(round_choices: Sequence[tuple[str, Sequence[Sequence[float]]]],
               history: Sequence[tuple[str, Sequence[Sequence[float]]]]) -> dict[str, int]:
    """Count redundant experiments in one round.

    ``within_round``: experiments in this round that duplicate (same id or near-identical profile)
    an *earlier-listed* experiment of the same round (A vs B -> 0 or 1 for two chambers).
    ``repeats``: experiments in this round that duplicate any experiment of earlier rounds.
    ``redundant``: experiments flagged by either rule (each experiment counted at most once).
    """
    within = repeats = redundant = 0
    for i, (sid, pts) in enumerate(round_choices):
        dup_round = any(sid == s2 or near_identical(pts, p2) for s2, p2 in round_choices[:i])
        dup_hist = any(sid == s2 or near_identical(pts, p2) for s2, p2 in history)
        within += dup_round
        repeats += dup_hist
        redundant += dup_round or dup_hist
    return {"within_round": within, "repeats": repeats, "redundant": redundant}
