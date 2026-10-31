"""Scientist agents: one logical agent per chamber, each with its own state (PLAN §9-§10).

Each :class:`ScientistAgent` keeps its own history, hypotheses and decisions and only
learns about its peer through the shared findings log. Decisions come either from the
deterministic offline policy or from an LLM (same structured decision models), but are
always published by the owning agent and always restricted to validated library schedules.

Also contains the §5 overproofing rule (:func:`assess_overproofing`) and the starter
readiness checklist (:func:`starter_readiness`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import offline_policy as P
from . import schedules as S
from .communication import Finding, FindingsLog, challenge, new_finding
from .observation import (
    ChamberObservation,
    compare_to_prediction,
    normalize_history,
    summarize_history,
)

DEFAULT_OBJECTIVES = {
    "A": "Mild hypothesis: relatively mild acidity with sufficient expansion (initial 22 °C).",
    "B": "More-acidic hypothesis: increased acidification and detectable flavor differences (initial 27 °C).",
}


# ---------------------------------------------------------------------------
# Telemetry helpers and the overproofing rule
# ---------------------------------------------------------------------------
def summarize_telemetry(rows: list[dict[str, Any]] | None, chamber_id: str | None = None,
                        now_utc: str | None = None) -> dict[str, Any]:
    """Quality-aware summary (only quality-'ok' values are reported as measurements)."""
    return _summary_from_obs(normalize_history(rows, chamber_id, now_utc))


def _summary_from_obs(obs: list[ChamberObservation]) -> dict[str, Any]:
    q = summarize_history(obs)
    out: dict[str, Any] = {"n": len(obs), "sources": q.get("sources", []), "quality": q}
    if not obs:
        return out
    el = [o.derived.get("elapsed_h") for o in obs]
    if all(e is not None for e in el):
        out["span_h"] = round(el[-1] - el[0], 2)
    else:
        out["timestamps_missing"] = True
    temps = [o.value("chamber_temperature") for o in obs if o.value("chamber_temperature") is not None]
    if temps:
        out["latest_temp_c"] = round(float(temps[-1]), 2)
        out["mean_temp_c"] = round(sum(temps) / len(temps), 2)
    st = [o.value("starter_temperature") for o in obs if o.value("starter_temperature") is not None]
    if st:
        out["latest_starter_temp_c"] = round(float(st[-1]), 2)
    sps = [o.value("temperature_setpoint") for o in obs if o.value("temperature_setpoint") is not None]
    if sps:
        out["latest_setpoint_c"] = float(sps[-1])
    phs = [o.value("ph") for o in obs if o.value("ph") is not None]
    if phs:
        out["latest_ph"] = round(float(phs[-1]), 2)
        out["ph_points"] = len(phs)
    return out


def _predicted_peak_h(pred: Any) -> float | None:
    """Accepts {"time_h": [...], "expansion_ratio": [...]}, a list of row dicts or a DataFrame."""
    try:
        if hasattr(pred, "columns") and "expansion_ratio" in pred.columns:
            ts, ex = [float(x) for x in pred["time_h"]], [float(x) for x in pred["expansion_ratio"]]
        elif isinstance(pred, dict) and "time_h" in pred and "expansion_ratio" in pred:
            ts, ex = list(pred["time_h"]), list(pred["expansion_ratio"])
        elif isinstance(pred, list) and pred and isinstance(pred[0], dict):
            ts = [float(r["time_h"]) for r in pred]
            ex = [float(r["expansion_ratio"]) for r in pred]
        else:
            return None
        i = max(range(len(ex)), key=lambda k: float(ex[k]))
        return float(ts[i])
    except (KeyError, TypeError, ValueError):
        return None


def assess_overproofing(
    telemetry: list[dict[str, Any]] | None,
    prediction: Any = None,
    elapsed_h: float | None = None,
    setpoint_c: float | None = None,
    *,
    chamber_id: str | None = None,
    now_utc: str | None = None,
    observations: list[ChamberObservation] | None = None,
) -> dict[str, Any]:
    """PLAN §5 rule: near/after peak expansion -> end fermentation or step down; never hold blindly.

    Quality-aware (PLAN_MASTER §9.2): only height readings with quality ``ok`` are used;
    low-confidence, invalid, wrong-chamber or unsynced readings are excluded and counted. If the
    latest telemetry is stale or its age cannot be verified, no intervention is proposed.

    Returns {"status": rising|near_peak|declining|insufficient_data|stale_data,
             "action": continue|step_down|end_fermentation|monitor,
             "suggested_setpoint_c", "rationale", "evidence"}.
    Actions are *proposals*: the bake decision requires human confirmation and every
    setpoint is clamped by the API/firmware.
    """
    obs = observations if observations is not None else normalize_history(telemetry, chamber_id, now_utc)
    hist = summarize_history(obs)
    heights = [(o.derived["elapsed_h"], float(o.value("starter_height"))) for o in obs
               if o.value("starter_height") is not None and o.derived.get("elapsed_h") is not None]
    excluded = {}
    for o in obs:
        f = o.fields["starter_height"]
        if f.value is not None and f.quality != "ok":
            excluded[f.quality] = excluded.get(f.quality, 0) + 1
    ev: dict[str, Any] = {"telemetry_points": len(obs), "sources": hist.get("sources", []),
                          "usable_height_points": len(heights), "excluded_height_points": excluded}
    status = "insufficient_data"
    reason = "fewer than 3 quality-approved starter-height readings"
    if excluded:
        reason += f" ({sum(excluded.values())} excluded: {', '.join(f'{k}={v}' for k, v in sorted(excluded.items()))})"
    latest = obs[-1] if obs else None
    latest_bad = latest is not None and any(f.quality in ("stale", "unsynced_time") and f.role == "expansion_estimate"
                                            or f.quality in ("stale", "unsynced_time") and f.role == "controlled_variable"
                                            for f in latest.fields.values())
    if latest_bad:
        status = "stale_data"
        age = latest.age_s
        reason = (f"latest telemetry is {age / 60:.0f} min old" if age is not None
                  else "age of the latest telemetry cannot be verified (no synced clock/receipt time)")
    elif len(heights) >= 3:
        h0 = heights[0][1] or 1e-9
        peak_t, peak_h = max(heights, key=lambda x: x[1])
        lt, lh = heights[-1]
        recent = [x for x in heights if x[0] >= lt - 1.0]
        slope = (recent[-1][1] - recent[0][1]) / max(1e-6, recent[-1][0] - recent[0][0]) / h0 if len(recent) >= 2 else 0.0
        ev.update({"initial_mm": h0, "peak_mm": peak_h, "peak_at_h": round(peak_t, 2), "latest_mm": lh,
                   "rise_ratio": round(lh / h0, 3), "peak_ratio": round(peak_h / h0, 3),
                   "recent_slope_per_h": round(slope, 4)})
        if lh < 0.97 * peak_h and lt - peak_t >= 0.25:
            status, reason = "declining", f"height fell {100 * (1 - lh / peak_h):.1f}% below its peak at {peak_t:.1f} h"
        elif peak_h / h0 >= 1.9 or (peak_h / h0 >= 1.5 and slope <= 0.02):
            status, reason = "near_peak", f"rise ratio {peak_h / h0:.2f}× with recent slope {slope:.3f}/h (plateauing)"
        else:
            status, reason = "rising", f"rise ratio {lh / h0:.2f}× and still climbing ({slope:.3f}/h)"
    pk = _predicted_peak_h(prediction) if prediction is not None else None
    if elapsed_h is None and heights:
        elapsed_h = heights[-1][0]
    if pk is not None:
        ev["predicted_peak_h"] = round(pk, 2)
        if status in ("rising", "insufficient_data") and elapsed_h is not None and elapsed_h >= pk - 0.5:
            status = "near_peak"
            reason = f"model predicts peak expansion at {pk:.1f} h and {elapsed_h:.1f} h have elapsed (prediction, not observation)"
    if setpoint_c is not None:
        sp = setpoint_c
    else:
        sps = [o.value("temperature_setpoint") for o in obs if o.value("temperature_setpoint") is not None]
        sp = sps[-1] if sps else None
    if status == "declining":
        action = "end_fermentation"
        text = ("Expansion is declining: propose ending fermentation now (shape/bake after human confirmation); "
                "if baking must wait, drop the chamber to its coolest safe setpoint.")
        new_sp = max(S.MIN_TEMP_C, min(16.0, sp - 6.0)) if sp is not None else 16.0
    elif status == "near_peak":
        action = "step_down"
        new_sp = max(S.MIN_TEMP_C, min(20.0, (sp - 4.0) if sp is not None else 20.0))
        text = (f"Near peak expansion: do not keep holding{f' {sp:g} °C' if sp is not None else ''}; propose stepping "
                f"down to {new_sp:g} °C or ending fermentation when the plateau is confirmed.")
    elif status == "rising":
        action, new_sp, text = "continue", None, "Still rising: continue, re-check every 10-15 min."
    elif status == "stale_data":
        action, new_sp, text = "monitor", None, ("Telemetry is stale or its age is unverifiable: no intervention is "
                                                 "proposed on old data; restore the device link/clock first.")
    else:
        action, new_sp, text = "monitor", None, "Not enough reliable rise data to judge proofing; keep logging starter height."
    return {"status": status, "action": action, "suggested_setpoint_c": new_sp, "rationale": f"{text} ({reason})",
            "evidence": ev, "requires_human_confirmation": action == "end_fermentation"}


def additional_actions(hist: dict[str, Any], deviations: dict[str, Any] | None, latest: ChamberObservation | None,
                       chamber_id: str) -> list[dict[str, Any]]:
    """Measurement requests / diagnostics implied by data quality, anomalies and deviations."""
    acts: list[dict[str, Any]] = []
    for a in hist.get("anomalies", []):
        if a["category"] == "equipment_safety" and a["severity"] in ("warning", "critical"):
            acts.append({"action": "equipment_diagnostics", "reason": a["message"], "scope": "equipment",
                         "note": "equipment safety, not a fermentation finding"})
        elif a["code"] in ("wrong_chamber", "simulated_labelled_as_measured", "simulated_field_in_measured_record"):
            acts.append({"action": "fix_data_source", "reason": a["message"]})
        elif a["code"] in ("setpoint_tracking_error", "control_direction_mismatch"):
            acts.append({"action": "check_temperature_control", "reason": a["message"]})
    if hist.get("n") and not hist.get("ph_valid"):
        acts.append({"action": "request_measurement", "what": "pH spot check (calibrated probe, separate sample)",
                     "reason": hist.get("ph_reason")})
    if latest is not None and latest.fields["starter_height"].quality == "low_confidence":
        acts.append({"action": "request_measurement", "what": "verify camera framing/lighting or take a manual height",
                     "reason": latest.fields["starter_height"].note})
    for flag in (deviations or {}).get("flags", []):
        acts.append({"action": "investigate_deviation", "what": flag, "chamber": chamber_id})
    seen, out = set(), []
    for a in acts:
        key = (a["action"], a.get("what"), a.get("reason"))
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def starter_readiness(
    doubling_time_h: float | None = None,
    abundant_bubbles: bool | None = None,
    clean_aroma: bool | None = None,
    mold_or_discoloration: bool | None = None,
    age_days: float | None = None,
    consecutive_doublings: int | None = None,
) -> dict[str, Any]:
    """PLAN §5 starter readiness checklist with an explicitly labeled contingency."""
    items = []

    def item(name, ok, detail):
        items.append({"check": name, "status": "unknown" if ok is None else ("pass" if ok else "fail"), "detail": detail})

    dbl = None if doubling_time_h is None else (doubling_time_h <= 8.0)
    item("Reliably doubles within ~6-8 h after feeding", dbl if consecutive_doublings is None or not dbl else
         (dbl and consecutive_doublings >= 2),
         f"observed doubling time {doubling_time_h} h" + (f", {consecutive_doublings} consecutive feeds" if consecutive_doublings is not None else ""))
    item("Abundant bubbles", abundant_bubbles, "visual check after feeding")
    item("Clean fermented aroma", clean_aroma, "no nail-polish/rotten notes")
    item("No visible mold or abnormal discoloration",
         None if mold_or_discoloration is None else not mold_or_discoloration, "discard if mold is present")
    fails = [i for i in items if i["status"] == "fail"]
    unknown = [i for i in items if i["status"] == "unknown"]
    risks = []
    if age_days is not None and age_days < 10:
        risks.append(f"Starter is only {age_days:g} days old; young starters are often unstable (bacterial bloom "
                     f"phases) and may not leaven reliably yet.")
    if any(i["check"].startswith("No visible mold") and i["status"] == "fail" for i in items):
        status = "unsafe"
    elif fails:
        status = "not_ready"
    elif unknown:
        status = "unknown"
    else:
        status = "ready"
    contingency = None
    if status != "ready":
        contingency = ("CONTINGENCY (explicitly labeled): use a mature starter from a reliable source, or keep feeding "
                       "every 12 h until it doubles in 6-8 h on consecutive feeds. A commercial-yeast fallback changes "
                       "the experiment and must be disclosed to judges.")
    return {"status": status, "items": items, "risks": risks, "contingency": contingency}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
@dataclass
class AgentState:
    agent_id: str
    chamber_id: str
    objective: str
    setpoint_c: float | None
    risk_weight: float
    history: list[dict[str, Any]] = field(default_factory=list)  # own prior findings
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    telemetry_summary: dict[str, Any] = field(default_factory=dict)
    overproof: dict[str, Any] = field(default_factory=dict)
    observation_quality: dict[str, Any] = field(default_factory=dict)
    deviations: dict[str, Any] | None = None
    ph_status: dict[str, Any] = field(default_factory=dict)


PH_UNCERTAINTY_WIDENING = 0.05  # added to the ± band when no valid pH observation exists


def _record_ref(o: ChamberObservation) -> str:
    if o.boot_id is not None and o.sequence_number is not None:
        return f"telemetry:{o.device_id or o.chamber_id}:{o.boot_id}:{o.sequence_number}"
    return f"telemetry:{o.chamber_id}:{o.effective_time_utc or o.device_uptime_ms or 'untimed'}"


class ScientistAgent:
    """Independent logical scientist owning one chamber."""

    def __init__(
        self,
        chamber_id: str,
        *,
        log: FindingsLog,
        cycle_id: str,
        timestamp_utc: str,
        setpoint_c: float | None = None,
        objective: str | None = None,
        risk_weight: float | None = None,
        evidence_source_override: str | None = None,
        experiment_id: str | None = None,
        current_experiment_id: str | None = None,
        reasoning_mode_label: str = "Offline Policy",
    ):
        self.chamber_id = chamber_id
        self.agent_id = f"scientist_{chamber_id}"
        self.peer_agent_id = f"scientist_{'B' if chamber_id == 'A' else 'A'}"
        self.log = log
        self.cycle_id = cycle_id
        self.ts = timestamp_utc
        self.source_override = evidence_source_override
        self.experiment_id = experiment_id  # the next experiment being planned
        self.current_experiment_id = current_experiment_id  # the run the telemetry belongs to
        self.reasoning_mode_label = reasoning_mode_label
        if risk_weight is None:
            # temperament from the original hypothesis: the cooler (mild) chamber is more overproof-averse
            risk_weight = 1.5 if (setpoint_c is not None and setpoint_c < 24.5) else 1.0
        self.state = AgentState(
            agent_id=self.agent_id, chamber_id=chamber_id,
            objective=objective or DEFAULT_OBJECTIVES.get(chamber_id, "independent chamber scientist"),
            setpoint_c=setpoint_c, risk_weight=risk_weight,
        )
        # own history only (separate state): previous findings published by this agent
        self.state.history = [f.to_dict() for f in log.read(agent_id=self.agent_id, limit=20)]
        self.evidence: dict[str, Any] | None = None
        self.observations: list[ChamberObservation] = []
        self.observation_refs: list[str] = []  # ids of this cycle's observation findings
        self.state.ph_status = {"valid": False, "reason": "no telemetry this cycle", "latest": None}

    # -- publishing ---------------------------------------------------------------------
    def _src(self, default: str) -> str:
        return self.source_override or default

    def publish(self, type_: str, content: str, payload: dict[str, Any] | None = None, *,
                evidence_source: str = "estimated", in_reply_to: str | None = None,
                recipients: list[str] | None = None, experiment_id: str | None = None,
                evidence_refs: list[str] | None = None, claim: str | None = None, uncertainty: str | None = None,
                proposed_action: dict[str, Any] | None = None) -> Finding:
        f = new_finding(self.agent_id, type_, content, payload=payload, evidence_source=self._src(evidence_source),
                        in_reply_to=in_reply_to, chamber_id=self.chamber_id, cycle_id=self.cycle_id,
                        timestamp_utc=self.ts,
                        recipients=recipients if recipients is not None else [self.peer_agent_id],
                        experiment_id=experiment_id or self.experiment_id, evidence_refs=evidence_refs,
                        claim=claim, uncertainty=uncertainty, proposed_action=proposed_action)
        self.log.append(f)
        self.state.history.append(f.to_dict())
        return f

    # -- perception (PLAN_MASTER §9.1-§9.3) ---------------------------------------------------
    def ph_statement(self) -> str:
        ph = self.state.ph_status
        if ph.get("valid"):
            return f"Latest valid pH in chamber {self.chamber_id}: {ph['latest']:.2f} (calibrated, method stated)."
        return (f"No valid pH measurement for chamber {self.chamber_id} ({ph.get('reason')}); I rely on model "
                f"predictions and widen the stated uncertainty by ±{PH_UNCERTAINTY_WIDENING:.2f}.")

    def uncertainty_widening(self) -> float:
        return 0.0 if self.state.ph_status.get("valid") else PH_UNCERTAINTY_WIDENING

    def observe(self, telemetry: list[dict[str, Any]] | None, prediction: Any = None, *, now_utc: str | None = None,
                hardware_limits: dict[str, Any] | None = None) -> list[Finding]:
        """Normalize own telemetry, gate by quality, compare with prediction, publish findings."""
        obs = normalize_history(telemetry, self.chamber_id, now_utc or self.ts, hardware_limits)
        self.observations = obs
        summ = _summary_from_obs(obs)
        hist = summ["quality"]
        self.state.telemetry_summary = summ
        self.state.observation_quality = hist
        self.state.ph_status = {"valid": bool(hist.get("ph_valid")), "reason": hist.get("ph_reason") or "",
                                "latest": hist.get("latest_valid_ph")}
        dev = compare_to_prediction(obs, prediction) if (obs and prediction is not None) else None
        self.state.deviations = dev
        op = assess_overproofing(None, prediction, setpoint_c=self.state.setpoint_c, observations=obs)
        if dev and dev.get("flags") and op["status"] in ("rising", "insufficient_data"):
            op["rationale"] += " Deviations from prediction: " + ", ".join(dev["flags"]) + "."
        op["deviations"] = dev
        latest = obs[-1] if obs else None
        op["observation_quality"] = {k: hist.get(k) for k in ("n", "sources", "latest_age_s", "latest_stale",
                                                                 "latest_usable", "ph_valid", "ph_reason")}
        op["additional_actions"] = additional_actions(hist, dev, latest, self.chamber_id)
        self.state.overproof = op
        out: list[Finding] = []
        if not obs:
            return out
        srcs = hist.get("sources", [])
        src = {"measured": "real", "simulated": "simulated", "replay": "replay"}.get(srcs[0], "estimated") \
            if len(srcs) == 1 else "estimated"
        refs = [_record_ref(o) for o in obs[-5:]]
        exp_id = self.current_experiment_id or (latest.experiment_id if latest else None)
        bits = [f"{summ['n']} telemetry records" + (f" over {summ['span_h']:.1f} h" if "span_h" in summ else
                                                     " (no common time basis)")]
        if "latest_starter_temp_c" in summ:
            bits.append(f"starter {summ['latest_starter_temp_c']:.1f} °C")
        if "latest_temp_c" in summ:
            bits.append(f"chamber air {summ['latest_temp_c']:.1f} °C")
        bits.append(f"usable now: {', '.join(hist.get('latest_usable') or []) or 'none'}")
        if hist.get("latest_stale"):
            bits.append("latest reading STALE")
        bits.append(f"proofing status: {op['status']}")
        claim = f"Chamber {self.chamber_id}: " + "; ".join(bits) + "."
        f = self.publish("observation_summary", claim + " " + self.ph_statement(), {
            "telemetry_summary": {k: v for k, v in summ.items() if k != "quality"},
            "observation_quality": hist, "latest_observation": latest.compact() if latest else None,
            "deviations_from_prediction": dev, "overproofing": op,
        }, evidence_source=src, recipients=[self.peer_agent_id], experiment_id=exp_id, evidence_refs=refs,
            claim=claim, uncertainty=self.ph_statement())
        out.append(f)
        self.observation_refs.append(f.id)
        for a in hist.get("anomalies", []):
            if a["severity"] == "info":
                continue
            act = next((x for x in op["additional_actions"] if x.get("reason") == a["message"]), None)
            af = self.publish("anomaly", f"Chamber {self.chamber_id} {a['severity']} [{a['category']}] {a['message']}",
                              {"anomaly": a}, evidence_source=src, recipients=[self.peer_agent_id], experiment_id=exp_id,
                              evidence_refs=refs, claim=a["message"], proposed_action=act)
            out.append(af)
            self.observation_refs.append(af.id)
        if op["action"] in ("step_down", "end_fermentation"):
            out.append(self.publish("proposed_intervention", f"Current run, chamber {self.chamber_id}: {op['rationale']}",
                                    {"scope": "current_run", **{k: v for k, v in op.items() if k != "deviations"}},
                                    evidence_source=src, experiment_id=exp_id, evidence_refs=refs,
                                    claim=op["rationale"],
                                    proposed_action={"type": op["action"], "chamber": self.chamber_id,
                                                     "suggested_setpoint_c": op["suggested_setpoint_c"],
                                                     "requires_human_confirmation": op["requires_human_confirmation"]}))
        return out

    def own_observation_text(self) -> str | None:
        summ = self.state.telemetry_summary
        if not summ.get("n"):
            src = (self.evidence or {}).get("prediction_source", "estimated")
            return (f"From my own chamber ({self.chamber_id}) I have no telemetry this cycle, so this critique relies on "
                    f"the judge feedback and model predictions ({src}).")
        hist = self.state.observation_quality
        op = self.state.overproof
        bad = [f"{a['code']}" for a in hist.get("anomalies", []) if a["severity"] != "info"]
        txt = (f"My chamber {self.chamber_id} telemetry ({summ['n']} rows, sources {', '.join(summ['sources']) or 'unknown'}; "
               f"usable now: {', '.join(hist.get('latest_usable') or []) or 'none'}"
               + (f"; anomalies: {', '.join(bad)}" if bad else "") + f") shows proofing status '{op.get('status')}': "
               f"{op.get('rationale', '')}")
        dev = self.state.deviations
        if dev and dev.get("flags"):
            txt += f" Deviations from my prediction: {', '.join(dev['flags'])}."
        return txt

    # -- reasoning ------------------------------------------------------------------------
    def build_evidence(self, interp: dict[str, Any], context: dict[str, Any], use_simulator: bool = True) -> dict[str, Any]:
        self.evidence = P.build_evidence(interp, context, use_simulator)
        return self.evidence

    def _decision_payload(self, schedule_id: str) -> dict[str, Any]:
        assert self.evidence is not None
        c = P._find(self.evidence, schedule_id)
        return {"chamber": self.chamber_id, "schedule_id": c["id"], "name": c["name"], "points": c["points"],
                "route": c["route"], "family": c["family"],
                "predicted": {"sourness": c["pred_sourness"], "openness": c["pred_openness"],
                              "rise_progress": c["model"]["rise_progress"],
                              "overproof_risk": c["model"]["overproof_risk"],
                              "time_to_peak_rise_h": c["model"]["time_to_peak_rise_h"],
                              "final_ph": c["model"].get("final_ph"),
                              "uncertainty": P.prediction_uncertainty(c, self.evidence, self.uncertainty_widening()),
                              "provenance": c["model"]["provenance"]}}

    def offline_lead(self, interp: dict[str, Any], lead_reason: str) -> P.LeadDecision:
        assert self.evidence is not None
        dec, _ = P.lead_decision(interp, self.evidence, self.chamber_id, risk_weight=self.state.risk_weight,
                                 lead_reason=lead_reason, obs_notes=[self.ph_statement()],
                                 widen=self.uncertainty_widening())
        return dec

    def propose(self, interp: dict[str, Any], decision: P.LeadDecision, *, decision_source: str,
                lead_reason: str) -> Finding:
        """Publish the proposer's findings; returns the headline hypothesis message."""
        assert self.evidence is not None
        ev_src = "simulated" if self.evidence["prediction_source"] == "simulated" else "estimated"
        own = self._decision_payload(decision.own_schedule_id)
        sugg = P._find(self.evidence, decision.peer_suggestion_schedule_id)
        peer = "B" if self.chamber_id == "A" else "A"
        hyp_struct = {"statement": decision.hypothesis_statement, "mechanism": decision.mechanism,
                      "independent_variable": "fermentation temperature schedule",
                      "dependent_variables": ["perceived sourness", "crumb openness", "rise (starter height)", "pH"],
                      "held_constant": ["recipe (200 g flour, 135 g water, 40 g starter, 4.4 g salt)", "starter feed",
                                        "mixing and folds", "bake"]}
        self.state.hypotheses.append(hyp_struct)
        self.state.decisions.append(own)
        sf = self.publish("sensory_feedback", decision.observation_summary,
                          {"interpretation_method": interp.get("method"),
                           "desired_summary": interp.get("desired_summary")},
                          evidence_source="judge_feedback", recipients=[self.peer_agent_id],
                          claim=interp.get("desired_summary"))
        refs = [sf.id, *self.observation_refs]
        content = (f"[{self.agent_id} → scientist_{peer}] Hypothesis: {decision.hypothesis_statement} "
                   f"Proposed for chamber {self.chamber_id}: '{own['name']}'. {decision.prediction} "
                   f"Suggestion for chamber {peer}: '{sugg['name']}'.")
        action = {"type": "set_schedule", "chamber": self.chamber_id, "schedule_id": own["schedule_id"],
                  "validated_library_schedule": True, "suggestion_for_peer": {"chamber": peer, "schedule_id": sugg["id"]}}
        msg = self.publish("hypothesis", content, {
            "role": "proposer", "decision_source": decision_source, "reasoning_mode_label": self.reasoning_mode_label,
            "lead_reason": lead_reason, "hypothesis": hyp_struct, "own_decision": own,
            "suggestion_for_peer": {"chamber": peer, "schedule_id": sugg["id"], "name": sugg["name"],
                                    "reason": decision.peer_suggestion_reason},
            "prediction": decision.prediction, "uncertainty": decision.uncertainty,
            "proposed_measurement": decision.proposed_measurement, "rationale": decision.rationale,
            "targets": self.evidence["targets"]["targets"], "ph_status": self.state.ph_status,
        }, evidence_source=ev_src, evidence_refs=refs, claim=decision.hypothesis_statement,
            uncertainty=decision.uncertainty, proposed_action=action)
        self.publish("prediction", decision.prediction, {"schedule_id": own["schedule_id"], **own["predicted"]},
                     evidence_source=ev_src, in_reply_to=msg.id, evidence_refs=[msg.id], claim=decision.prediction)
        self.publish("uncertainty", decision.uncertainty, {}, evidence_source=ev_src, in_reply_to=msg.id,
                     uncertainty=decision.uncertainty)
        self.publish("proposed_intervention", f"Next experiment, chamber {self.chamber_id}: '{own['name']}'.",
                     {"scope": "next_experiment", **own}, evidence_source=ev_src, in_reply_to=msg.id, proposed_action=action)
        self.publish("proposed_measurement", decision.proposed_measurement, {}, evidence_source=ev_src, in_reply_to=msg.id,
                     proposed_action={"type": "measure", "what": decision.proposed_measurement})
        return msg

    def read_peer_proposal(self) -> Finding | None:
        """Read the peer's headline hypothesis for this cycle from the shared log."""
        msgs = self.log.read(cycle_id=self.cycle_id, type="hypothesis", exclude_agent=self.agent_id)
        return msgs[-1] if msgs else None

    def offline_critique(self, interp: dict[str, Any], peer_msg: Finding, communicate: bool = True,
                         own_has_judged_outcome: bool = True) -> tuple[P.PeerDecision, dict[str, Any]]:
        assert self.evidence is not None
        return P.peer_decision(interp, self.evidence, self.chamber_id, peer_msg.payload,
                               risk_weight=self.state.risk_weight, own_obs=self.own_observation_text(),
                               communicate=communicate, own_has_judged_outcome=own_has_judged_outcome,
                               obs_notes=[self.ph_statement()], widen=self.uncertainty_widening())

    def respond(self, peer_msg: Finding, decision: P.PeerDecision, *, decision_source: str,
                independent_choice_id: str | None) -> Finding:
        """Post a critique of the peer's proposal (via ``challenge``) plus own decision findings."""
        assert self.evidence is not None
        ev_src = "simulated" if self.evidence["prediction_source"] == "simulated" else "estimated"
        own = self._decision_payload(decision.own_schedule_id)
        self.state.decisions.append(own)
        self.state.hypotheses.append({"statement": decision.alternative_hypothesis, "role": "alternative"})
        indep = self._decision_payload(independent_choice_id) if independent_choice_id else None
        ph_txt = self.ph_statement()
        content = (f"[{self.agent_id} → {peer_msg.agent_id}] Critique: {decision.critique} "
                   f"Decision for chamber {self.chamber_id}: '{own['name']}'. {decision.decision_change_note}"
                   + ("" if ph_txt in decision.critique else f" {ph_txt}"))
        action = {"type": "set_schedule", "chamber": self.chamber_id, "schedule_id": own["schedule_id"],
                  "validated_library_schedule": True}
        crit = challenge(peer_msg, critic_agent_id=self.agent_id, content=content, payload={
            "role": "critic", "decision_source": decision_source, "reasoning_mode_label": self.reasoning_mode_label,
            "message_kind": "peer_critique",
            "agrees_with_hypothesis": decision.agrees_with_hypothesis, "concerns": decision.concerns,
            "alternative_hypothesis": decision.alternative_hypothesis, "own_decision": own,
            "accepted_peer_suggestion": decision.accepted_peer_suggestion,
            "independent_choice": indep,
            "decision_changed_by_peer": bool(indep and indep["schedule_id"] != own["schedule_id"]),
            "decision_change_note": decision.decision_change_note,
            "prediction": decision.prediction, "uncertainty": decision.uncertainty,
            "proposed_measurement": decision.proposed_measurement, "rationale": decision.rationale,
            "ph_status": self.state.ph_status,
        }, evidence_source=self._src(ev_src), log=self.log, chamber_id=self.chamber_id, cycle_id=self.cycle_id,
            timestamp_utc=self.ts, experiment_id=self.experiment_id, claim=decision.alternative_hypothesis,
            uncertainty=decision.uncertainty, proposed_action=action, evidence_refs=list(self.observation_refs))
        self.state.history.append(crit.to_dict())
        self.publish("prediction", decision.prediction, {"schedule_id": own["schedule_id"], **own["predicted"]},
                     evidence_source=ev_src, in_reply_to=crit.id, evidence_refs=[crit.id], claim=decision.prediction)
        self.publish("uncertainty", decision.uncertainty, {}, evidence_source=ev_src, in_reply_to=crit.id,
                     uncertainty=decision.uncertainty)
        self.publish("proposed_intervention", f"Next experiment, chamber {self.chamber_id}: '{own['name']}'.",
                     {"scope": "next_experiment", **own}, evidence_source=ev_src, in_reply_to=crit.id, proposed_action=action)
        self.publish("proposed_measurement", decision.proposed_measurement, {}, evidence_source=ev_src, in_reply_to=crit.id,
                     proposed_action={"type": "measure", "what": decision.proposed_measurement})
        return crit

    def reply_to_critique(self, critique: Finding, own_msg: Finding, decision: P.PeerDecision) -> tuple[Finding, dict[str, Any]]:
        assert self.evidence is not None
        rep = P.lead_reply(self.evidence, own_msg.payload["own_decision"]["schedule_id"], decision,
                           risk_weight=self.state.risk_weight, peer_choice_id=decision.own_schedule_id)
        final = self._decision_payload(rep["final"]["id"])
        if rep["revised"]:
            self.state.decisions.append(final)
        msg = self.publish("rationale", f"[{self.agent_id} → {critique.agent_id}] {rep['content']}",
                           {"role": "proposer_reply", "decision_source": "offline_policy", "revised": rep["revised"],
                            "final_decision": final},
                           evidence_source="simulated" if self.evidence["prediction_source"] == "simulated" else "estimated",
                           in_reply_to=critique.id, evidence_refs=[critique.id], claim=rep["content"],
                           proposed_action={"type": "set_schedule", "chamber": self.chamber_id,
                                            "schedule_id": final["schedule_id"], "validated_library_schedule": True})
        return msg, final

    def final_schedule_id(self) -> str | None:
        return self.state.decisions[-1]["schedule_id"] if self.state.decisions else None

    def llm_context(self, interp: dict[str, Any], peer_msg: Finding | None = None) -> dict[str, Any]:
        """Only this agent's own context (+ the peer's message if critiquing) for the LLM prompt.

        Telemetry reaches the LLM only as quality-annotated observations (never raw JSON).
        """
        assert self.evidence is not None
        ev = self.evidence
        cands = [{"id": c["id"], "name": c["name"], "route": c["route"], "points": c["points"],
                  "summary": c["summary"], "pred_sourness": c["pred_sourness"], "pred_openness": c["pred_openness"],
                  "rise_progress": c["model"]["rise_progress"], "overproof_risk": c["model"]["overproof_risk"],
                  "offline_cost": P.candidate_cost(c, ev, self.state.risk_weight)["total"]} for c in ev["candidates"]]
        latest = self.observations[-1] if self.observations else None
        ctx = {
            "you_are": self.agent_id, "your_chamber": self.chamber_id, "your_original_objective": self.state.objective,
            "your_setpoint_c": self.state.setpoint_c,
            "your_recent_findings": [{"type": h["type"], "content": h["content"][:400]} for h in self.state.history[-6:]],
            "your_observations": {
                "latest_normalized_observation": latest.compact() if latest else None,
                "history_quality": self.state.observation_quality or {"n": 0, "note": "no telemetry"},
                "deviations_from_prediction": self.state.deviations,
                "ph_status": self.state.ph_status,
                "ph_statement": self.ph_statement(),
                "uncertainty_widening": self.uncertainty_widening(),
            },
            "your_proofing_assessment": {k: v for k, v in self.state.overproof.items() if k != "deviations"},
            "judge_feedback_raw": interp.get("raw_text"),
            "rule_based_interpretation": {
                "loaves": {l: {d: {"score": v["score"], "label": v["label"], "confidence": v["confidence"]}
                               for d, v in (interp["loaves"][l]["attributes"]).items()} for l in P.LOAVES},
                "desired": {d: {k: v[k] for k in ("direction", "strength", "confidence", "preferred_pole")}
                            for d, v in interp.get("desired", {}).items()},
                "desired_summary": interp.get("desired_summary"), "preferred_loaf": interp.get("preferred_loaf"),
            },
            "numeric_targets_judge_scale": ev["targets"]["targets"],
            "recorded_experiments": {l: {k: ev["recorded"][l][k] for k in ("points", "source", "experiment_id", "violations",
                                                                           "pred_sourness", "pred_openness", "judged")}
                                     for l in P.LOAVES},
            "prediction_source": ev["prediction_source"],
            "calibration": ev["anchors"],
            "candidate_library": cands,
        }
        if peer_msg is not None:
            ctx["peer_message"] = {"id": peer_msg.id, "from": peer_msg.agent_id, "content": peer_msg.content,
                                   "own_decision": peer_msg.payload.get("own_decision"),
                                   "suggestion_for_you": peer_msg.payload.get("suggestion_for_peer"),
                                   "hypothesis": peer_msg.payload.get("hypothesis")}
        return ctx
