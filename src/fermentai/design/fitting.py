"""Multi-experiment calibration for the benchmark (adapter; provenance: simulated).

``fermentation.calibration.calibrate`` fits one schedule's observations. A benchmark agent
accumulates several experiments with *different* schedules, so this module fits them jointly
using the same conventions as ``calibrate``:

* free parameters bounded by their configured [min, max] and optimised in normalised
  coordinates z = (theta - min) / (max - min) with ``scipy.optimize.least_squares`` (trf);
* residuals (observed - model) / sigma for pH and expansion ratio;
* ``prior_weight`` > 0 adds a Gaussian pull toward the configured value with sd = range / 4.

Posterior (Laplace / Gauss-Newton): Sigma_z = (J^T J)^-1 with J the Jacobian of the whitened
residuals *including the prior rows*, i.e. a proper Gaussian posterior approximation given the
known in-silico noise sd. (``calibrate`` additionally inflates by max(2*cost/dof, 1); we do not,
because the benchmark's noise sd is known exactly — documented in docs/benchmark_results.md.)

Prior covariance in z units is diag(0.25^2 / prior_weight). Information gain of a dataset is
IG = 0.5 * (logdet Sigma_prior - logdet Sigma_post) [nats].
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from fermentai.fermentation import kinetics as K

from .world import FREE_PARAMS, NOISE_SD, forward

PRIOR_SD_Z = 0.25  # calibrate(): prior sd = range / 4
PRIOR_WEIGHT = 1.0


def spans(free: Sequence[str] = FREE_PARAMS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    specs = K.parameter_specs()
    lo = np.array([specs[n].min for n in free], float)
    hi = np.array([specs[n].max for n in free], float)
    center = np.array([specs[n].value for n in free], float)
    return lo, hi, center


def z_to_params(z: np.ndarray, free: Sequence[str] = FREE_PARAMS, base: Mapping[str, float] | None = None
                ) -> dict[str, float]:
    lo, hi, _ = spans(free)
    p = K.resolve_params(base)
    p.update({n: float(v) for n, v in zip(free, lo + np.asarray(z, float) * (hi - lo))})
    return p


def params_to_z(values: Mapping[str, float], free: Sequence[str] = FREE_PARAMS) -> np.ndarray:
    lo, hi, _ = spans(free)
    return (np.array([values[n] for n in free], float) - lo) / (hi - lo)


def prior_cov_z(free: Sequence[str] = FREE_PARAMS, prior_weight: float = PRIOR_WEIGHT) -> np.ndarray:
    return np.eye(len(free)) * PRIOR_SD_Z**2 / prior_weight


def whitened_outputs(points, params: Mapping[str, float], times: Mapping[str, Any]) -> np.ndarray:
    out = forward(points, params, {k: np.asarray(v, float) for k, v in times.items()})
    return np.concatenate([out[v] / NOISE_SD[v] for v in ("ph", "expansion_ratio") if v in out])


@dataclass
class FitResult:
    z: np.ndarray  # MAP estimate (normalised)
    params: dict[str, float]  # full parameter dict at the MAP
    fitted: dict[str, float]  # free parameters only
    cov_z: np.ndarray  # Laplace posterior covariance (normalised units)
    sd: dict[str, float]  # posterior sd in parameter units
    sd_ratio: dict[str, float]  # posterior sd / prior sd (1 = nothing learned)
    info_gain_nats: float
    n_experiments: int
    n_observations: int
    nfev: int
    success: bool
    n_failed_evals: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"fitted": self.fitted, "sd": self.sd, "sd_ratio": self.sd_ratio,
                "info_gain_nats": self.info_gain_nats, "n_experiments": self.n_experiments,
                "n_observations": self.n_observations, "nfev": self.nfev, "success": self.success}


def _posterior(J_data: np.ndarray, free: Sequence[str], prior_weight: float) -> tuple[np.ndarray, float]:
    """Laplace posterior: precision = J_data^T J_data + analytic prior precision (always invertible).
    Identical to (J^T J)^-1 of the full whitened Jacobian incl. prior rows, but robust when the
    numerical data Jacobian degenerates (e.g. a failed integration step)."""
    Jd = np.nan_to_num(np.asarray(J_data, float), nan=0.0, posinf=0.0, neginf=0.0)
    H = Jd.T @ Jd + np.linalg.inv(prior_cov_z(free, prior_weight))
    cov = np.linalg.inv(H)
    sign, logdet_post = np.linalg.slogdet(cov)
    _, logdet_prior = np.linalg.slogdet(prior_cov_z(free, prior_weight))
    ig = 0.5 * (logdet_prior - logdet_post) if sign > 0 else float("nan")
    return cov, float(ig)


def prior_fit(free: Sequence[str] = FREE_PARAMS, prior_weight: float = PRIOR_WEIGHT) -> FitResult:
    _, _, center = spans(free)
    lo, hi, _ = spans(free)
    z = (center - lo) / (hi - lo)
    cov = prior_cov_z(free, prior_weight)
    sd = np.sqrt(np.diag(cov)) * (hi - lo)
    return FitResult(z=z, params=z_to_params(z, free), fitted=dict(zip(free, center.tolist())), cov_z=cov,
                     sd=dict(zip(free, sd.tolist())), sd_ratio={n: 1.0 for n in free}, info_gain_nats=0.0,
                     n_experiments=0, n_observations=0, nfev=0, success=True)


def fit_experiments(experiments: Sequence[Mapping[str, Any]], *, free: Sequence[str] = FREE_PARAMS,
                    prior_weight: float = PRIOR_WEIGHT, z0: np.ndarray | None = None, max_nfev: int = 60
                    ) -> FitResult:
    """Jointly fit ``free`` parameters to every experiment's noisy observations (MAP + Laplace)."""
    if not experiments:
        return prior_fit(free, prior_weight)
    lo, hi, center = spans(free)
    span = hi - lo
    zc = (center - lo) / span
    obs = []
    for e in experiments:
        times = {k: np.asarray(v, float) for k, v in e["times"].items()}
        y = np.concatenate([np.asarray(e["observed"][v], float) / NOISE_SD[v] for v in ("ph", "expansion_ratio")])
        obs.append((e["points"], times, y))
    n_obs = int(sum(len(o[2]) for o in obs))
    sw = np.sqrt(prior_weight) / PRIOR_SD_Z
    failures = [0]

    def resid(z):
        p = z_to_params(z, free)
        try:
            r = [whitened_outputs(pts, p, times) - y for pts, times, y in obs]
        except Exception:  # noqa: BLE001 - integration failure -> large residual (counted, reported)
            failures[0] += 1
            return np.full(n_obs + len(free), 1e3)
        return np.concatenate([*r, sw * (np.asarray(z) - zc)])

    start = np.clip(zc if z0 is None else np.asarray(z0, float), 1e-6, 1 - 1e-6)
    sol = least_squares(resid, start, bounds=(0.0, 1.0), x_scale=1.0, diff_step=1e-3, max_nfev=max_nfev,
                        method="trf")
    cov, ig = _posterior(np.asarray(sol.jac)[:n_obs], free, prior_weight)
    sd_z = np.sqrt(np.clip(np.diag(cov), 0, None))
    params = z_to_params(sol.x, free)
    return FitResult(
        z=sol.x.copy(), params=params, fitted={n: params[n] for n in free}, cov_z=cov,
        sd=dict(zip(free, (sd_z * span).tolist())),
        sd_ratio=dict(zip(free, (sd_z / (PRIOR_SD_Z / np.sqrt(prior_weight))).tolist())),
        info_gain_nats=ig, n_experiments=len(experiments), n_observations=n_obs, nfev=int(sol.nfev),
        success=bool(sol.success), n_failed_evals=failures[0],
    )
