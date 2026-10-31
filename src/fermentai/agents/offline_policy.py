"""Deterministic offline decision policy (labeled "deterministic fallback", PLAN §9).

Pipeline of pure functions (same inputs -> same outputs):

1. ``recorded_schedules(context)``  which schedules produced the judged loaves
2. ``build_targets(interp)``         judge preferences -> numeric targets on a 0..1 judge scale
3. ``build_evidence(interp, context)`` predictions for every validated candidate, *anchored*
   on the judged loaves (linear map model-index -> judge-score through the recorded loaves)
4. ``rank_candidates(evidence, ...)`` cost = distance to targets + overproof/underproof risk
   (+ redundancy vs. a peer's choice when agents communicate)
5. ``lead_decision`` / ``peer_decision`` build the structured decisions agents publish.

The functions are exposed separately so a benchmark (``fermentai.design``) can run many
decision cycles against the fermentation simulator.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from . import schedules as S

LOAVES = ("A", "B")
DEFAULT_SETPOINTS = {"A": 22.0, "B": 27.0}
DEFAULT_RECORDED_DURATION_H = 14.0
POLICY_ID = "offline_policy_v1"


# ---------------------------------------------------------------------------
# Structured decisions (shared by offline policy and LLM adapter)
# ---------------------------------------------------------------------------
class LeadDecision(BaseModel):
    observation_summary: str
    hypothesis_statement: str
    mechanism: str
    own_schedule_id: str
    peer_suggestion_schedule_id: str
    peer_suggestion_reason: str
    prediction: str
    uncertainty: str
    proposed_measurement: str
    rationale: str


class PeerDecision(BaseModel):
    critique: str
    agrees_with_hypothesis: bool
    concerns: list[str] = Field(default_factory=list)
    alternative_hypothesis: str
    own_schedule_id: str
    accepted_peer_suggestion: bool
    decision_change_note: str
    prediction: str
    uncertainty: str
    proposed_measurement: str
    rationale: str


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------
def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def setpoints_from_context(context: dict[str, Any]) -> dict[str, float]:
    sp = dict(DEFAULT_SETPOINTS)
    raw = context.get("setpoints") or {}
    if isinstance(raw, dict):
        for k in LOAVES:
            v = _num(raw.get(k))
            if v is not None:
                sp[k] = v
    return sp


def _points_from(obj: Any) -> list[tuple[float, float]] | None:
    if isinstance(obj, dict):
        for key in ("points", "schedule", "temperature_schedule", "schedule_points"):
            if key in obj:
                return _points_from(obj[key])
        return None
    if isinstance(obj, (list, tuple)) and obj:
        try:
            pts = [(float(p[0]), float(p[1])) for p in obj]
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if pts and pts[0][0] == 0.0:
            return pts
    return None


def recorded_schedules(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Find the schedule that produced each judged loaf (latest recorded experiment)."""
    setpoints = setpoints_from_context(context)
    found: dict[str, dict[str, Any]] = {}
    exps = context.get("experiments") or []
    if isinstance(exps, dict):
        exps = [exps]
    for exp in exps if isinstance(exps, list) else []:
        if not isinstance(exp, dict):
            continue
        exp_id = exp.get("experiment_id") or exp.get("id")
        chambers = exp.get("chambers")
        items: list[tuple[str, Any]] = []
        if isinstance(chambers, dict):
            items = [(k, v) for k, v in chambers.items() if k in LOAVES]
        elif exp.get("chamber_id") in LOAVES:
            items = [(exp["chamber_id"], exp)]
        for ch, spec in items:
            pts = _points_from(spec)
            if pts is None and isinstance(spec, dict):
                sp = _num(spec.get("setpoint_c") or spec.get("temperature_setpoint_c"))
                dur = _num(spec.get("duration_h")) or DEFAULT_RECORDED_DURATION_H
                if sp is not None:
                    pts = list(S.steady_schedule(sp, dur))
            if pts:
                found[ch] = {"points": pts, "experiment_id": exp_id, "source": "recorded experiment"}
    for ch in LOAVES:
        if ch not in found:
            found[ch] = {
                "points": list(S.steady_schedule(setpoints[ch], DEFAULT_RECORDED_DURATION_H)),
                "experiment_id": None,
                "source": f"assumed steady {setpoints[ch]:g} °C for {DEFAULT_RECORDED_DURATION_H:g} h (from setpoints)",
            }
    for ch, rec in found.items():
        rec["violations"] = S.validate_schedule(rec["points"])
    return found


# ---------------------------------------------------------------------------
# Judge-scale observations of the tasted loaves
# ---------------------------------------------------------------------------
def judged_values(interp: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per-loaf judged sourness/openness/aroma on 0..1 (unattributed -> both loaves)."""
    out: dict[str, dict[str, float]] = {l: {} for l in LOAVES}
    un = (interp.get("unattributed") or {}).get("attributes") or {}
    for l in LOAVES:
        attrs = dict(un)
        attrs.update(((interp.get("loaves") or {}).get(l) or {}).get("attributes") or {})
        if "sourness" in attrs:
            out[l]["sourness"] = attrs["sourness"]["score"]
        crumb = []
        if "texture" in attrs:
            crumb.append(attrs["texture"]["score"])
        if "density" in attrs:
            crumb.append(1.0 - attrs["density"]["score"])
        if crumb:
            out[l]["openness"] = round(sum(crumb) / len(crumb), 3)
        if "aroma" in attrs:
            out[l]["aroma"] = attrs["aroma"]["score"]
    return out


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
def build_targets(interp: dict[str, Any]) -> dict[str, Any]:
    judged = judged_values(interp)
    desired = interp.get("desired") or {}
    targets: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    def vals(metric):
        return {l: judged[l][metric] for l in LOAVES if metric in judged[l]}

    # --- sourness ---
    d = desired.get("sourness")
    sv = vals("sourness")
    fl = desired.get("flavor_intensity")
    if d:
        conf = max(0.4, d["confidence"])
        anchor = d.get("anchor_loaf")
        if d["direction"] == "increase":
            base = sv.get(anchor) if anchor in sv else (max(sv.values()) if sv else 0.5)
            t = d["target_level"] if d.get("target_level") and d["target_level"] > base else min(0.95, base + d["step"])
            targets["sourness"] = {"target": round(t, 3), "mode": "near", "weight": round(1.0 * conf, 3),
                                   "reason": f"more sour than the {'Loaf ' + anchor if anchor in sv else 'sourest judged loaf'} ({base:.2f})"}
        elif d["direction"] == "decrease":
            base = sv.get(anchor) if anchor in sv else (min(sv.values()) if sv else 0.5)
            t = d["target_level"] if d.get("target_level") is not None and d["target_level"] < base else max(0.05, base - d["step"])
            targets["sourness"] = {"target": round(t, 3), "mode": "near", "weight": round(1.0 * conf, 3),
                                   "reason": f"milder than {'Loaf ' + anchor if anchor in sv else 'the mildest judged loaf'} ({base:.2f})"}
        else:
            if d.get("preferred_pole") == "low":
                bound = d.get("bound")
                if anchor in sv:
                    bound = sv[anchor] if bound is None else min(bound, sv[anchor] + 0.05)
                bound = 0.7 if bound is None else bound
                targets["sourness"] = {"target": round(bound, 3), "mode": "at_most", "weight": round(2.0 * conf, 3),
                                       "constraint": True, "reason": "do not exceed current sourness"}
            else:
                base = sv.get(anchor) if anchor in sv else (max(sv.values()) if sv else 0.6)
                targets["sourness"] = {"target": round(base - 0.05, 3), "mode": "at_least", "weight": round(2.0 * conf, 3),
                                       "constraint": True, "reason": "keep current sourness"}
        # "milder than B but more flavor than A": aim between the judged loaves
        if (fl and fl["direction"] in ("increase", "decrease") and d["direction"] in ("increase", "decrease")
                and fl["direction"] != d["direction"] and len(sv) == 2):
            mid = round((sv["A"] + sv["B"]) / 2, 3)
            targets["sourness"].update(target=mid, reason="between the two judged loaves (sourness and overall "
                                                          "flavor requested in opposite directions)")
    elif fl and fl["direction"] in ("increase", "decrease"):
        base = (max(sv.values()) if fl["direction"] == "increase" else min(sv.values())) if sv else 0.5
        step = 0.1 if fl["direction"] == "increase" else -0.1
        targets["sourness"] = {"target": round(min(0.95, max(0.05, base + step)), 3), "mode": "near",
                               "weight": round(0.5 * max(0.4, fl["confidence"]), 3),
                               "reason": f"{'stronger' if step > 0 else 'milder'} overall flavor (sourness proxy)"}

    # --- crumb openness (texture and inverse density) ---
    ov = vals("openness")
    tex, den = desired.get("texture"), desired.get("density")
    crumb_votes = []
    for dd, invert in ((tex, False), (den, True)):
        if not dd:
            continue
        direction = dd["direction"]
        pole = dd.get("preferred_pole")
        if invert:
            direction = {"increase": "decrease", "decrease": "increase"}.get(direction, direction)
            pole = {"high": "low", "low": "high"}.get(pole, pole) if pole else None
        crumb_votes.append((direction, pole, dd))
    if crumb_votes:
        direction, pole, dd = max(crumb_votes, key=lambda v: v[2]["confidence"])
        conf = max(0.4, max(v[2]["confidence"] for v in crumb_votes))
        anchor = dd.get("anchor_loaf")
        if direction == "increase":
            base = ov.get(anchor) if anchor in ov else (max(ov.values()) if ov else 0.6)
            targets["openness"] = {"target": round(min(0.95, base + dd["step"]), 3), "mode": "at_least",
                                   "weight": round(0.9 * conf, 3), "reason": "more open/airy crumb"}
        elif direction == "decrease":
            base = ov.get(anchor) if anchor in ov else (min(ov.values()) if ov else 0.5)
            targets["openness"] = {"target": round(max(0.1, base - dd["step"]), 3), "mode": "near",
                                   "weight": round(0.7 * conf, 3), "reason": "tighter/chewier crumb"}
        elif pole == "high" or pole is None:
            best = max(ov, key=ov.get) if ov else None
            base = ov[best] if best else 0.75
            targets["openness"] = {"target": round(base - 0.05, 3), "mode": "at_least",
                                   "weight": round(2.5 * conf, 3), "constraint": True,
                                   "reason": f"keep crumb as open as {'Loaf ' + best if best else 'before'} ({base:.2f})"}
        else:
            targets["openness"] = {"target": 0.5, "mode": "at_most", "weight": round(0.5 * conf, 3),
                                   "reason": "keep crumb from getting more open"}

    # --- aroma / acetic character ---
    ar = desired.get("aroma")
    dtags = set(interp.get("desired_aroma_tags") or [])
    if (ar and ar["direction"] == "increase") or (fl and fl["direction"] == "increase") or "acetic" in dtags:
        targets["acetic"] = {"target": 0.4, "mode": "at_least", "weight": 0.25,
                             "reason": "more aroma/complexity: favor a cool (acetic-leaning) phase"}
    if ar and ar["direction"] == "decrease":
        loaf_tags = set()
        for l in LOAVES:
            loaf_tags |= set(((interp.get("loaves") or {}).get(l) or {}).get("aroma_tags") or [])
        if "acetic" in loaf_tags or "acetic" in dtags:
            targets["acetic"] = {"target": 0.25, "mode": "at_most", "weight": 0.3,
                                 "reason": "less vinegary: avoid long cool (acetic-leaning) phases"}
        if "alcoholic" in loaf_tags:
            reasons.append("boozy/alcoholic aroma suggests overfermentation: overproof penalty doubled")

    preferred = interp.get("preferred_loaf")
    if preferred and not targets and judged.get(preferred):
        for metric in ("sourness", "openness"):
            if metric in judged[preferred]:
                targets[metric] = {"target": judged[preferred][metric], "mode": "near", "weight": 0.8,
                                   "reason": f"reproduce preferred Loaf {preferred}"}
        reasons.append(f"judge preferred Loaf {preferred}: replicate its profile with lower overproof risk")

    overproof_weight = 1.0
    flags = interp.get("flags") or []
    if any("overproof" in f for f in flags) or any("alcoholic" in r for r in reasons):
        overproof_weight = 2.0
    return {
        "targets": targets,
        "explore": not targets,
        "judged": judged,
        "overproof_weight": overproof_weight,
        "notes": reasons,
        "preferred_loaf": preferred,
    }


# ---------------------------------------------------------------------------
# Evidence: anchored predictions for candidates
# ---------------------------------------------------------------------------
def _anchor(points: list[tuple[float, float]], metric: str) -> dict[str, Any]:
    """points: [(model_index, judged_score)]; returns judge = a + b * model."""
    if len(points) >= 2:
        (m1, j1), (m2, j2) = points[0], points[1]
        if abs(m2 - m1) > 0.02:
            b = (j2 - j1) / (m2 - m1)
            if b <= 0:
                a = sum(j - m for m, j in points) / len(points)
                return {"slope": 1.0, "intercept": round(a, 4), "n_points": len(points), "status": "model_judge_disagree",
                        "note": f"Judged {metric} order contradicts the model's order for the recorded loaves; "
                                f"using an offset-only map (low confidence)."}
            b_c = min(6.0, max(0.25, b))
            a = (j1 + j2) / 2 - b_c * (m1 + m2) / 2
            return {"slope": round(b_c, 4), "intercept": round(a, 4), "n_points": 2,
                    "status": "anchored" if b_c == b else "anchored_slope_clamped",
                    "note": f"Linear map through both judged loaves (slope {b:.2f}{' clamped' if b_c != b else ''})."}
        a = sum(j - m for m, j in points) / len(points)
        return {"slope": 1.0, "intercept": round(a, 4), "n_points": len(points), "status": "offset_only",
                "note": "Recorded loaves predicted alike; offset-only map."}
    if len(points) == 1:
        m, j = points[0]
        return {"slope": 1.0, "intercept": round(j - m, 4), "n_points": 1, "status": "offset_only",
                "note": "One judged loaf: offset-only map."}
    return {"slope": 1.0, "intercept": 0.0, "n_points": 0, "status": "unanchored",
            "note": "No judged loaves for this attribute: raw model index used."}


def _j(anchor: dict[str, Any], model_value: float) -> float:
    return round(min(0.98, max(0.02, anchor["intercept"] + anchor["slope"] * model_value)), 3)


def build_evidence(interp: dict[str, Any], context: dict[str, Any], use_simulator: bool = True) -> dict[str, Any]:
    """Shared, deterministic evidence base every agent can recompute from its own inputs."""
    tg = build_targets(interp)
    recorded = recorded_schedules(context)
    rec_pred = {l: S.predict_schedule(recorded[l]["points"], use_simulator) for l in LOAVES}
    judged = tg["judged"]
    anchors = {}
    for metric, key in (("sourness", "sourness_index"), ("openness", "openness_index")):
        pts = [(rec_pred[l][key], judged[l][metric]) for l in LOAVES if metric in judged[l]]
        anchors[metric] = _anchor(pts, metric)
    cands = []
    for c in S.LIBRARY:
        p = S.predict_schedule(c.points, use_simulator)
        cands.append({
            "id": c.id, "name": c.name, "family": c.family, "route": c.route,
            "points": c.as_list(), "summary": c.summary, "mechanism": c.mechanism,
            "model": p,
            "pred_sourness": _j(anchors["sourness"], p["sourness_index"]),
            "pred_openness": _j(anchors["openness"], p["openness_index"]),
        })
    sources = sorted({c["model"]["provenance"] for c in cands})
    prediction_source = "simulated" if sources == ["simulated"] else "estimated" if sources == ["estimated"] else "mixed"
    recorded_out = {}
    for l in LOAVES:
        recorded_out[l] = {**recorded[l], "points": [list(p) for p in recorded[l]["points"]],
                           "model": rec_pred[l],
                           "pred_sourness": _j(anchors["sourness"], rec_pred[l]["sourness_index"]),
                           "pred_openness": _j(anchors["openness"], rec_pred[l]["openness_index"]),
                           "judged": judged[l]}
    return {
        "policy": POLICY_ID,
        "targets": tg,
        "recorded": recorded_out,
        "anchors": anchors,
        "candidates": cands,
        "prediction_source": prediction_source,
        "simulator_used": prediction_source in ("simulated", "mixed"),
    }


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def _loss(pred: float, t: dict[str, Any]) -> float:
    if t["mode"] == "near":
        return (pred - t["target"]) ** 2
    if t["mode"] == "at_least":
        return max(0.0, t["target"] - pred) ** 2
    return max(0.0, pred - t["target"]) ** 2


def _features(c: dict[str, Any]) -> tuple[float, float, float, float]:
    m = c["model"]
    return (c["pred_sourness"], c["pred_openness"], m["mean_temp_c"] / 10.0, m["acetic_index"])


def _dist(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def candidate_cost(c: dict[str, Any], evidence: dict[str, Any], risk_weight: float = 1.0) -> dict[str, float]:
    tg = evidence["targets"]
    t = tg["targets"]
    m = c["model"]
    parts = {}
    if tg["explore"]:
        rec_feats = [(r["pred_sourness"], r["pred_openness"], r["model"]["mean_temp_c"] / 10.0, r["model"]["acetic_index"])
                     for r in evidence["recorded"].values()]
        novelty = min(_dist(_features(c), rf) for rf in rec_feats)
        parts["novelty"] = -0.5 * min(novelty, 0.6)
    if "sourness" in t:
        parts["sourness"] = t["sourness"]["weight"] * _loss(c["pred_sourness"], t["sourness"])
    if "openness" in t:
        parts["openness"] = t["openness"]["weight"] * _loss(c["pred_openness"], t["openness"])
    if "acetic" in t:
        parts["acetic"] = t["acetic"]["weight"] * _loss(m["acetic_index"], t["acetic"])
    parts["overproof"] = 0.12 * risk_weight * tg["overproof_weight"] * m["overproof_risk"] ** 2
    parts["underproof"] = 0.12 * risk_weight * m.get("underproof_risk", 0.0) ** 2
    parts["total"] = round(sum(parts.values()), 5)
    return {k: round(v, 5) for k, v in parts.items()}


def rank_candidates(
    evidence: dict[str, Any],
    *,
    risk_weight: float = 1.0,
    avoid: dict[str, Any] | None = None,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank validated candidates; ``avoid`` = a peer's chosen candidate (redundancy penalty)."""
    rows = []
    for c in evidence["candidates"]:
        if exclude_ids and c["id"] in exclude_ids:
            continue
        cost = candidate_cost(c, evidence, risk_weight)
        total = cost["total"]
        red = 0.0
        if avoid is not None:
            if c["id"] == avoid["id"]:
                red = 1.0
            else:
                d = _dist(_features(c), _features(avoid))
                red = 0.03 * max(0.0, 1.0 - d / 0.35) + (0.015 if c["route"] == avoid["route"] else 0.0)
        rows.append({**c, "cost": cost, "redundancy_penalty": round(red, 5), "score": round(total + red, 5)})
    rows.sort(key=lambda r: (r["score"], r["id"]))
    return rows


# ---------------------------------------------------------------------------
# Lead selection
# ---------------------------------------------------------------------------
def choose_lead(interp: dict[str, Any], evidence: dict[str, Any], setpoints: dict[str, float]) -> tuple[str, str]:
    judged = evidence["targets"]["judged"]
    prim = interp.get("primary_dimension")
    desired = interp.get("desired") or {}
    warm = max(LOAVES, key=lambda l: (setpoints.get(l, 0), l == "B"))
    cool = "A" if warm == "B" else "B"
    pref = interp.get("preferred_loaf")
    if prim in ("sourness", "flavor_intensity", "aroma"):
        up = desired[prim]["direction"] == "increase"
        vals = {l: judged[l]["sourness"] for l in LOAVES if "sourness" in judged[l]}
        if len(vals) == 2 and vals["A"] != vals["B"]:
            lead = max(vals, key=vals.get) if up else min(vals, key=vals.get)
            return lead, f"Loaf {lead} was judged closest to the requested {'more' if up else 'less'} sour direction"
        return (warm if up else cool), f"chamber {(warm if up else cool)} ran the {'warmer' if up else 'cooler'} condition"
    if prim in ("texture", "density"):
        d = desired[prim]
        up_open = (d["direction"] == "increase") == (prim == "texture")
        vals = {l: judged[l]["openness"] for l in LOAVES if "openness" in judged[l]}
        if len(vals) == 2 and vals["A"] != vals["B"]:
            lead = max(vals, key=vals.get) if up_open else min(vals, key=vals.get)
            return lead, f"Loaf {lead} had the crumb closest to the requested texture"
        return cool, f"chamber {cool} ran the cooler (lower overproof risk) condition"
    if pref:
        return pref, f"the judge preferred Loaf {pref}"
    return "A", "no explicit direction: chamber A opens an exploratory round (tie-break)"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def fmt_target(t: dict[str, Any]) -> str:
    sym = {"near": "≈", "at_least": "≥", "at_most": "≤"}[t["mode"]]
    return f"{sym}{t['target']:.2f}"


def prediction_uncertainty(c: dict[str, Any], evidence: dict[str, Any], widen: float = 0.0) -> float:
    """Heuristic ± band on the judge scale: grows with extrapolation from recorded loaves."""
    rec_feats = [(r["pred_sourness"], r["pred_openness"], r["model"]["mean_temp_c"] / 10.0, r["model"]["acetic_index"])
                 for r in evidence["recorded"].values()]
    d = min(_dist(_features(c), rf) for rf in rec_feats)
    n = min(evidence["anchors"]["sourness"]["n_points"], evidence["anchors"]["openness"]["n_points"])
    base = 0.12 if n >= 2 else 0.18 if n == 1 else 0.25
    return round(min(0.4, base + 0.2 * d + widen), 2)


def describe_prediction(c: dict[str, Any], evidence: dict[str, Any], widen: float = 0.0) -> str:
    m = c["model"]
    u = prediction_uncertainty(c, evidence, widen)
    src = "simulator" if m["provenance"] == "simulated" else "degree-hour heuristic"
    s = (f"Predicted ({src}, anchored on judged loaves): sourness {c['pred_sourness']:.2f} ± {u:.2f}, "
         f"crumb openness {c['pred_openness']:.2f} ± {u:.2f}; rise progress {m['rise_progress']:.2f}× peak "
         f"(overproof risk {m['overproof_risk']:.0%})")
    if m.get("time_to_peak_rise_h") is not None:
        s += f", peak rise expected near {m['time_to_peak_rise_h']:.1f} h"
    if m.get("final_ph") is not None:
        s += f", final pH ≈ {m['final_ph']:.2f} (simulated)"
    return s + "."


def describe_judged(interp: dict[str, Any]) -> str:
    bits = []
    for l in LOAVES:
        attrs = ((interp.get("loaves") or {}).get(l) or {}).get("attributes") or {}
        if not attrs:
            continue
        desc = ", ".join(f"{d} {v['label']} ({v['score']:.2f})" for d, v in attrs.items() if d in ("sourness", "texture", "density", "aroma"))
        if desc:
            bits.append(f"Loaf {l}: {desc}")
    un = (interp.get("unattributed") or {}).get("attributes") or {}
    if un:
        bits.append("unattributed: " + ", ".join(f"{d} {v['label']}" for d, v in un.items()))
    return "; ".join(bits) if bits else "no loaf-specific descriptions"


def describe_recorded(evidence: dict[str, Any]) -> str:
    out = []
    for l in LOAVES:
        r = evidence["recorded"][l]
        m = r["model"]
        s = f"{l}: {r['source'] if r['experiment_id'] is None else r['experiment_id']}"
        s += f" -> model rise progress {m['rise_progress']:.2f}× peak"
        if r["violations"]:
            s += " (exceeded the 10 h hot-hold guideline)"
        out.append(s)
    return "; ".join(out)


ALT_HYPOTHESIS = {
    "total_acid_warm": "Perceived sourness may depend more on the acetic share than on total acid; a long cool phase "
                       "could raise sharpness without pushing the dough past peak rise.",
    "overproof_protection": "Perceived sourness may depend more on the acetic share than on total acid; a long cool "
                            "phase could raise sharpness without needing a hot start at all.",
    "acetic_cool": "Perceived sourness may track total acid, which builds fastest when warm; a cool route might only "
                   "give a milder loaf with a slightly sharper aroma.",
    "baseline": "The schedule's shape, not its mean temperature, may control the sourness/structure trade-off.",
    "dose_response": "The response may be non-linear (threshold near the yeast optimum); shape tests may be more "
                     "informative than intermediate steady temperatures.",
}

ROUTE_SHORT = {
    "total_acid_warm": "warm acid route",
    "overproof_protection": "warm start + step-down",
    "acetic_cool": "cool acetic route",
    "baseline": "baseline control",
    "dose_response": "intermediate temperature",
}


def uncertainty_text(c: dict[str, Any], evidence: dict[str, Any], extra: list[str] | None = None,
                     widen: float = 0.0) -> str:
    a = evidence["anchors"]
    parts = []
    src = evidence["prediction_source"]
    if src == "estimated":
        parts.append("Predictions come from an uncalibrated degree-hour heuristic (estimated), not a fitted kinetic model")
    elif src == "simulated":
        parts.append("Predictions come from the fermentation simulator (uncalibrated against this starter)")
    else:
        parts.append("Predictions mix simulator and heuristic outputs")
    parts.append(f"the judge-scale map rests on {a['sourness']['n_points']} judged loaf/loaves for sourness "
                 f"({a['sourness']['status']}) and {a['openness']['n_points']} for crumb ({a['openness']['status']})")
    parts.append(f"band ±{prediction_uncertainty(c, evidence, widen):.2f} grows with distance from the recorded "
                 f"conditions" + (f" (widened by {widen:.2f} for missing/invalid observations)" if widen else ""))
    parts.append("one judge, one tasting, one shared pH probe; perceived sourness ≠ pH (lactic/acetic balance matters)")
    if extra:
        parts.extend(extra)
    return "; ".join(parts) + "."


def measurement_plan(c: dict[str, Any]) -> str:
    steps = [p for p in zip(c["points"], c["points"][1:]) if p[0][1] != p[1][1]]
    step_txt = f" and at each temperature change (t≈{', '.join(f'{p[0][0]:g}' for p in steps)} h)" if steps else ""
    return (f"Side-camera starter height every 10 min (end at peak/plateau, not by the clock); pH spot checks on a "
            f"separate non-edible sample at 0 h{step_txt} and at the end (single shared probe); chamber temperature "
            f"continuously; blind tasting for sourness, crumb texture and aroma plus a crumb photo.")


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def lead_decision(interp: dict[str, Any], evidence: dict[str, Any], chamber: str, *, risk_weight: float,
                  lead_reason: str, obs_notes: list[str] | None = None,
                  widen: float = 0.0) -> tuple[LeadDecision, dict[str, Any]]:
    ranked = rank_candidates(evidence, risk_weight=risk_weight)
    best = ranked[0]
    # suggestion for the peer chamber: best candidate testing a different route (complementary arm)
    peer_opts = [r for r in rank_candidates(evidence, risk_weight=risk_weight, avoid=best) if r["route"] != best["route"]]
    sugg = peer_opts[0] if peer_opts else ranked[1]
    tg = evidence["targets"]
    t = tg["targets"]
    peer = "B" if chamber == "A" else "A"
    target_txt = ", ".join(f"{k} {fmt_target(v)}" for k, v in t.items()) or "none stated (exploration)"
    if tg["explore"]:
        stmt = (f"No explicit preference was given, so the most informative next step for chamber {chamber} is "
                f"'{best['name']}', the validated schedule farthest from both recorded conditions in predicted "
                f"outcome space. {best['mechanism']}")
    else:
        stmt = (f"Running '{best['name']}' in chamber {chamber} will move the loaf toward the judge's target "
                f"({interp.get('desired_summary', '').rstrip('.')}). {best['mechanism']}")
    shortfalls = []
    if "sourness" in t and t["sourness"]["mode"] == "near" and abs(best["pred_sourness"] - t["sourness"]["target"]) > 0.12:
        shortfalls.append(f"no validated schedule is predicted to reach sourness {t['sourness']['target']:.2f} "
                          f"(best {best['pred_sourness']:.2f}) without passing peak rise; this run tests how far "
                          f"the {ROUTE_SHORT[best['route']]} can go")
    ru = ranked[1]
    rationale = (f"I lead because {lead_reason}. Of {len(evidence['candidates'])} validated candidates, "
                 f"'{best['name']}' has the lowest cost {best['score']:.4f} against targets [{target_txt}] "
                 f"(runner-up '{ru['name']}' {ru['score']:.4f}). Recorded conditions: {describe_recorded(evidence)}.")
    dec = LeadDecision(
        observation_summary=f"Judge feedback interpreted as: {describe_judged(interp)}. Desired: "
                            f"{interp.get('desired_summary', 'n/a')}",
        hypothesis_statement=stmt,
        mechanism=f"{best['mechanism']} ({S.ROUTE_TEXT.get(best['route'], best['route'])})",
        own_schedule_id=best["id"],
        peer_suggestion_schedule_id=sugg["id"],
        peer_suggestion_reason=(f"Chamber {peer} could run '{sugg['name']}' ({ROUTE_SHORT[sugg['route']]}) so the two "
                                f"chambers test different mechanisms instead of duplicating one."),
        prediction=describe_prediction(best, evidence, widen),
        uncertainty=uncertainty_text(best, evidence, list(obs_notes or []) + shortfalls, widen),
        proposed_measurement=measurement_plan(best),
        rationale=rationale,
    )
    return dec, {"ranked": ranked[:5], "best": best, "suggestion": sugg}


def _find(evidence: dict[str, Any], cid: str) -> dict[str, Any]:
    for c in evidence["candidates"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def critique_points(lead_choice: dict[str, Any], evidence: dict[str, Any], own_obs: str | None) -> list[str]:
    t = evidence["targets"]["targets"]
    m = lead_choice["model"]
    pts = []
    if m["overproof_risk"] > 0.3:
        pts.append(f"'{lead_choice['name']}' is predicted to reach {m['rise_progress']:.2f}× peak rise "
                   f"(overproof risk {m['overproof_risk']:.0%}) — the same failure mode that plausibly made the warm "
                   f"loaf dense; end at the plateau rather than the clock.")
    if "openness" in t and _loss(lead_choice["pred_openness"], t["openness"]) > 0.0025:
        pts.append(f"predicted crumb openness {lead_choice['pred_openness']:.2f} misses the target "
                   f"{fmt_target(t['openness'])}.")
    if "sourness" in t and _loss(lead_choice["pred_sourness"], t["sourness"]) > 0.01:
        pts.append(f"predicted sourness {lead_choice['pred_sourness']:.2f} vs target {fmt_target(t['sourness'])}: "
                   f"the requested change may not be reachable on this route alone.")
    a = evidence["anchors"]["sourness"]
    if a["status"] in ("model_judge_disagree", "anchored_slope_clamped") or a["n_points"] < 2:
        pts.append(f"the sourness calibration is weak ({a['note']}).")
    elif a["slope"] > 3:
        pts.append(f"only two judged loaves calibrate the sourness scale and the fitted slope is steep "
                   f"(×{a['slope']:.1f}), so small model errors become large predicted sourness differences.")
    if lead_choice["route"] in ("total_acid_warm", "overproof_protection"):
        hot = max(p[1] for p in lead_choice["points"])
        rate = S.Q10_RISE ** ((hot - S.REF_TEMP_C) / 10.0) / S.RISE_PEAK_REF_H
        ends = "the step-down" if lead_choice["family"] != "steady" and lead_choice["family"] != "warm_short" else "ending"
        pts.append(f"timing sensitivity: at {hot:g} °C each extra hour adds ≈{rate:.2f}× of peak-rise progress "
                   f"(heuristic), so {ends} must happen on time.")
    if lead_choice["route"] == "acetic_cool":
        pts.append("the acetic shift at cool temperatures is an assumption in our model; we have no acid-specific "
                   "sensor to confirm it (pH alone cannot separate lactic from acetic).")
    if own_obs:
        if "telemetry (" in own_obs:
            pts.insert(0, own_obs)
        else:
            pts.append(own_obs)
    return pts


def peer_decision(interp: dict[str, Any], evidence: dict[str, Any], chamber: str, lead_msg_payload: dict[str, Any],
                  *, risk_weight: float, own_obs: str | None = None, communicate: bool = True,
                  own_has_judged_outcome: bool = True, obs_notes: list[str] | None = None,
                  widen: float = 0.0) -> tuple[PeerDecision, dict[str, Any]]:
    lead_choice = _find(evidence, lead_msg_payload["own_decision"]["schedule_id"])
    sugg_id = (lead_msg_payload.get("suggestion_for_peer") or {}).get("schedule_id")
    independent = rank_candidates(evidence, risk_weight=risk_weight)
    indep_choice = independent[0]
    if not communicate:
        choice = indep_choice
        accepted = False
        note = "Independent mode: I did not read the peer's message; this is my stand-alone choice."
        ranked = independent
    else:
        ranked = rank_candidates(evidence, risk_weight=risk_weight, avoid=lead_choice)
        best = ranked[0]
        sugg = next((r for r in ranked if r["id"] == sugg_id), None)
        if sugg is not None and (sugg["id"] == best["id"] or sugg["score"] <= best["score"] * 1.1 + 0.002 or not own_has_judged_outcome):
            choice, accepted = sugg, True
        else:
            choice, accepted = best, False
        if choice["id"] != indep_choice["id"]:
            why = ("it would duplicate the proposer's arm" if indep_choice["id"] == lead_choice["id"]
                   else "a complementary arm is more informative next to the proposer's choice")
            note = (f"Without the proposer's message I would have chosen '{indep_choice['name']}'; I switched to "
                    f"'{choice['name']}' because {why}.")
        else:
            note = f"The proposer's message did not change my choice ('{choice['name']}' was already my best option)."
    concerns = critique_points(lead_choice, evidence, own_obs)
    alt = ALT_HYPOTHESIS.get(lead_choice["route"], ALT_HYPOTHESIS["baseline"])
    agrees = lead_choice["model"]["overproof_risk"] <= 0.5 and len([c for c in concerns if "misses" in c]) == 0
    t = evidence["targets"]["targets"]
    if accepted:
        acc_txt = f"I accept your suggestion for chamber {chamber}: '{choice['name']}'."
    elif communicate and sugg_id:
        sugg_c = _find(evidence, sugg_id)
        acc_txt = (f"I decline your suggestion '{sugg_c['name']}' for chamber {chamber}; from my own ranking "
                   f"'{choice['name']}' scores better ({choice['score']:.4f}) as a complementary arm.")
    else:
        acc_txt = f"For chamber {chamber} I choose '{choice['name']}'."
    stance = "I agree the direction is plausible, but:" if agrees else "I am not convinced yet:"
    critique = (f"{stance} " + " ".join(f"({i + 1}) {c}" for i, c in enumerate(concerns[:5]))
                + f" Alternative hypothesis: {alt} {acc_txt}")
    dec = PeerDecision(
        critique=critique,
        agrees_with_hypothesis=agrees,
        concerns=concerns,
        alternative_hypothesis=alt,
        own_schedule_id=choice["id"],
        accepted_peer_suggestion=accepted,
        decision_change_note=note,
        prediction=describe_prediction(choice, evidence, widen),
        uncertainty=uncertainty_text(choice, evidence, list(obs_notes or []), widen),
        proposed_measurement=measurement_plan(choice) + " Retain a slice of each loaf for a side-by-side re-taste.",
        rationale=(f"My chamber runs '{choice['name']}' ({ROUTE_SHORT[choice['route']]}) so that, together with "
                   f"'{lead_choice['name']}' ({ROUTE_SHORT[lead_choice['route']]}), the round discriminates between "
                   f"the two mechanisms. Targets: "
                   + (", ".join(f"{k} {fmt_target(v)}" for k, v in t.items()) or "exploration") + "."),
    )
    return dec, {"ranked": ranked[:5], "choice": choice, "independent_choice": indep_choice, "lead_choice": lead_choice}


def lead_reply(evidence: dict[str, Any], lead_choice_id: str, peer: PeerDecision, *, risk_weight: float,
               peer_choice_id: str) -> dict[str, Any]:
    """Proposer's response to the critique: keep or revise its own chamber's schedule."""
    lead_choice = _find(evidence, lead_choice_id)
    peer_choice = _find(evidence, peer_choice_id)
    revised_to = None
    risk = lead_choice["model"]["overproof_risk"]
    if risk > 0.6:
        ranked = rank_candidates(evidence, risk_weight=risk_weight * 1.5, avoid=peer_choice)
        own_row = next(r for r in ranked if r["id"] == lead_choice["id"])
        def fit(r):  # distance to the judge's targets only (risk terms excluded)
            return sum(v for k, v in r["cost"].items() if k not in ("total", "overproof", "underproof"))

        alt = next((r for r in ranked if r["model"]["overproof_risk"] <= risk - 0.15
                    and r["score"] <= own_row["score"] * 1.25 + 0.002 and r["id"] != peer_choice["id"]
                    and fit(r) <= fit(own_row) + 0.05), None)
        if alt is not None and alt["id"] != lead_choice["id"]:
            revised_to = alt
    if revised_to:
        text = (f"Point taken on overproofing: I revise my chamber's schedule from '{lead_choice['name']}' to "
                f"'{revised_to['name']}' (overproof risk {revised_to['model']['overproof_risk']:.0%}). "
                f"I adopt your measurement plan.")
        final = revised_to
    else:
        raised = "overproof" in (peer.critique + " " + " ".join(peer.concerns)).lower()
        if risk > 0.6:
            lead_in = (f"Your overproofing concern stands (risk {risk:.0%})" if raised
                       else f"Note: my own estimate of overproof risk is {risk:.0%}")
            keep = (f"{lead_in}, but no validated alternative lowers it without giving up most of the predicted gain, "
                    f"so I keep '{lead_choice['name']}' and will end at the rise plateau from the starter-height curve "
                    f"rather than the clock.")
        else:
            keep = (f"I keep '{lead_choice['name']}' (overproof risk {risk:.0%}, within tolerance) and will end at the "
                    f"rise plateau if it comes early.")
        if lead_choice["route"] != peer_choice["route"]:
            contrast = f"{ROUTE_SHORT[lead_choice['route']]} vs {ROUTE_SHORT[peer_choice['route']]}"
        else:
            contrast = (f"two variants of the {ROUTE_SHORT[lead_choice['route']]} ('{lead_choice['name']}' vs "
                        f"'{peer_choice['name']}')")
        text = (f"Thanks. {keep} I adopt your proposal to retain slices for a side-by-side re-taste. The round now "
                f"contrasts {contrast}.")
        final = lead_choice
    return {"content": text, "revised": revised_to is not None, "final": final}
