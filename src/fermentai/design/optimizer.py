"""Experiment-selection utilities for the benchmark (not part of the deployed agents).

Linear-Gaussian (Laplace) expected information gain over the calibrated parameters:

    EIG(c | Sigma) = 0.5 * logdet(I + J_c Sigma J_c^T)
                   = 0.5 * [logdet(Sigma^-1 + J_c^T J_c) - logdet(Sigma^-1)]   [nats]

``J_c`` is the Jacobian of candidate c's *whitened* observation vector (the pH spot checks and
rise curve it would produce, divided by the noise sd) w.r.t. the normalised free parameters,
evaluated at the current MAP estimate; ``Sigma`` is the current posterior covariance (z units).

Batch / collaborative use: after a peer commits to experiment a, the information matrix is
updated ``Sigma_a^-1 = Sigma^-1 + J_a^T J_a`` and the next agent scores
``EIG(c | Sigma_a)`` — the gain *given the peer's experiment* (greedy D-optimal batch). A
candidate identical to the peer's choice therefore scores lower than on its own, which is the
information-theoretic version of a deduplication penalty.

Selection score (lower is better), in the objective-cost units of ``offline_policy``:

    score(c) = cost(c) + redundancy_penalty(c, peer) - lam * EIG(c | Sigma[, peer])

``cost`` and ``redundancy_penalty`` come unmodified from ``offline_policy.rank_candidates``.
``lam`` is fixed a priori by :func:`scale_matched_lambda` (information spread across the
library == cost spread across the library at the prior); it is not tuned on benchmark outcomes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from fermentai.agents import offline_policy as P
from fermentai.agents import schedules as S

from .fitting import FREE_PARAMS, prior_cov_z, whitened_outputs, z_to_params
from .world import observation_times

JAC_STEP_Z = 2e-3


def candidate_jacobian(points: Sequence[Sequence[float]], z: np.ndarray, free: Sequence[str] = FREE_PARAMS
                       ) -> np.ndarray:
    """d(whitened observations)/dz by forward differences (step backwards near the upper bound)."""
    times = observation_times(points)
    z = np.asarray(z, float)
    y0 = whitened_outputs(points, z_to_params(z, free), times)
    J = np.empty((len(y0), len(free)))
    for k in range(len(free)):
        h = JAC_STEP_Z if z[k] + JAC_STEP_Z <= 1.0 else -JAC_STEP_Z
        zk = z.copy()
        zk[k] += h
        J[:, k] = (whitened_outputs(points, z_to_params(zk, free), times) - y0) / h
    return J


def library_jacobians(z: np.ndarray, free: Sequence[str] = FREE_PARAMS) -> dict[str, np.ndarray]:
    return {c.id: candidate_jacobian(c.points, z, free) for c in S.LIBRARY}


def _logdet(m: np.ndarray) -> float:
    sign, ld = np.linalg.slogdet(m)
    return float(ld) if sign > 0 else float("-inf")


def eig(J: np.ndarray, cov: np.ndarray) -> float:
    """Expected information gain (nats) of one experiment with Jacobian ``J`` given ``cov``."""
    # 0.5 * logdet(I + J cov J^T), computed in the parameter space (P x P) for stability
    S_ = np.linalg.cholesky(0.5 * (cov + cov.T))
    M = S_.T @ (J.T @ J) @ S_
    return 0.5 * _logdet(np.eye(len(cov)) + M)


def condition(cov: np.ndarray, J: np.ndarray) -> np.ndarray:
    """Posterior covariance after (hypothetically) running an experiment with Jacobian ``J``."""
    S_ = np.linalg.cholesky(0.5 * (cov + cov.T))
    inner = np.linalg.inv(np.eye(len(cov)) + S_.T @ (J.T @ J) @ S_)
    return S_ @ inner @ S_.T


def eig_table(jacs: Mapping[str, np.ndarray], cov: np.ndarray) -> dict[str, float]:
    return {cid: eig(J, cov) for cid, J in jacs.items()}


def score_candidates(evidence: dict[str, Any], eigs: Mapping[str, float], lam: float, *, risk_weight: float = 1.0,
                     avoid: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rows of ``rank_candidates`` re-scored with the information bonus, best first."""
    rows = P.rank_candidates(evidence, risk_weight=risk_weight, avoid=avoid)
    out = []
    for r in rows:
        info = float(eigs[r["id"]])
        out.append({**r, "eig_nats": round(info, 5), "info_score": round(r["score"] - lam * info, 6)})
    out.sort(key=lambda r: (r["info_score"], r["id"]))
    return out


def scale_matched_lambda(evidence: dict[str, Any], eigs: Mapping[str, float], risk_weight: float = 1.0) -> float:
    """lam such that lam * (spread of EIG over the library) == spread of objective cost over the library
    (spread = inter-quartile range, robust to the one or two extreme candidates)."""
    costs = np.array([P.candidate_cost(c, evidence, risk_weight)["total"] for c in evidence["candidates"]])
    infos = np.array([eigs[c["id"]] for c in evidence["candidates"]])
    ci = np.subtract(*np.percentile(costs, [75, 25]))
    ii = np.subtract(*np.percentile(infos, [75, 25]))
    return float(ci / ii) if ii > 1e-12 else 0.0


def prior_lambda(free: Sequence[str] = FREE_PARAMS) -> float:
    """The benchmark's fixed lam: scale-matched at the prior (default parameters, no data)."""
    from .agent_adapter import build_evidence_calibrated
    from .fitting import prior_fit

    pf = prior_fit(free)
    ev = build_evidence_calibrated(pf.params)
    return scale_matched_lambda(ev, eig_table(library_jacobians(pf.z, free), prior_cov_z(free)))
