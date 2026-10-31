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
from datetime import datetime
from typing import Any

from . import offline_policy as P
from . import schedules as S
from .communication import Finding, FindingsLog, challenge, new_finding

DEFAULT_OBJECTIVES = {
    "A": "Mild hypothesis: relatively mild acidity with sufficient expansion (initial 22 °C).",
    "B": "More-acidic hypothesis: increased acidification and detectable flavor differences (initial 27 °C).",
}


# ---------------------------------------------------------------------------
# Telemetry helpers and the overproofing rule
# ---------------------------------------------------------------------------
def _parse_ts(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_telemetry(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    out: dict[str, Any] = {"n": len(rows), "sources": sorted({str(r.get("source")) for r in rows if r.get("source")})}
    if not rows:
        return out
    stamped = [(t, r) for r in rows if (t := _parse_ts(r.get("timestamp_utc"))) is not None]
    if stamped:
        stamped.sort(key=lambda x: x[0])
        t0 = stamped[0][0]
        series = [((t - t0).total_seconds() / 3600.0, r) for t, r in stamped]
    else:
        series = [(float(i) * 0.25, r) for i, r in enumerate(rows)]  # assume 15-min spacing
        out["timestamps_missing"] = True
    out["span_h"] = round(series[-1][0] - series[0][0], 2)
    temps = [r.get("chamber_temperature_c") for _, r in series if isinstance(r.get("chamber_temperature_c"), (int, float))]
    if temps:
        out["latest_temp_c"] = round(float(temps[-1]), 2)
        out["mean_temp_c"] = round(sum(temps) / len(temps), 2)
    sps = [r.get("temperature_setpoint_c") for _, r in series if isinstance(r.get("temperature_setpoint_c"), (int, float))]
    if sps:
        out["latest_setpoint_c"] = float(sps[-1])
    heights = [(t, float(r["starter_height_mm"])) for t, r in series if isinstance(r.get("starter_height_mm"), (int, float))]
    out["heights"] = heights
    phs = [(t, float(r["ph"])) for t, r in series if isinstance(r.get("ph"), (int, float))]
    if phs:
        out["latest_ph"] = round(phs[-1][1], 2)
        out["ph_points"] = len(phs)
    return out


def _predicted_peak_h(pred: Any) -> float | None:
    """Accepts {"time_h": [...], "expansion_ratio": [...]} or a list of row dicts."""
    try:
        if isinstance(pred, dict) and "time_h" in pred and "expansion_ratio" in pred:
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
) -> dict[str, Any]:
    """PLAN §5 rule: near/after peak expansion -> end fermentation or step down; never hold blindly.

    Returns {"status": rising|near_peak|declining|insufficient_data,
             "action": continue|step_down|end_fermentation|monitor,
             "suggested_setpoint_c", "rationale", "evidence"}.
    Actions are *proposals*: the bake decision requires human confirmation and every
    setpoint is clamped by the API/firmware.
    """
    summ = summarize_telemetry(telemetry)
    heights = summ.get("heights") or []
    ev: dict[str, Any] = {"telemetry_points": summ["n"], "sources": summ["sources"]}
    status = "insufficient_data"
    reason = "fewer than 3 starter-height readings"
    if len(heights) >= 3:
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
    sp = setpoint_c if setpoint_c is not None else summ.get("latest_setpoint_c")
    if status == "declining":
        action, new_sp = "end_fermentation", None
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
    else:
        action, new_sp, text = "monitor", None, "Not enough rise data to judge proofing; keep logging starter height."
    return {"status": status, "action": action, "suggested_setpoint_c": new_sp, "rationale": f"{text} ({reason})",
            "evidence": ev, "requires_human_confirmation": action == "end_fermentation"}


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
    ):
        self.chamber_id = chamber_id
        self.agent_id = f"scientist_{chamber_id}"
        self.log = log
        self.cycle_id = cycle_id
        self.ts = timestamp_utc
        self.source_override = evidence_source_override
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

    # -- publishing ---------------------------------------------------------------------
    def _src(self, default: str) -> str:
        return self.source_override or default

    def publish(self, type_: str, content: str, payload: dict[str, Any] | None = None, *,
                evidence_source: str = "estimated", in_reply_to: str | None = None) -> Finding:
        f = new_finding(self.agent_id, type_, content, payload=payload, evidence_source=self._src(evidence_source),
                        in_reply_to=in_reply_to, chamber_id=self.chamber_id, cycle_id=self.cycle_id,
                        timestamp_utc=self.ts)
        self.log.append(f)
        self.state.history.append(f.to_dict())
        return f

    # -- perception -----------------------------------------------------------------------
    def observe(self, telemetry: list[dict[str, Any]] | None, prediction: Any = None) -> list[Finding]:
        summ = summarize_telemetry(telemetry)
        self.state.telemetry_summary = {k: v for k, v in summ.items() if k != "heights"}
        op = assess_overproofing(telemetry, prediction, setpoint_c=self.state.setpoint_c)
        self.state.overproof = op
        out: list[Finding] = []
        if summ["n"]:
            src = summ["sources"][0] if len(summ["sources"]) == 1 and summ["sources"][0] in (
                "real", "simulated", "replay", "estimated", "imported_human") else "estimated"
            bits = [f"{summ['n']} telemetry rows over {summ.get('span_h', 0):.1f} h"]
            if "latest_temp_c" in summ:
                bits.append(f"chamber {summ['latest_temp_c']:.1f} °C")
            if "latest_ph" in summ:
                bits.append(f"latest pH {summ['latest_ph']:.2f}")
            bits.append(f"proofing status: {op['status']}")
            out.append(self.publish("observation", f"Chamber {self.chamber_id}: " + ", ".join(bits) + ".",
                                    {"telemetry_summary": self.state.telemetry_summary, "overproofing": op},
                                    evidence_source=src))
            if op["action"] in ("step_down", "end_fermentation"):
                out.append(self.publish("proposed_intervention", f"Current run, chamber {self.chamber_id}: {op['rationale']}",
                                        {"scope": "current_run", **op}, evidence_source=src))
        return out

    def own_observation_text(self) -> str | None:
        summ = self.state.telemetry_summary
        if not summ.get("n"):
            src = (self.evidence or {}).get("prediction_source", "estimated")
            return (f"From my own chamber ({self.chamber_id}) I have no telemetry this cycle, so this critique relies on "
                    f"the judge feedback and model predictions ({src}).")
        op = self.state.overproof
        return (f"My chamber {self.chamber_id} telemetry ({summ['n']} rows, sources {', '.join(summ['sources']) or 'unknown'}) "
                f"shows proofing status '{op.get('status')}': {op.get('rationale', '')}")

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
                              "uncertainty": P.prediction_uncertainty(c, self.evidence),
                              "provenance": c["model"]["provenance"]}}

    def offline_lead(self, interp: dict[str, Any], lead_reason: str) -> P.LeadDecision:
        assert self.evidence is not None
        dec, _ = P.lead_decision(interp, self.evidence, self.chamber_id, risk_weight=self.state.risk_weight,
                                 lead_reason=lead_reason)
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
        self.publish("observation", decision.observation_summary, {"interpretation_method": interp.get("method")},
                     evidence_source="judge_feedback")
        content = (f"[{self.agent_id} → scientist_{peer}] Hypothesis: {decision.hypothesis_statement} "
                   f"Proposed for chamber {self.chamber_id}: '{own['name']}'. {decision.prediction} "
                   f"Suggestion for chamber {peer}: '{sugg['name']}'.")
        msg = self.publish("hypothesis", content, {
            "role": "proposer", "decision_source": decision_source, "lead_reason": lead_reason,
            "hypothesis": hyp_struct, "own_decision": own,
            "suggestion_for_peer": {"chamber": peer, "schedule_id": sugg["id"], "name": sugg["name"],
                                    "reason": decision.peer_suggestion_reason},
            "prediction": decision.prediction, "uncertainty": decision.uncertainty,
            "proposed_measurement": decision.proposed_measurement, "rationale": decision.rationale,
            "targets": self.evidence["targets"]["targets"],
        }, evidence_source=ev_src)
        self.publish("prediction", decision.prediction, {"schedule_id": own["schedule_id"], **own["predicted"]},
                     evidence_source=ev_src, in_reply_to=msg.id)
        self.publish("uncertainty", decision.uncertainty, {}, evidence_source=ev_src, in_reply_to=msg.id)
        self.publish("proposed_intervention", f"Next experiment, chamber {self.chamber_id}: '{own['name']}'.",
                     {"scope": "next_experiment", **own}, evidence_source=ev_src, in_reply_to=msg.id)
        self.publish("proposed_measurement", decision.proposed_measurement, {}, evidence_source=ev_src, in_reply_to=msg.id)
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
                               communicate=communicate, own_has_judged_outcome=own_has_judged_outcome)

    def respond(self, peer_msg: Finding, decision: P.PeerDecision, *, decision_source: str,
                independent_choice_id: str | None) -> Finding:
        """Post a critique of the peer's proposal (via ``challenge``) plus own decision findings."""
        assert self.evidence is not None
        ev_src = "simulated" if self.evidence["prediction_source"] == "simulated" else "estimated"
        own = self._decision_payload(decision.own_schedule_id)
        self.state.decisions.append(own)
        self.state.hypotheses.append({"statement": decision.alternative_hypothesis, "role": "alternative"})
        indep = self._decision_payload(independent_choice_id) if independent_choice_id else None
        content = (f"[{self.agent_id} → {peer_msg.agent_id}] Critique: {decision.critique} "
                   f"Decision for chamber {self.chamber_id}: '{own['name']}'. {decision.decision_change_note}")
        crit = challenge(peer_msg, critic_agent_id=self.agent_id, content=content, payload={
            "role": "critic", "decision_source": decision_source,
            "agrees_with_hypothesis": decision.agrees_with_hypothesis, "concerns": decision.concerns,
            "alternative_hypothesis": decision.alternative_hypothesis, "own_decision": own,
            "accepted_peer_suggestion": decision.accepted_peer_suggestion,
            "independent_choice": indep,
            "decision_changed_by_peer": bool(indep and indep["schedule_id"] != own["schedule_id"]),
            "decision_change_note": decision.decision_change_note,
            "prediction": decision.prediction, "uncertainty": decision.uncertainty,
            "proposed_measurement": decision.proposed_measurement, "rationale": decision.rationale,
        }, evidence_source=self._src(ev_src), log=self.log, chamber_id=self.chamber_id, cycle_id=self.cycle_id,
            timestamp_utc=self.ts)
        self.state.history.append(crit.to_dict())
        self.publish("prediction", decision.prediction, {"schedule_id": own["schedule_id"], **own["predicted"]},
                     evidence_source=ev_src, in_reply_to=crit.id)
        self.publish("uncertainty", decision.uncertainty, {}, evidence_source=ev_src, in_reply_to=crit.id)
        self.publish("proposed_intervention", f"Next experiment, chamber {self.chamber_id}: '{own['name']}'.",
                     {"scope": "next_experiment", **own}, evidence_source=ev_src, in_reply_to=crit.id)
        self.publish("proposed_measurement", decision.proposed_measurement, {}, evidence_source=ev_src, in_reply_to=crit.id)
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
                           in_reply_to=critique.id)
        return msg, final

    def final_schedule_id(self) -> str | None:
        return self.state.decisions[-1]["schedule_id"] if self.state.decisions else None

    def llm_context(self, interp: dict[str, Any], peer_msg: Finding | None = None) -> dict[str, Any]:
        """Only this agent's own context (+ the peer's message if critiquing) for the LLM prompt."""
        assert self.evidence is not None
        ev = self.evidence
        cands = [{"id": c["id"], "name": c["name"], "route": c["route"], "points": c["points"],
                  "summary": c["summary"], "pred_sourness": c["pred_sourness"], "pred_openness": c["pred_openness"],
                  "rise_progress": c["model"]["rise_progress"], "overproof_risk": c["model"]["overproof_risk"],
                  "offline_cost": P.candidate_cost(c, ev, self.state.risk_weight)["total"]} for c in ev["candidates"]]
        ctx = {
            "you_are": self.agent_id, "your_chamber": self.chamber_id, "your_original_objective": self.state.objective,
            "your_setpoint_c": self.state.setpoint_c,
            "your_recent_findings": [{"type": h["type"], "content": h["content"][:400]} for h in self.state.history[-6:]],
            "your_telemetry_summary": self.state.telemetry_summary, "your_proofing_assessment": self.state.overproof,
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
