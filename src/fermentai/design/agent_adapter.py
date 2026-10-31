"""Adapter: run the agents' deployed offline decision policy on a *calibrated* model.

``agents.offline_policy.build_evidence`` predicts every library candidate with
``agents.schedules.predict_schedule``, which always uses the simulator's *default*
parameters. In the benchmark each agent calibrates its own model, so this module rebuilds the
same evidence structure with predictions from given parameters. Everything downstream
(``rank_candidates``, ``lead_decision``, ``peer_decision``, ``lead_reply``, ``ScientistAgent``
messaging) is the unmodified agents/ code.

``predict_schedule_with_params`` replicates ``schedules._simulator_prediction`` (same index
mapping) with a ``params`` argument; ``tests/test_design_adapter.py`` checks it reproduces
``schedules.predict_schedule`` exactly at default parameters, so drift in agents/ is caught.
No file in agents/ is modified.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from fermentai.agents import offline_policy as P
from fermentai.agents import schedules as S
from fermentai.agents.sensory import parse_feedback
from fermentai.fermentation.simulation import simulate

from .world import to_schedule

# Canonical judge feedback for the benchmark objective (same text as data/demos/demo_01).
FEEDBACK_TEXT = ("Loaf A was mild and fluffy. Loaf B was more sour but a little dense. "
                 "I'd like a stronger sour flavor without losing the airy texture.")
SETPOINTS = {"A": 22.0, "B": 27.0}
CONTEXT: dict[str, Any] = {"setpoints": SETPOINTS}


@lru_cache(maxsize=4)
def _interp_cached(text: str) -> tuple:
    import json
    return (json.dumps(parse_feedback(text, dict(SETPOINTS)), sort_keys=True, default=str),)


def objective_interp(text: str = FEEDBACK_TEXT) -> dict[str, Any]:
    import json
    return json.loads(_interp_cached(text)[0])


def predict_schedule_with_params(points, params: Mapping[str, float] | None = None, dt_h: float = 0.05
                                 ) -> dict[str, Any]:
    """Same relative indices as ``schedules._simulator_prediction``, for arbitrary parameters."""
    pts = S.normalize_points(points)
    duration = pts[-1][0]
    df = simulate(to_schedule(pts), params=dict(params or {}), t_end_h=duration, dt_h=dt_h)
    ph = [float(x) for x in df["ph"]]
    exp_ = [float(x) for x in df["expansion_ratio"]]
    times = [float(x) for x in df["time_h"]]
    if any(math.isnan(v) for v in (ph[-1], exp_[-1])):
        raise ValueError("simulator returned NaN output")
    peak = max(exp_)
    t_peak = times[exp_.index(peak)]
    tta = [float(x) for x in df["tta_ml_per_10g"]]
    fq = [float(x) for x in df["fermentation_quotient"]]
    acid_index = min(1.0, max(0.0, (tta[-1] - 4.0) / 8.0))
    acetic_index = min(1.0, max(0.0, (4.5 - fq[-1]) / 2.0)) if math.isfinite(fq[-1]) else 0.0
    sourness = 0.1 + 0.7 * acid_index + 0.2 * acetic_index
    overproof = float(df["overproof_risk"].iloc[-1])
    g = float(df["gluten_integrity"].iloc[-1])
    rise_score = min(1.0, max(0.0, (peak - 1.0) / 1.0))
    openness = 0.15 + 0.75 * rise_score * g - 0.3 * overproof
    rise_progress = duration / t_peak if (t_peak > 0 and t_peak < duration - 0.05) else 1.0
    h = S.heuristic_prediction(pts)
    h.update({
        "model": "fermentai.fermentation.simulation",
        "provenance": "simulated",
        "acid_progress": round(acid_index, 3),
        "rise_progress": round(rise_progress, 3),
        "sourness_index": round(min(1.0, max(0.0, sourness)), 3),
        "openness_index": round(min(1.0, max(0.0, openness)), 3),
        "overproof_risk": round(min(1.0, max(0.0, overproof)), 3),
        "underproof_risk": round(1.0 / (1.0 + math.exp((peak - 1.5) / 0.08)), 3),
        "acetic_index": round(acetic_index, 3),
        "final_ph": round(ph[-1], 3),
        "peak_expansion_ratio": round(peak, 3),
        "time_to_peak_rise_h": round(t_peak, 2),
        "final_expansion_ratio": round(exp_[-1], 3),
        "final_tta_ml_per_10g": round(tta[-1], 2),
        "final_fermentation_quotient": round(fq[-1], 2),
        "final_gluten_integrity": round(g, 3),
    })
    return h


def build_evidence_calibrated(params: Mapping[str, float] | None, interp: dict[str, Any] | None = None,
                              context: dict[str, Any] | None = None, dt_h: float = 0.05) -> dict[str, Any]:
    """``offline_policy.build_evidence`` with candidate predictions from ``params``."""
    interp = interp if interp is not None else objective_interp()
    context = context if context is not None else CONTEXT
    tg = P.build_targets(interp)
    recorded = P.recorded_schedules(context)
    rec_pred = {l: predict_schedule_with_params(recorded[l]["points"], params, dt_h) for l in P.LOAVES}
    judged = tg["judged"]
    anchors = {}
    for metric, key in (("sourness", "sourness_index"), ("openness", "openness_index")):
        pts = [(rec_pred[l][key], judged[l][metric]) for l in P.LOAVES if metric in judged[l]]
        anchors[metric] = P._anchor(pts, metric)
    cands = []
    for c in S.LIBRARY:
        p = predict_schedule_with_params(c.points, params, dt_h)
        cands.append({
            "id": c.id, "name": c.name, "family": c.family, "route": c.route,
            "points": c.as_list(), "summary": c.summary, "mechanism": c.mechanism,
            "model": p,
            "pred_sourness": P._j(anchors["sourness"], p["sourness_index"]),
            "pred_openness": P._j(anchors["openness"], p["openness_index"]),
        })
    recorded_out = {}
    for l in P.LOAVES:
        recorded_out[l] = {**recorded[l], "points": [list(p) for p in recorded[l]["points"]], "model": rec_pred[l],
                           "pred_sourness": P._j(anchors["sourness"], rec_pred[l]["sourness_index"]),
                           "pred_openness": P._j(anchors["openness"], rec_pred[l]["openness_index"]),
                           "judged": judged[l]}
    return {"policy": P.POLICY_ID, "targets": tg, "recorded": recorded_out, "anchors": anchors,
            "candidates": cands, "prediction_source": "simulated", "simulator_used": True}


def true_costs(evidence_true: dict[str, Any], risk_weight: float = 1.0) -> dict[str, float]:
    """Objective value of every library schedule evaluated in the *true* world (lower = better)."""
    return {c["id"]: P.candidate_cost(c, evidence_true, risk_weight)["total"] for c in evidence_true["candidates"]}
