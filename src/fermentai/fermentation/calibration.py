"""Least-squares calibration of selected kinetic parameters to (sparse) observations.

Stable public API
-----------------
``calibrate(observations, schedule=None, free_params=("beta_lactic", "qs_yeast"), params=None,
initial=None, sigmas=None, prior_weight=0.0, t_end_h=None, dt_h=0.1, max_nfev=200)
-> CalibrationResult``

``observations``: DataFrame with ``time_h`` (or ``timestamp_utc``, converted to hours from the
first row) and any of:

- ``ph`` (also accepted: ``ph_observed``),
- ``expansion_ratio`` (aliases ``relative_height``, ``rise_ratio``), or ``height_mm`` /
  ``starter_height_mm`` (converted to a ratio using ``initial_height_mm`` if present or the
  earliest valid height),
- a temperature column (``temperature_c``, ``starter_temperature_c`` /
  ``dough_temperature_c`` preferred over ``chamber_temperature_c``). Temperature is a model
  *input*: when ``schedule`` is None a piecewise-linear schedule is built from it.

NaN / missing values are dropped per variable, so sparse pH (one probe, periodic) is fine.
Fitting is bounded by each parameter's configured [min, max] (configs/kinetics.yaml) and
performed in normalised coordinates with ``scipy.optimize.least_squares`` (trust-region).
``prior_weight > 0`` adds a weak Gaussian pull toward the configured value (sd = range/4),
which stabilises fits to very sparse data.

``CalibrationResult`` fields: fitted_params, initial_params, param_std (from the Jacobian;
NaN if not identifiable), residuals (DataFrame: time_h, variable, observed, predicted,
residual), rmse (per variable), n_observations, status, message, success, cost, prediction
(DataFrame of the calibrated simulation), provenance ("simulated").

``status`` labels: ``"uncalibrated"`` (no usable observations / no free params),
``"under_determined"`` (fewer observations than free parameters), ``"poorly_constrained"``
(converged but a parameter is unidentifiable or its sd exceeds half its range or it sits on a
bound), ``"calibrated"``, ``"failed"`` (optimiser error).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from . import kinetics as K
from .simulation import TemperatureSchedule, _time_grid, integrate, simulate

FITTABLE_OUTPUTS = ("ph", "expansion_ratio")
DEFAULT_SIGMAS = {"ph": 0.05, "expansion_ratio": 0.08}

_ALIASES = {
    "ph": ("ph", "ph_observed"),
    "expansion_ratio": ("expansion_ratio", "relative_height", "rise_ratio"),
}
_HEIGHT_COLS = ("height_mm", "starter_height_mm", "dough_height_mm")
_TEMP_COLS = (
    "temperature_c",
    "dough_temperature_c",
    "starter_temperature_c",
    "chamber_temperature_c",
)


@dataclass
class CalibrationResult:
    fitted_params: dict[str, float]
    initial_params: dict[str, float]
    param_std: dict[str, float]
    residuals: pd.DataFrame
    rmse: dict[str, float]
    n_observations: int
    status: str
    message: str
    success: bool
    cost: float = float("nan")
    prediction: pd.DataFrame | None = None
    schedule: TemperatureSchedule | None = None
    provenance: str = K.PROVENANCE
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary (without the prediction DataFrame)."""
        return {
            "fitted_params": self.fitted_params,
            "initial_params": self.initial_params,
            "param_std": self.param_std,
            "rmse": self.rmse,
            "n_observations": self.n_observations,
            "status": self.status,
            "message": self.message,
            "success": self.success,
            "cost": self.cost,
            "schedule": self.schedule.to_dict() if self.schedule else None,
            "provenance": self.provenance,
        }


def _time_column(obs: pd.DataFrame) -> np.ndarray:
    if "time_h" in obs:
        return obs["time_h"].to_numpy(dtype=float)
    if "timestamp_utc" in obs:
        ts = pd.to_datetime(obs["timestamp_utc"], utc=True)
        return ((ts - ts.min()).dt.total_seconds() / 3600.0).to_numpy()
    raise ValueError("observations need a 'time_h' or 'timestamp_utc' column")


def prepare_observations(observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Normalise observations to long format (time_h, variable, observed) + temperature series."""
    obs = observations.copy()
    obs["time_h"] = _time_column(obs)
    rows = []
    for var, aliases in _ALIASES.items():
        col = next((c for c in aliases if c in obs), None)
        if col is not None:
            sub = obs[["time_h", col]].dropna()
            rows += [(t, var, float(v)) for t, v in zip(sub.time_h, sub[col])]
    if not any(r[1] == "expansion_ratio" for r in rows):
        hcol = next((c for c in _HEIGHT_COLS if c in obs), None)
        if hcol is not None:
            sub = obs[["time_h", hcol]].dropna().sort_values("time_h")
            if len(sub):
                if "initial_height_mm" in obs and obs["initial_height_mm"].notna().any():
                    h0 = float(obs["initial_height_mm"].dropna().iloc[0])
                else:
                    h0 = float(sub[hcol].iloc[0])
                if h0 > 0:
                    rows += [
                        (t, "expansion_ratio", float(v) / h0) for t, v in zip(sub.time_h, sub[hcol])
                    ]
    long = pd.DataFrame(rows, columns=["time_h", "variable", "observed"])
    long = long[long.time_h >= 0].sort_values(["variable", "time_h"]).reset_index(drop=True)
    tcol = next((c for c in _TEMP_COLS if c in obs and obs[c].notna().any()), None)
    temps = None
    if tcol is not None:
        temps = obs[["time_h", tcol]].dropna().rename(columns={tcol: "temperature_c"})
        temps = temps.groupby("time_h", as_index=False).mean().sort_values("time_h")
    return long, temps


def schedule_from_observations(temps: pd.DataFrame, max_points: int = 200) -> TemperatureSchedule:
    """Piecewise-linear schedule from an observed temperature series (downsampled if long)."""
    t = temps.time_h.to_numpy(float)
    T = temps.temperature_c.to_numpy(float)
    if len(t) > max_points:
        idx = np.unique(np.linspace(0, len(t) - 1, max_points).round().astype(int))
        t, T = t[idx], T[idx]
    if t[0] > 0:
        t, T = np.r_[0.0, t], np.r_[T[0], T]
    return TemperatureSchedule(list(zip(t, T)), mode="piecewise_linear")


def calibrate(
    observations: pd.DataFrame,
    schedule: TemperatureSchedule | None = None,
    free_params: Sequence[str] = ("beta_lactic", "qs_yeast"),
    params: Mapping[str, float] | None = None,
    initial: Mapping[str, float] | None = None,
    sigmas: Mapping[str, float] | None = None,
    prior_weight: float = 0.0,
    t_end_h: float | None = None,
    dt_h: float = 0.1,
    max_nfev: int = 200,
) -> CalibrationResult:
    """Fit ``free_params`` to observed pH / expansion (see module docstring)."""
    specs = K.parameter_specs()
    base = K.resolve_params(params)
    free = list(free_params)
    unknown = [n for n in free if n not in specs]
    if unknown:
        raise KeyError(f"unknown free parameter(s): {unknown}")
    sig = {**DEFAULT_SIGMAS, **(sigmas or {})}

    long, temps = prepare_observations(observations)
    if schedule is None:
        if temps is None or temps.empty:
            raise ValueError("no schedule given and no temperature column in observations")
        schedule = schedule_from_observations(temps)
    elif not isinstance(schedule, TemperatureSchedule):
        schedule = TemperatureSchedule.from_dict(schedule)  # type: ignore[arg-type]

    init_vals = {n: base[n] for n in free}
    n_obs = len(long)

    def _result(status, message, success, fitted, std=None, cost=float("nan")):
        p = dict(base, **fitted)
        horizon = t_end_h or max(16.0, float(long.time_h.max()) if n_obs else 16.0)
        pred = simulate(schedule, params=p, t_end_h=horizon, dt_h=dt_h, initial=initial)
        res_rows = []
        for var, g in long.groupby("variable"):
            pr = np.interp(g.time_h, pred.time_h, pred[var])
            res_rows.append(
                pd.DataFrame(
                    {
                        "time_h": g.time_h.values,
                        "variable": var,
                        "observed": g.observed.values,
                        "predicted": pr,
                        "residual": g.observed.values - pr,
                    }
                )
            )
        residuals = (
            pd.concat(res_rows, ignore_index=True)
            if res_rows
            else pd.DataFrame(columns=["time_h", "variable", "observed", "predicted", "residual"])
        )
        rmse = {v: float(np.sqrt(np.mean(g.residual**2))) for v, g in residuals.groupby("variable")}
        return CalibrationResult(
            fitted_params={n: p[n] for n in free},
            initial_params=init_vals,
            param_std=std or {n: float("nan") for n in free},
            residuals=residuals,
            rmse=rmse,
            n_observations=n_obs,
            status=status,
            message=message,
            success=success,
            cost=cost,
            prediction=pred,
            schedule=schedule,
        )

    if n_obs == 0 or not free:
        return _result(
            "uncalibrated",
            "no usable pH/expansion observations or no free parameters",
            False,
            init_vals,
        )

    lo = np.array([specs[n].min for n in free])
    hi = np.array([specs[n].max for n in free])
    span = np.where(hi > lo, hi - lo, 1.0)
    z0 = np.clip((np.array([init_vals[n] for n in free]) - lo) / span, 1e-6, 1 - 1e-6)
    t_max = float(long.time_h.max())
    t_grid = _time_grid(max(t_max, dt_h), dt_h)
    obs_by_var = {
        v: (g.time_h.to_numpy(), g.observed.to_numpy()) for v, g in long.groupby("variable")
    }
    prior_sd = span / 4.0
    prior_center = np.array([specs[n].value for n in free])

    def residual_vec(z):
        vals = lo + z * span
        p = dict(base, **dict(zip(free, vals)))
        try:
            Y = integrate(schedule, p, t_grid, initial)
        except Exception:  # noqa: BLE001
            return np.full(n_obs + (len(free) if prior_weight > 0 else 0), 1e3)
        idx = {n: i for i, n in enumerate(K.STATE_NAMES)}
        T = np.asarray(schedule.temperature_at(t_grid), float)
        out = []
        for var, (tt, yy) in obs_by_var.items():
            if var == "ph":
                model = np.array(
                    [
                        K.ph_from_acids(a, b, p)
                        for a, b in zip(Y[:, idx["lactic_acid"]], Y[:, idx["acetic_acid"]])
                    ]
                )
            else:
                model = np.array(
                    [
                        K.expansion_from_state(g, q, Ti, p)[0]
                        for g, q, Ti in zip(
                            Y[:, idx["gas_retained"]], Y[:, idx["gluten_integrity"]], T
                        )
                    ]
                )
            out.append((np.interp(tt, t_grid, model) - yy) / sig[var])
        r = np.concatenate(out)
        if prior_weight > 0:
            r = np.concatenate([r, np.sqrt(prior_weight) * (vals - prior_center) / prior_sd])
        return r

    try:
        sol = least_squares(
            residual_vec,
            z0,
            bounds=(0.0, 1.0),
            x_scale=1.0,
            diff_step=1e-3,
            max_nfev=max_nfev,
            method="trf",
        )
    except Exception as exc:  # noqa: BLE001
        return _result("failed", f"optimiser error: {exc}", False, init_vals)

    fitted = dict(zip(free, (lo + sol.x * span).tolist()))
    # parameter sd from the Gauss-Newton approximation, scaled by the residual variance
    std = {n: float("nan") for n in free}
    dof = max(1, len(sol.fun) - len(free))
    try:
        J = sol.jac
        cov_z = np.linalg.pinv(J.T @ J) * max(2 * sol.cost / dof, 1.0)
        sd = np.sqrt(np.clip(np.diag(cov_z), 0, None)) * span
        rank = np.linalg.matrix_rank(J.T @ J, tol=1e-8)
        std = {n: (float(s) if rank == len(free) else float("nan")) for n, s in zip(free, sd)}
    except Exception:  # noqa: BLE001
        pass

    if not sol.success:
        status, msg = "failed", sol.message
    elif n_obs < len(free):
        status, msg = (
            "under_determined",
            f"{n_obs} observation(s) for {len(free)} free parameter(s)",
        )
    else:
        on_bound = [n for n, z in zip(free, sol.x) if z < 1e-3 or z > 1 - 1e-3]
        loose = [n for n in free if not np.isfinite(std[n]) or std[n] > 0.5 * span[free.index(n)]]
        if on_bound or loose:
            status = "poorly_constrained"
            msg = f"converged; at bound: {on_bound or 'none'}; weakly identified: {loose or 'none'}"
        else:
            status, msg = "calibrated", "converged"
    return _result(status, msg, bool(sol.success), fitted, std, float(sol.cost))
