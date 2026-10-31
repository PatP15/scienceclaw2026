"""Taste-feedback -> next-experiment cycle (PLAN §11). Public entry point for the API.

    run_taste_feedback_cycle(feedback_text, context, mode="offline") -> dict

``context`` keys (all optional, missing-tolerant):
    telemetry       {"A": [telemetry dicts], "B": [...]}   (PLAN §7 schema)
    setpoints       {"A": 22.0, "B": 27.0}
    experiments     [{"experiment_id", "chamber_id", "schedule"|"points"|"setpoint_c", ...}]
                    or [{"experiment_id", "chambers": {"A": {...}, "B": {...}}}]
    prior_feedback  ["text", {"text": ...}, ...]
    predictions     {"A": {"time_h": [...], "expansion_ratio": [...]}, "B": ...}
  extra knobs (for the API / tests / benchmarks):
    now_utc             fixed ISO timestamp -> fully reproducible output (ids included)
    findings_log_path   path of the JSONL log (default data/experiments/findings_log.jsonl)
    persist_findings    False -> in-memory log only (default True)
    communicate         False -> agent B decides without reading agent A (benchmark baseline)
    use_simulator       False -> force the heuristic predictor
    replay              True  -> every finding labeled evidence_source "replay"
    llm_mode            override FERMENTAI_LLM_MODE for live mode (offline|claude_cli|anthropic_api)
    llm_adapter         injected LLMAdapter (tests)
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import offline_policy as P
from . import schedules as S
from .communication import Finding, FindingsLog, utc_now_iso
from .llm_adapter import LLMAdapter
from .scientist import ScientistAgent
from .sensory import parse_feedback

MODE_OFFLINE = "offline:deterministic_fallback"
REASONING_ONLINE = "Online Agent"
REASONING_OFFLINE = "Offline Policy"
SAFETY_NOTE = (
    "Recommended schedules are proposals selected only from a validated candidate library "
    f"({S.MIN_TEMP_C:g}-{S.MAX_TEMP_C:g} °C, no hold at or above {S.HOT_THRESHOLD_C:g} °C longer than "
    f"{S.MAX_HOT_HOLD_H:g} h, ramps ≤ {S.MAX_RAMP_C_PER_H:g} °C/h). The local API clamps every setpoint and "
    "the ESP32 firmware independently enforces temperature limits, sensor-failure handling, fan protection and "
    "safe heating/cooling switching. Neither the LLM nor judge text can set power or bypass these checks, and "
    "ending fermentation/baking requires human confirmation."
)


def _prior_texts(prior: Any) -> list[str]:
    out = []
    for p in prior or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            t = p.get("text") or p.get("feedback_text") or p.get("feedback")
            if isinstance(t, str):
                out.append(t)
    return out


def _cycle_id(feedback_text: str, context: dict[str, Any], now: str) -> str:
    keep = {k: context.get(k) for k in ("setpoints", "experiments", "prior_feedback")}
    blob = json.dumps({"t": feedback_text, "c": keep, "now": now}, sort_keys=True, default=str)
    return "cyc_" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _next_experiment_id(context: dict[str, Any]) -> str:
    nums = []
    exps = context.get("experiments") or []
    for e in exps if isinstance(exps, list) else []:
        if isinstance(e, dict):
            m = re.search(r"(\d+)\s*$", str(e.get("experiment_id") or e.get("id") or ""))
            if m:
                nums.append(int(m.group(1)))
    n = (max(nums) if nums else max(1, len(exps) if isinstance(exps, list) else 1)) + 1
    return f"trial_{n:03d}"


def _merge_prior(interp: dict[str, Any], prior: list[str], setpoints: dict[str, float]) -> None:
    if not prior:
        return
    interp["notes"].append(f"{len(prior)} prior feedback entr{'y' if len(prior) == 1 else 'ies'} considered.")
    if interp["desired"] or interp.get("preferred_loaf"):
        return
    for text in reversed(prior):
        old = parse_feedback(text, setpoints)
        if old["desired"]:
            for dim, v in old["desired"].items():
                interp["desired"][dim] = {**v, "confidence": round(v["confidence"] * 0.6, 3), "implied": True,
                                          "from_prior_feedback": True, "anchor_loaf": None}
            interp["desired_summary"] = old["desired_summary"].rstrip(".") + " (carried over from earlier feedback)."
            interp["primary_dimension"] = old["primary_dimension"]
            interp["notes"].append("No direction in the new feedback; carried over the most recent earlier preference "
                                   "at reduced confidence.")
            return


def _per_chamber(context: dict[str, Any], key: str) -> dict[str, Any]:
    v = context.get(key)
    return v if isinstance(v, dict) else {}


def _success_criteria(evidence: dict[str, Any], final: dict[str, dict[str, Any]]) -> list[str]:
    t = evidence["targets"]["targets"]
    judged = evidence["targets"]["judged"]
    crit = []
    if "sourness" in t:
        s = t["sourness"]
        crit.append(f"Blind tasting: at least one loaf is judged {fmt_dir(s)} — target sourness {P.fmt_target(s)} on "
                    f"the 0-1 scale ({s['reason']}); the same feedback parser scores the new comments.")
    if "openness" in t:
        o = t["openness"]
        crit.append(f"Crumb: judged crumb openness {P.fmt_target(o)} ({o['reason']}); crumb photo shows no collapse "
                    f"or gumminess.")
    if "acetic" in t:
        crit.append(f"Aroma: {t['acetic']['reason']} — judged aroma notes move in the requested direction.")
    if evidence["targets"]["explore"]:
        crit.append("Information: the two new loaves differ from both recorded loaves on at least one judged "
                    "attribute (otherwise the tested range is too narrow).")
    crit.append("Proofing: each chamber ends at its rise plateau — starter height must not fall >3% below its peak "
                "before ending (no overproofing).")
    routes = {final[c]["route"] for c in final}
    if len(routes) > 1:
        crit.append("Discrimination: the two loaves are distinguishable in blind tasting on the targeted attribute, so "
                    "the two mechanisms can be told apart.")
    if evidence["prediction_source"] == "simulated":
        crit.append("Model check: measured end-of-fermentation pH within ±0.15 of the simulated value.")
    else:
        crit.append("Model check: record pH spot checks to calibrate the kinetic model (the heuristic predictor has "
                    "no pH output, so no pass/fail threshold yet).")
    if not judged["A"] and not judged["B"]:
        crit.append("Collect loaf-specific feedback (Loaf A / Loaf B) so the next round can be calibrated.")
    return crit


def fmt_dir(t: dict[str, Any]) -> str:
    if t["mode"] == "at_most":
        return "no more sour than before"
    if t["mode"] == "at_least":
        return "at least as sour as before"
    return "closer to the requested sourness"


MEASUREMENTS = [
    {"quantity": "chamber_temperature_c", "method": "chamber sensor, continuous", "label": "real (when hardware connected)"},
    {"quantity": "starter_height_mm / rise", "method": "side camera every 10 min; end at plateau",
     "label": "real (when hardware connected)"},
    {"quantity": "pH", "method": "single shared probe; spot checks on a separate non-edible sample at 0 h, at each "
                                 "temperature change and at the end", "label": "real (experimental probe)"},
    {"quantity": "gas_resistance_ohms", "method": "BME680 VOC signal (NOT CO2; no calibrated mapping)",
     "label": "real (environmental signal)"},
    {"quantity": "sensory: sourness, crumb texture, aroma", "method": "blind tasting, free-text feedback parsed by the "
                                                                      "same rules", "label": "judge_feedback"},
    {"quantity": "crumb photo", "method": "cross-section photo after cooling", "label": "real"},
]


def run_taste_feedback_cycle(feedback_text: str, context: dict | None = None, mode: str = "offline") -> dict:
    context = dict(context or {})
    feedback_text = feedback_text if isinstance(feedback_text, str) else str(feedback_text or "")
    requested = (mode or "offline").strip().lower()
    now = context.get("now_utc") or utc_now_iso()
    setpoints = P.setpoints_from_context(context)
    interp = parse_feedback(feedback_text, setpoints)
    _merge_prior(interp, _prior_texts(context.get("prior_feedback")), setpoints)
    cycle_id = _cycle_id(feedback_text, context, now)
    persist = context.get("persist_findings", True)
    log_path = context.get("findings_log_path") or "default"
    history_log = FindingsLog(log_path) if persist else FindingsLog(None)
    live_reason = None
    result = None
    adapter = None
    if requested == "live":
        adapter = context.get("llm_adapter") or LLMAdapter(context.get("llm_mode"))
        ok, why = adapter.check_available()
        if not ok:
            live_reason = why
        else:
            try:
                result = _run_cycle(interp, context, setpoints, cycle_id, now, history_log, adapter)
            except Exception as exc:  # noqa: BLE001 - any live failure -> offline fallback
                live_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                result = None
    elif requested != "offline":
        live_reason = f"unknown mode {requested!r}"
    if result is None:
        result = _run_cycle(interp, context, setpoints, cycle_id, now, history_log, None)
        result["mode_label"] = MODE_OFFLINE + (f" (live unavailable: {live_reason})" if live_reason else "")
        result["reasoning_mode_label"] = REASONING_OFFLINE
    else:
        result["mode_label"] = f"live:{adapter.mode}"
        result["reasoning_mode_label"] = REASONING_ONLINE
    result["provenance"]["reasoning_mode_label"] = result["reasoning_mode_label"]
    staged: list[Finding] = result.pop("_staged")
    if persist:
        history_log.extend(staged)
    result["findings_log"] = {"path": str(history_log.path) if history_log.path else None, "persisted": bool(persist),
                              "appended": len(staged)}
    return result


def _ensure_ph_statement(decision, agent: ScientistAgent):
    """PLAN_MASTER §9.2: if there is no valid pH, the agent's text must say so (LLM output included)."""
    if agent.state.ph_status.get("valid") or "ph" in decision.uncertainty.lower():
        return decision
    return decision.model_copy(update={"uncertainty": f"{decision.uncertainty} {agent.ph_statement()}"})


def _run_cycle(interp: dict[str, Any], context: dict[str, Any], setpoints: dict[str, float], cycle_id: str, now: str,
               history_log: FindingsLog, adapter: LLMAdapter | None) -> dict[str, Any]:
    staging = FindingsLog(None)  # nothing reaches the persistent log unless the whole cycle succeeds
    for f in history_log.read(limit=200):
        staging.append(f)
    already = {f.id for f in staging.read()}
    use_sim = bool(context.get("use_simulator", True))
    communicate = bool(context.get("communicate", True))
    src_override = "replay" if context.get("replay") else None
    telemetry = _per_chamber(context, "telemetry")
    predictions = _per_chamber(context, "predictions")
    next_id = _next_experiment_id(context)
    current_exp = context.get("experiment_id") if isinstance(context.get("experiment_id"), str) else None
    reasoning = REASONING_ONLINE if adapter is not None else REASONING_OFFLINE
    agents = {c: ScientistAgent(c, log=staging, cycle_id=cycle_id, timestamp_utc=now, setpoint_c=setpoints.get(c),
                                evidence_source_override=src_override, experiment_id=next_id,
                                current_experiment_id=current_exp, reasoning_mode_label=reasoning)
              for c in P.LOAVES}
    limits = context.get("hardware_limits") if isinstance(context.get("hardware_limits"), dict) else None
    for c, ag in agents.items():
        # quality-gated observation adapter (PLAN_MASTER §9.1-9.2); raw telemetry never reaches the policy/LLM
        ag.observe(telemetry.get(c) if isinstance(telemetry.get(c), list) else None, predictions.get(c),
                   now_utc=now, hardware_limits=limits)
        ag.build_evidence(interp, context, use_sim)  # each agent builds its own evidence view
    lead_id, lead_reason = P.choose_lead(interp, agents["A"].evidence, setpoints)
    forced = str(context.get("lead_agent") or "").replace("scientist_", "").upper()
    if forced in P.LOAVES:
        lead_id, lead_reason = forced, f"the operator asked scientist_{forced} to open this round (lead_agent)"
    peer_id = "B" if lead_id == "A" else "A"
    lead, peer = agents[lead_id], agents[peer_id]
    ids = [c.id for c in S.LIBRARY]
    decision_source = "llm" if adapter is not None else "offline_policy"

    # 1) proposer publishes its hypothesis (from its own context only)
    if adapter is not None:
        lead_dec = _ensure_ph_statement(adapter.lead(lead.agent_id, lead_id, lead.llm_context(interp), ids), lead)
    else:
        lead_dec = lead.offline_lead(interp, lead_reason)
    lead_msg = lead.propose(interp, lead_dec, decision_source=decision_source, lead_reason=lead_reason)

    # 2) the second agent reads the proposal FROM THE LOG and critiques it from its own context
    seen = peer.read_peer_proposal() if communicate else None
    if communicate and seen is None:
        raise RuntimeError("peer could not read the proposer's message from the findings log")
    own_judged = bool(peer.evidence["targets"]["judged"][peer_id])
    if adapter is not None and communicate:
        peer_dec = _ensure_ph_statement(
            adapter.critique(peer.agent_id, peer_id, lead.agent_id, peer.llm_context(interp, seen), ids), peer)
        _, peer_extra = peer.offline_critique(interp, seen, communicate=False)  # for the "independent choice" record
        indep_id = peer_extra["independent_choice"]["id"]
    else:
        peer_dec, peer_extra = peer.offline_critique(interp, seen or lead_msg, communicate=communicate,
                                                     own_has_judged_outcome=own_judged)
        indep_id = peer_extra["independent_choice"]["id"]
    crit_msg = peer.respond(seen or lead_msg, peer_dec, decision_source=decision_source, independent_choice_id=indep_id)

    # 3) proposer answers the critique (rule-based; may revise its own chamber only)
    reply_msg, lead_final = lead.reply_to_critique(crit_msg, lead_msg, peer_dec)

    final = {lead_id: lead_final, peer_id: crit_msg.payload["own_decision"]}
    for c in P.LOAVES:  # defense in depth: everything recommended must be a valid library schedule
        errs = S.validate_schedule(final[c]["points"])
        if errs or final[c]["schedule_id"] not in S.LIBRARY_BY_ID:
            raise RuntimeError(f"invalid schedule for chamber {c}: {errs}")

    ev = lead.evidence
    hyp = lead_msg.payload["hypothesis"]
    t = ev["targets"]
    conf = "low"
    if ev["anchors"]["sourness"]["n_points"] >= 2 and interp["overall_confidence"] >= 0.7:
        conf = "moderate"
    if final["A"]["route"] != final["B"]["route"]:
        title_routes = " vs ".join(f"{c}: {P.ROUTE_SHORT[final[c]['route']]}" for c in P.LOAVES)
    else:
        title_routes = " vs ".join(f"{c}: {final[c]['name']}" for c in P.LOAVES)
    objective = (f"{interp['desired_summary'].rstrip('.')}. Test whether '{final[lead_id]['name']}' "
                 f"({P.ROUTE_SHORT[final[lead_id]['route']]}) or '{final[peer_id]['name']}' "
                 f"({P.ROUTE_SHORT[final[peer_id]['route']]}) better achieves it.") if not t["explore"] else (
        f"No explicit preference: map the response with two informative schedules "
        f"('{final['A']['name']}', '{final['B']['name']}').")
    next_experiment = {
        "id": next_id,
        "title": f"{next_id.replace('_', ' ').title()}: {title_routes}",
        "status": "proposed",
        "requires_human_confirmation": True,
        "objective": objective,
        "schedules": {c: {**final[c], "owner_agent": agents[c].agent_id,
                          "summary": S.LIBRARY_BY_ID[final[c]["schedule_id"]].summary} for c in P.LOAVES},
        "success_criteria": _success_criteria(ev, final),
        "measurements": MEASUREMENTS,
        "held_constant": hyp["held_constant"],
        "window_h": [12, 16],
        "notes": ["End each chamber at its rise plateau, not by the clock (PLAN §5).",
                  "Schedules are setpoint proposals; API/firmware clamp and acknowledge every command."],
    }
    # 4) the agreed plan is published as a structured experiment_proposal (PLAN_MASTER §9.4)
    lead.publish("experiment_proposal", f"[{lead.agent_id} → {peer.agent_id}, operator] {next_experiment['title']}. "
                 f"{objective}", {"next_experiment": {k: next_experiment[k] for k in
                                                       ("id", "title", "objective", "status", "success_criteria")},
                                  "schedules": {c: {k: final[c][k] for k in ("schedule_id", "name", "points", "route")}
                                                for c in P.LOAVES},
                                  "reasoning_mode_label": reasoning},
                 evidence_source="simulated" if ev["prediction_source"] == "simulated" else "estimated",
                 recipients=[peer.agent_id, "operator"], experiment_id=next_id,
                 evidence_refs=[lead_msg.id, crit_msg.id, reply_msg.id], claim=objective,
                 uncertainty=lead_msg.payload["uncertainty"],
                 proposed_action={"type": "start_experiment", "experiment_id": next_id,
                                  "schedules": {c: final[c]["schedule_id"] for c in P.LOAVES},
                                  "requires_human_confirmation": True})
    staged = [f for f in staging.read() if f.id not in already]
    comparison = {c: {"recorded_schedule": ev["recorded"][c]["points"], "recorded_source": ev["recorded"][c]["source"],
                      "recorded_violations": ev["recorded"][c]["violations"], "judged": ev["recorded"][c]["judged"],
                      "model_rise_progress": ev["recorded"][c]["model"]["rise_progress"],
                      "model_overproof_risk": ev["recorded"][c]["model"]["overproof_risk"]} for c in P.LOAVES}
    tele_sources = {c: agents[c].state.telemetry_summary.get("sources", []) for c in P.LOAVES}
    provenance = {
        "decision_source": decision_source,
        "policy": P.POLICY_ID,
        "sensory_interpretation": f"{interp['method']} on judge free text (judge_feedback)",
        "predictions": ("simulated (fermentai.fermentation.simulation)" if ev["prediction_source"] == "simulated"
                        else "estimated (degree-hour heuristic, not a calibrated kinetic model)"
                        if ev["prediction_source"] == "estimated" else "mixed simulator/heuristic"),
        "prediction_source": ev["prediction_source"],
        "simulator_available": S.simulator_available(),
        "calibration": {k: {"status": v["status"], "n_points": v["n_points"], "note": v["note"]} for k, v in ev["anchors"].items()},
        "recorded_schedules": {c: ev["recorded"][c]["source"] for c in P.LOAVES},
        "telemetry_sources": tele_sources,
        "replay": bool(context.get("replay")),
        "agents_communicated": communicate,
        "observation_adapter": "fermentai.agents.observation (obs-1.0, quality-gated per PLAN_MASTER §9.1-9.2)",
        "physical_observations_imputed": False,
        "ph_status": {c: agents[c].state.ph_status for c in P.LOAVES},
        "proposer_reply": "offline_policy (rule-based)",
        "data_labels": {"judge feedback": "judge_feedback", "predictions": ev["prediction_source"],
                        "telemetry": tele_sources, "scripted demo": "replay" if context.get("replay") else None},
    }
    if adapter is not None:
        provenance["llm"] = adapter.describe()
    return {
        "cycle_id": cycle_id,
        "timestamp_utc": now,
        "feedback_text": interp["raw_text"],
        "sensory_interpretation": interp,
        "hypothesis": {
            "text": hyp["statement"],
            "statement": hyp["statement"],
            "mechanism": hyp["mechanism"],
            "proposed_by": lead.agent_id,
            "lead_reason": lead_reason,
            "challenged_by": peer.agent_id,
            "peer_agrees": crit_msg.payload["agrees_with_hypothesis"],
            "alternative_hypothesis": crit_msg.payload["alternative_hypothesis"],
            "independent_variable": hyp["independent_variable"],
            "dependent_variables": hyp["dependent_variables"],
            "held_constant": hyp["held_constant"],
            "targets": t["targets"],
            "explore": t["explore"],
            "predictions": {c: final[c]["predicted"] for c in P.LOAVES},
            "uncertainty": lead_msg.payload["uncertainty"],
            "confidence": conf,
            "status": "proposed",
        },
        "recommended_schedules": {c: final[c]["points"] for c in P.LOAVES},
        "agent_messages": [lead_msg.to_dict(), crit_msg.to_dict(), reply_msg.to_dict()],
        "next_experiment": next_experiment,
        "comparison_with_recorded": comparison,
        "current_run_actions": {c: agents[c].state.overproof for c in P.LOAVES},
        "observations": {c: {"latest": agents[c].observations[-1].compact() if agents[c].observations else None,
                             "quality": agents[c].state.observation_quality or {"n": 0, "note": "no telemetry"},
                             "ph_status": agents[c].state.ph_status,
                             "deviations_from_prediction": agents[c].state.deviations} for c in P.LOAVES},
        "candidate_ranking": [{"id": r["id"], "name": r["name"], "route": r["route"], "score": r["score"],
                               "pred_sourness": r["pred_sourness"], "pred_openness": r["pred_openness"],
                               "overproof_risk": r["model"]["overproof_risk"]}
                              for r in P.rank_candidates(ev, risk_weight=lead.state.risk_weight)[:6]],
        "findings_logged": [f.to_dict() for f in staged],
        "provenance": provenance,
        "safety_note": SAFETY_NOTE,
        "_staged": staged,
    }
