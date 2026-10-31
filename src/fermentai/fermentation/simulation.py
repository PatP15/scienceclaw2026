"""Time-domain simulation of the sourdough kinetic model (CPU, SciPy). Provenance: "simulated".

Stable public API
-----------------
``TemperatureSchedule(breakpoints=[(time_h, temp_c), ...], mode="piecewise_constant")``
    ``mode`` is ``"piecewise_constant"`` (step: hold each value until the next breakpoint) or
    ``"piecewise_linear"`` (linear ramps between breakpoints). Before the first / after the last
    breakpoint the end value is held. Methods: ``temperature_at(t_h)`` (scalar or array),
    ``constant(temp_c)``, ``from_dict(d)`` / ``to_dict()`` (JSON-friendly:
    ``{"mode": ..., "breakpoints": [[t, T], ...]}``).

``simulate(schedule, params=None, t_end_h=16.0, dt_h=0.05, initial=None) -> pandas.DataFrame``
    ``params``: partial ``{name: value}`` overriding configs/kinetics.yaml (initial conditions
    such as ``substrate_0`` are parameters too). ``initial``: overrides by state name
    (``fermentai.fermentation.kinetics.STATE_NAMES``). Columns:
    time_h, temperature_c, substrate [g/kg], yeast [rel], lab [rel], lactic_acid [g/kg],
    acetic_acid [g/kg], co2_cumulative [g CO2/kg dough, MODELLED], ph, expansion_ratio [V/V0],
    plus extras: gas_retained, co2_retained (= gas_retained; gas held in dough, saturating),
    gluten_weakening_index [0-1], gluten_integrity, expansion_capacity, tta_ml_per_10g,
    fermentation_quotient, overproof_risk, provenance ("simulated").
    ``df.attrs`` holds provenance, schedule, params and units.

``simulate_with_uncertainty(schedule, n_samples=64, seed=0, params=None, t_end_h=16.0,
dt_h=0.1, initial=None, quantiles=(0.05, 0.5, 0.95), sample_names=None) -> dict``
    Monte-Carlo over parameters sampled within their configured ranges. Returns
    ``{"time_h": ndarray, "outputs": {var: {"p05", "median", "p95"} ndarrays},
    "envelope": DataFrame, "param_samples": DataFrame, "nominal": DataFrame, "n_samples",
    "n_failed", "seed", "quantiles", "provenance": "simulated"}``.

``summarize_prediction(df) -> dict``
    Key numbers (final pH, pH at 12 h, peak expansion & time, time to 2x, overproof onset...).

``summarize_for_morphology(df, t_end_h=None) -> dict``
    Scalars for the crumb-morphology mapper (co2_cumulative/retained, expansion final/peak,
    peak time, max expansion rate, overproof flag/onset, final pH, gluten_weakening_index ...).

``load_scenario(chamber_id) / load_scenarios() / load_experiments()`` (from ``scenarios``)
    Chamber A/B schedules and metadata from configs/experiments.yaml.

Notes: model CO2 is *modelled production*, not a sensor value; the BME680 gas-resistance
channel does not measure CO2 and is never mapped onto it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

from . import kinetics as K

PROVENANCE = K.PROVENANCE

OUTPUT_COLUMNS: tuple[str, ...] = (
    "time_h",
    "temperature_c",
    "substrate",
    "yeast",
    "lab",
    "lactic_acid",
    "acetic_acid",
    "co2_cumulative",
    "ph",
    "expansion_ratio",
)
EXTRA_COLUMNS: tuple[str, ...] = (
    "gas_retained",
    "co2_retained",
    "gluten_weakening_index",
    "gluten_integrity",
    "expansion_capacity",
    "tta_ml_per_10g",
    "fermentation_quotient",
    "overproof_risk",
)
UNCERTAINTY_OUTPUTS: tuple[str, ...] = (
    "temperature_c",
    "substrate",
    "lactic_acid",
    "acetic_acid",
    "co2_cumulative",
    "ph",
    "expansion_ratio",
    "tta_ml_per_10g",
    "fermentation_quotient",
    "overproof_risk",
)
UNITS: dict[str, str] = {
    "time_h": "h",
    "temperature_c": "degC",
    "substrate": "g hexose-eq / kg dough",
    "yeast": "relative to inoculum",
    "lab": "relative to inoculum",
    "lactic_acid": "g / kg dough",
    "acetic_acid": "g / kg dough",
    "co2_cumulative": "g CO2 / kg dough (modelled production, not a sensor value)",
    "ph": "pH",
    "expansion_ratio": "V/V0 (= relative height in a straight-sided vessel)",
    "gas_retained": "g CO2 / kg dough held as gas",
    "gluten_integrity": "0-1",
    "expansion_capacity": "V/V0",
    "tta_ml_per_10g": "mL 0.1 N NaOH per 10 g dough to pH 8.5",
    "fermentation_quotient": "mol lactic / mol acetic",
    "overproof_risk": "0-1 heuristic",
    "co2_retained": "g CO2 / kg dough held as gas in dough cells (= gas_retained; saturates at capacity)",
    "gluten_weakening_index": "0-1, cumulative temperature-weighted acid exposure (phenomenological)",
}

Mode = Literal["piecewise_constant", "piecewise_linear"]


# ---------------------------------------------------------------------------------------------
# Temperature schedule
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TemperatureSchedule:
    """Dough temperature schedule as (time_h, temp_c) breakpoints plus an interpolation mode."""

    breakpoints: tuple[tuple[float, float], ...]
    mode: Mode = "piecewise_constant"

    def __init__(
        self,
        breakpoints: Iterable[Sequence[float]],
        mode: Mode = "piecewise_constant",
    ) -> None:
        bps = tuple((float(t), float(T)) for t, T in breakpoints)
        if not bps:
            raise ValueError("TemperatureSchedule needs at least one breakpoint")
        times = [t for t, _ in bps]
        if any(t2 <= t1 for t1, t2 in zip(times, times[1:])):
            raise ValueError("breakpoint times must be strictly increasing")
        if any(not math.isfinite(T) or T < -10 or T > 60 for _, T in bps):
            raise ValueError("breakpoint temperatures must be finite and within -10..60 degC")
        if mode not in ("piecewise_constant", "piecewise_linear"):
            raise ValueError(f"unknown mode {mode!r}")
        object.__setattr__(self, "breakpoints", bps)
        object.__setattr__(self, "mode", mode)

    @classmethod
    def constant(cls, temp_c: float) -> TemperatureSchedule:
        return cls([(0.0, temp_c)])

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TemperatureSchedule:
        return cls(d["breakpoints"], d.get("mode", "piecewise_constant"))

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "breakpoints": [list(bp) for bp in self.breakpoints]}

    @property
    def times(self) -> np.ndarray:
        return np.array([t for t, _ in self.breakpoints])

    @property
    def temps(self) -> np.ndarray:
        return np.array([T for _, T in self.breakpoints])

    def temperature_at(self, t_h):
        """Temperature (degC) at time(s) ``t_h`` (float or array-like)."""
        t = np.asarray(t_h, dtype=float)
        if self.mode == "piecewise_linear":
            out = np.interp(t, self.times, self.temps)
        else:
            idx = np.searchsorted(self.times, t, side="right") - 1
            out = self.temps[np.clip(idx, 0, len(self.temps) - 1)]
        return float(out) if out.ndim == 0 else out

    def segment_edges(self, t_end_h: float) -> list[float]:
        """Integration segment edges: 0, interior breakpoints, t_end."""
        inner = [t for t in self.times if 0.0 < t < t_end_h]
        return [0.0, *inner, float(t_end_h)]


# ---------------------------------------------------------------------------------------------
# Core integrator
# ---------------------------------------------------------------------------------------------


def _time_grid(t_end_h: float, dt_h: float) -> np.ndarray:
    if t_end_h <= 0 or dt_h <= 0:
        raise ValueError("t_end_h and dt_h must be positive")
    n = int(round(t_end_h / dt_h))
    grid = np.linspace(0.0, n * dt_h, n + 1)
    if grid[-1] < t_end_h - 1e-9:
        grid = np.append(grid, t_end_h)
    return np.round(grid, 10)


def integrate(
    schedule: TemperatureSchedule,
    p: Mapping[str, float],
    t_eval: np.ndarray,
    initial: Mapping[str, float] | None = None,
) -> np.ndarray:
    """Integrate the ODE; returns states with shape (len(t_eval), len(STATE_NAMES)).

    Integration is split at schedule breakpoints so step changes are handled exactly.
    """
    y = K.initial_state(p, initial)
    t_eval = np.asarray(t_eval, dtype=float)
    t_end = float(t_eval[-1])
    out = np.empty((len(t_eval), len(y)))
    edges = schedule.segment_edges(t_end)
    for a, b in zip(edges[:-1], edges[1:]):
        last = b == edges[-1]
        mask = (t_eval >= a) & ((t_eval <= b) if last else (t_eval < b))
        te = t_eval[mask]
        te_full = np.unique(np.concatenate([[a], te, [b]]))
        if schedule.mode == "piecewise_constant":
            T_seg = schedule.temperature_at(a)
            fun = lambda t, yy, T_seg=T_seg: K.rhs(t, yy, T_seg, p)
        else:
            fun = lambda t, yy: K.rhs(t, yy, schedule.temperature_at(t), p)
        sol = solve_ivp(
            fun,
            (a, b),
            y,
            method="LSODA",
            t_eval=te_full,
            rtol=1e-6,
            atol=1e-8,
            max_step=0.25,
        )
        if not sol.success:
            raise RuntimeError(f"ODE integration failed on [{a}, {b}] h: {sol.message}")
        Y = sol.y.T
        idx = np.searchsorted(te_full, te)
        out[mask] = Y[idx]
        y = np.maximum(Y[-1], 0.0)
    return np.maximum(out, 0.0)


def _postprocess(
    t: np.ndarray, Y: np.ndarray, schedule: TemperatureSchedule, p: Mapping[str, float]
) -> pd.DataFrame:
    T = np.asarray(schedule.temperature_at(t), dtype=float)
    df = pd.DataFrame(Y, columns=list(K.STATE_NAMES))
    df.insert(0, "temperature_c", T)
    df.insert(0, "time_h", t)
    ph = np.array([K.ph_from_acids(la, ac, p) for la, ac in zip(df.lactic_acid, df.acetic_acid)])
    df["ph"] = ph
    exp_cap = np.array(
        [
            K.expansion_from_state(g, q, Ti, p)
            for g, q, Ti in zip(df.gas_retained, df.gluten_integrity, T)
        ]
    )
    df["expansion_ratio"] = exp_cap[:, 0]
    df["expansion_capacity"] = exp_cap[:, 1]
    df["tta_ml_per_10g"] = [
        K.titratable_acidity(la, ac, h, p) for la, ac, h in zip(df.lactic_acid, df.acetic_acid, ph)
    ]
    with np.errstate(divide="ignore", invalid="ignore"):
        fq = (df.lactic_acid / K.MW_LACTIC) / (df.acetic_acid / K.MW_ACETIC)
    df["fermentation_quotient"] = fq.replace([np.inf, -np.inf], np.nan)
    # gas-mass-based rise at a fixed 25 degC, so that thermal contraction of the gas after a
    # cooling step is not mistaken for a post-peak collapse
    e_ref = np.array([K.expansion_from_state(g, 1.0, 25.0, p)[0] for g in df.gas_retained])
    df["overproof_risk"] = overproof_risk(
        df.expansion_ratio.to_numpy(), df.expansion_capacity.to_numpy(), e_ref
    )
    df["co2_retained"] = df["gas_retained"]
    df["gluten_weakening_index"] = gluten_weakening_index(df.time_h.to_numpy(), ph, T, p)
    df["provenance"] = PROVENANCE
    return df[[*OUTPUT_COLUMNS, *EXTRA_COLUMNS, "provenance"]]


def gluten_weakening_index(
    t_h: np.ndarray, ph: np.ndarray, temperature_c: np.ndarray, p: Mapping[str, float]
) -> np.ndarray:
    """Phenomenological 0-1 index of acid-driven gluten weakening (assumption; downstream use).

    ``GWI(t) = 1 - exp(-D(t) / gwi_dose_ref)``, with acid dose
    ``D(t) = integral_0^t max(0, gluten_ph_onset - pH) * q10_gluten**((T - 25)/10) dt``
    (pH-unit x hours). Monotone non-decreasing; 0 for dough that never acidifies below the
    onset pH. It is an exposure measure, not a rheological measurement.
    """
    t = np.asarray(t_h, float)
    drive = np.maximum(0.0, p["gluten_ph_onset"] - np.asarray(ph, float)) * p["q10_gluten"] ** (
        (np.asarray(temperature_c, float) - 25.0) / 10.0
    )
    dose = np.concatenate([[0.0], np.cumsum(0.5 * (drive[1:] + drive[:-1]) * np.diff(t))])
    return 1.0 - np.exp(-dose / p["gwi_dose_ref"])


def overproof_risk(
    expansion: np.ndarray, capacity: np.ndarray, expansion_for_fall: np.ndarray | None = None
) -> np.ndarray:
    """Heuristic 0-1 overproofing signal (model-derived warning, not a measurement).

    - Approaching the gas-holding capacity (rise/capacity 0.85 -> 1.0) ramps the signal to at
      most 0.45: the dough is near its peak ("ready"), not yet overproofed.
    - A fall from the running peak rise drives it 0 -> 1 as the dough loses 0 -> 10 % of its
      peak rise above 1; >= 0.5 (5 % of the rise lost) is reported as overproof onset.

    ``expansion_for_fall`` (default ``expansion``) is the series used for the fall; the
    simulator passes the rise at a fixed 25 degC so that thermal contraction of the gas after
    a cooling step is not mistaken for collapse.
    """
    e = np.asarray(expansion, float)
    c = np.asarray(capacity, float)
    ef = e if expansion_for_fall is None else np.asarray(expansion_for_fall, float)
    near = 0.45 * np.clip((e / c - 0.85) / 0.15, 0.0, 1.0)
    peak = np.maximum.accumulate(ef)
    fall = np.where(peak > 1.0, (peak - ef) / np.maximum(peak - 1.0, 1e-9), 0.0)
    fall_sig = np.clip(fall / 0.10, 0.0, 1.0)
    return np.maximum(near, fall_sig)


def simulate(
    schedule: TemperatureSchedule,
    params: Mapping[str, float] | None = None,
    t_end_h: float = 16.0,
    dt_h: float = 0.05,
    initial: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Simulate one fermentation. Returns a DataFrame (see module docstring); provenance "simulated"."""
    if not isinstance(schedule, TemperatureSchedule):
        schedule = TemperatureSchedule.from_dict(schedule)  # type: ignore[arg-type]
    p = K.resolve_params(params)
    t = _time_grid(t_end_h, dt_h)
    Y = integrate(schedule, p, t, initial)
    df = _postprocess(t, Y, schedule, p)
    df.attrs.update(
        provenance=PROVENANCE,
        schedule=schedule.to_dict(),
        params=dict(p),
        units=dict(UNITS),
        note="Model output. co2_cumulative is modelled CO2 production; the BME680 does not measure CO2.",
    )
    return df


# ---------------------------------------------------------------------------------------------
# Uncertainty
# ---------------------------------------------------------------------------------------------


def sample_parameters(
    n_samples: int,
    seed: int = 0,
    base: Mapping[str, float] | None = None,
    sample_names: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Draw ``n_samples`` parameter sets (rows) within configured ranges; deterministic in ``seed``.

    Parameters not in ``sample_names`` (default: all with ``sampled: true``) and any keys fixed
    in ``base`` stay at their (base) value.
    """
    specs = K.parameter_specs()
    p0 = K.resolve_params(base)
    names = (
        list(sample_names)
        if sample_names is not None
        else [k for k, s in specs.items() if s.sampled]
    )
    names = [n for n in names if not (base and n in base)]
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n_samples):
        row = dict(p0)
        for n in names:
            row[n] = specs[n].sample(rng)
        # keep cardinal temperatures ordered
        for org in ("yeast", "lab"):
            lo, opt, hi = row[f"t_min_{org}"], row[f"t_opt_{org}"], row[f"t_max_{org}"]
            if not lo < opt < hi:
                row[f"t_min_{org}"], row[f"t_opt_{org}"], row[f"t_max_{org}"] = (
                    p0[f"t_min_{org}"],
                    p0[f"t_opt_{org}"],
                    p0[f"t_max_{org}"],
                )
        rows.append(row)
    return pd.DataFrame(rows)


def _qname(q: float) -> str:
    return "median" if abs(q - 0.5) < 1e-12 else f"p{int(round(q * 100)):02d}"


def simulate_with_uncertainty(
    schedule: TemperatureSchedule,
    n_samples: int = 64,
    seed: int = 0,
    params: Mapping[str, float] | None = None,
    t_end_h: float = 16.0,
    dt_h: float = 0.1,
    initial: Mapping[str, float] | None = None,
    quantiles: Sequence[float] = (0.05, 0.5, 0.95),
    sample_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Monte-Carlo uncertainty envelopes (see module docstring). Deterministic for a given seed."""
    if not isinstance(schedule, TemperatureSchedule):
        schedule = TemperatureSchedule.from_dict(schedule)  # type: ignore[arg-type]
    samples = sample_parameters(n_samples, seed=seed, base=params, sample_names=sample_names)
    t = _time_grid(t_end_h, dt_h)
    stacks: dict[str, list[np.ndarray]] = {v: [] for v in UNCERTAINTY_OUTPUTS}
    ok_rows = []
    n_failed = 0
    for i, row in samples.iterrows():
        p = row.to_dict()
        try:
            Y = integrate(schedule, p, t, initial)
        except Exception:  # noqa: BLE001 - a failed sample is recorded, not fatal
            n_failed += 1
            continue
        df = _postprocess(t, Y, schedule, p)
        for v in UNCERTAINTY_OUTPUTS:
            stacks[v].append(df[v].to_numpy(dtype=float))
        ok_rows.append(i)
    if not ok_rows:
        raise RuntimeError("all uncertainty samples failed")
    outputs: dict[str, dict[str, np.ndarray]] = {}
    env = {"time_h": t}
    for v, arrs in stacks.items():
        A = np.vstack(arrs)
        outputs[v] = {}
        for q in quantiles:
            qa = np.nanquantile(A, q, axis=0)
            outputs[v][_qname(q)] = qa
            env[f"{v}_{_qname(q)}"] = qa
    envelope = pd.DataFrame(env)
    envelope["provenance"] = PROVENANCE
    envelope.attrs.update(
        provenance=PROVENANCE, schedule=schedule.to_dict(), n_samples=n_samples, seed=seed
    )
    nominal = simulate(schedule, params=params, t_end_h=t_end_h, dt_h=dt_h, initial=initial)
    return {
        "time_h": t,
        "outputs": outputs,
        "envelope": envelope,
        "param_samples": samples.loc[ok_rows].reset_index(drop=True),
        "samples": {v: np.vstack(a) for v, a in stacks.items()},
        "nominal": nominal,
        "n_samples": n_samples,
        "n_failed": n_failed,
        "seed": seed,
        "quantiles": tuple(quantiles),
        "provenance": PROVENANCE,
    }


# ---------------------------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------------------------


def _interp(df: pd.DataFrame, col: str, t: float) -> float:
    return float(np.interp(t, df.time_h, df[col]))


def summarize_prediction(
    df: pd.DataFrame, window_h: tuple[float, float] = (12.0, 16.0)
) -> dict[str, Any]:
    """Key decision numbers from a ``simulate`` DataFrame (all values simulated)."""
    t = df.time_h.to_numpy()
    e = df.expansion_ratio.to_numpy()
    i_pk = int(np.argmax(e))
    doubled = np.nonzero(e >= 2.0)[0]
    risky = np.nonzero(df.overproof_risk.to_numpy() >= 0.5)[0]
    out = {
        "provenance": PROVENANCE,
        "t_end_h": float(t[-1]),
        "final_ph": float(df.ph.iloc[-1]),
        "final_lactic_g_per_kg": float(df.lactic_acid.iloc[-1]),
        "final_acetic_g_per_kg": float(df.acetic_acid.iloc[-1]),
        "final_fermentation_quotient": float(df.fermentation_quotient.iloc[-1]),
        "final_tta_ml_per_10g": float(df.tta_ml_per_10g.iloc[-1]),
        "final_co2_g_per_kg": float(df.co2_cumulative.iloc[-1]),
        "final_expansion_ratio": float(e[-1]),
        "peak_expansion_ratio": float(e[i_pk]),
        "peak_expansion_time_h": float(t[i_pk]),
        "time_to_double_h": float(t[doubled[0]]) if len(doubled) else None,
        "overproof_onset_h": float(t[risky[0]]) if len(risky) else None,
    }
    for th in window_h:
        if th <= t[-1]:
            out[f"ph_at_{th:g}h"] = _interp(df, "ph", th)
            out[f"expansion_at_{th:g}h"] = _interp(df, "expansion_ratio", th)
            out[f"lactic_at_{th:g}h"] = _interp(df, "lactic_acid", th)
            out[f"acetic_at_{th:g}h"] = _interp(df, "acetic_acid", th)
    return out


def summarize_for_morphology(df: pd.DataFrame, t_end_h: float | None = None) -> dict[str, Any]:
    """Scalars a crumb-morphology mapper needs, from a ``simulate`` DataFrame.

    ``t_end_h`` truncates the trajectory at the (planned or actual) end of fermentation / bake
    time; default is the last simulated time. All values are simulated.

    Keys: provenance, t_end_h, co2_cumulative_g_per_kg, co2_retained_g_per_kg,
    co2_retained_peak_g_per_kg, expansion_ratio_final, expansion_ratio_peak,
    peak_expansion_time_h, max_expansion_rate_per_h, max_expansion_rate_time_h,
    overproof_flag, overproof_onset_h, overproof_risk_final, final_ph,
    gluten_weakening_index, final_tta_ml_per_10g, final_fermentation_quotient,
    expansion_fall_from_peak_fraction, units.
    """
    d = df if t_end_h is None else df[df.time_h <= t_end_h + 1e-9]
    if len(d) < 2:
        raise ValueError("need at least two time points up to t_end_h")
    t = d.time_h.to_numpy(float)
    e = d.expansion_ratio.to_numpy(float)
    rate = np.gradient(e, t)
    i_pk, i_rate = int(np.argmax(e)), int(np.argmax(rate))
    risk = d.overproof_risk.to_numpy(float)
    risky = np.nonzero(risk >= 0.5)[0]
    last = d.iloc[-1]
    return {
        "provenance": PROVENANCE,
        "t_end_h": float(t[-1]),
        "co2_cumulative_g_per_kg": float(last.co2_cumulative),
        "co2_retained_g_per_kg": float(last.co2_retained),
        "co2_retained_peak_g_per_kg": float(d.co2_retained.max()),
        "expansion_ratio_final": float(e[-1]),
        "expansion_ratio_peak": float(e[i_pk]),
        "peak_expansion_time_h": float(t[i_pk]),
        "max_expansion_rate_per_h": float(rate[i_rate]),
        "max_expansion_rate_time_h": float(t[i_rate]),
        "overproof_flag": bool(len(risky) > 0),
        "overproof_onset_h": float(t[risky[0]]) if len(risky) else None,
        "overproof_risk_final": float(risk[-1]),
        "expansion_fall_from_peak_fraction": float((e[i_pk] - e[-1]) / max(e[i_pk] - 1.0, 1e-9)),
        "final_ph": float(last.ph),
        "gluten_weakening_index": float(last.gluten_weakening_index),
        "final_tta_ml_per_10g": float(last.tta_ml_per_10g),
        "final_fermentation_quotient": float(last.fermentation_quotient),
        "units": {
            "co2": "g CO2 / kg dough (modelled; not a sensor value)",
            "expansion_ratio": "V/V0 (= relative height)",
            "max_expansion_rate_per_h": "(V/V0) per h",
            "gluten_weakening_index": "0-1 (phenomenological)",
        },
    }


# Scenario helpers re-exported for convenience.
from .scenarios import Scenario, load_experiments, load_scenario, load_scenarios

__all__ = [
    "EXTRA_COLUMNS",
    "OUTPUT_COLUMNS",
    "PROVENANCE",
    "UNITS",
    "Scenario",
    "TemperatureSchedule",
    "integrate",
    "load_experiments",
    "load_scenario",
    "load_scenarios",
    "overproof_risk",
    "sample_parameters",
    "simulate",
    "simulate_with_uncertainty",
    "summarize_prediction",
    "summarize_for_morphology",
    "gluten_weakening_index",
]
