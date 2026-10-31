"""Taste feedback -> agents pipeline -> proposed experiment + motor animation.

The judge's free text is passed to `fermentai.agents.pipeline.run_taste_feedback_cycle`
(written by the agents workstream). Its output can influence temperature ONLY through
`ControlService.set_desired_setpoint()` (clamped, step-limited, rate-limited), and only if
schedule application is enabled. The motor animation goes through the separate motor path.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from typing import Any

from .config import Settings
from .db import dumps, iso, loads_cols
from .devices import ControlService, agent_key
from .jsonutil import jsonable  # re-exported (routes/app import it from here)
from .schemas import TasteFeedbackIn

log = logging.getLogger("fermentai.api.feedback")

RESULT_KEYS = (
    "sensory_interpretation", "hypothesis", "recommended_schedules", "agent_messages",
    "next_experiment", "mode_label", "provenance",
)
SETPOINT_KEYS = ("setpoint_c", "temperature_setpoint_c", "temperature_c", "temp_c", "target_c",
                 "setpoint", "temperature")


def pipeline_available() -> bool:
    try:
        from fermentai.agents.pipeline import run_taste_feedback_cycle  # noqa: F401
    except ImportError:
        return False
    return True




def first_setpoint(steps: Any) -> float | None:
    """Extract the first step's setpoint from a schedule of unknown-but-plausible shape:
    [{"t_h":0,"setpoint_c":24}, ...] | [[0, 24], ...] | [24, ...] | {"steps": [...]}."""
    if isinstance(steps, dict):
        steps = steps.get("points") or steps.get("steps") or steps.get("schedule") or [steps]
    if not isinstance(steps, (list, tuple)) or not steps:
        return None
    s0 = steps[0]
    val: Any = None
    if isinstance(s0, dict):
        for k in SETPOINT_KEYS:
            if k in s0:
                val = s0[k]
                break
    elif isinstance(s0, (list, tuple)) and len(s0) >= 2:
        val = s0[1]
    elif isinstance(s0, (int, float)) and not isinstance(s0, bool):
        val = s0
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def telemetry_record(row: dict) -> dict:
    """Full telemetry record as stored: the device payload (all v1.1 fields, field_provenance,
    diagnostics, canonical source) plus server fields (receipt time, server quality flags)."""
    rec = dict(row.get("payload") or {})
    rec.update({
        "telemetry_id": row["id"],
        "chamber_id": row["chamber_id"],
        "timestamp_utc": row.get("timestamp_utc"),   # NULL stays null (never invented)
        "received_at_utc": row["received_at_utc"],
        "time_basis": row.get("time_basis"),
        "source": row["source"],
        "quality_flags": row.get("quality_flags") or [],
        "field_provenance": rec.get("field_provenance") or {},
    })
    return rec


def build_context(svc: ControlService, settings: Settings, fb: TasteFeedbackIn | None,
                  feedback_id: int | None, extra: dict | None = None) -> dict:
    """Context for the agents (taste-feedback cycle and chat). fb/feedback_id may be None
    (chat turns before a hand-off): prior feedback is then simply the latest 10."""
    keep = ("id", "timestamp_utc", "source", "chamber_temperature_c", "starter_temperature_c",
            "relative_humidity_percent", "gas_resistance_ohms", "ph", "ph_method",
            "starter_height_mm", "door_open", "peltier_mode", "temperature_setpoint_c",
            "quality_flags", "experiment_id")
    chambers, recent, setpoints, raw = {}, {}, {}, {}
    for cid in svc.lab_ids:   # demo labs (kind=demo) are not part of the sous-chef contexts
        ctl = svc.current_control(cid)
        history = svc.telemetry_history(cid, limit=50)
        recent[cid] = [{k: r.get(k) for k in keep} for r in history]   # v1.0-era compact view
        raw[cid] = [telemetry_record(r) for r in history]
        setpoints[cid] = ctl["setpoint_c"]
        chambers[cid] = {"label": svc.hw.chambers[cid].label, "current_setpoint_c": ctl["setpoint_c"],
                         "setpoint_source": ctl["source"], "experiment_id": ctl.get("experiment_id")}
    experiments = [loads_cols(r, "schedules_json", "payload_json") for r in svc.db.query(
        "SELECT * FROM experiments ORDER BY id DESC LIMIT 20")]
    prior = [loads_cols(r, "result_json") for r in svc.db.query(
        "SELECT * FROM taste_feedback WHERE id<? ORDER BY id DESC LIMIT 10",
        (feedback_id if feedback_id is not None else 2**62,))]
    t = svc.hw.temperature
    hw_limits = {
        **svc.hw.firmware_local_limits,
        "setpoint_min_c": t.min_setpoint_c,
        "setpoint_max_c": t.max_setpoint_c,
        "max_step_c_per_update": t.max_step_c_per_update,
        "min_update_interval_s": t.min_update_interval_s,
        "stale_after_s": float(svc.hw.telemetry.stale_after_s),
        "motor_max_duration_s": svc.hw.motor.max_duration_s,
    }
    running = svc.db.query_one(
        "SELECT experiment_id FROM experiments WHERE status='running' ORDER BY id DESC LIMIT 1")
    exp_ids = {cid: chambers[cid]["experiment_id"] for cid in chambers}
    latest_tel_exp = next((r.get("experiment_id") for cid in raw for r in reversed(raw[cid])
                           if r.get("experiment_id")), None)
    current_exp = ((running or {}).get("experiment_id")
                   or next((e for e in exp_ids.values() if e), None) or latest_tel_exp)
    return {
        "feedback_id": feedback_id,
        "now_utc": iso(svc.clock()),          # server clock: staleness judged consistently
        "experiment_id": current_exp,         # current run (None if nothing is running)
        "experiment_ids": exp_ids,
        "telemetry": raw,                     # full stored records per chamber (oldest -> newest)
        "hardware_limits": hw_limits,
        "judge_name": fb.judge_name if fb else None,
        "loaf_notes": {"A": fb.loaf_a_notes if fb else None, "B": fb.loaf_b_notes if fb else None},
        "chambers": chambers,
        "current_setpoints": setpoints,
        "setpoints": setpoints,               # key the agents' offline policy reads
        "recent_telemetry": recent,
        "experiments": experiments,
        "prior_feedback": prior,
        "limits": {"min_setpoint_c": t.min_setpoint_c, "max_setpoint_c": t.max_setpoint_c,
                   "max_step_c_per_update": t.max_step_c_per_update},
        "llm_mode": settings.llm_mode,
        # The pipeline appends to this findings log (served by GET /api/v1/findings).
        **({"findings_log_path": str(settings.findings_path)} if settings.findings_path else {}),
        **(extra or {}),
    }


def placeholder_result(fb: TasteFeedbackIn, context: dict, why: str) -> dict:
    sp = context["current_setpoints"]
    return {
        "sensory_interpretation": {
            "raw_text": fb.text, "loaf_a_notes": fb.loaf_a_notes, "loaf_b_notes": fb.loaf_b_notes,
            "interpreted": False,
            "note": f"PLACEHOLDER - feedback saved but not interpreted ({why}).",
        },
        "hypothesis": "PLACEHOLDER - no hypothesis generated; agents pipeline unavailable.",
        "recommended_schedules": {cid: [{"t_h": 0.0, "setpoint_c": v}] for cid, v in sp.items()},
        "agent_messages": [{
            "from": "system", "to": "all", "type": "notice",
            "content": f"Agents pipeline unavailable ({why}); holding current setpoints.",
        }],
        "next_experiment": {
            "title": "PLACEHOLDER - repeat current schedules",
            "hypothesis": None,
            "schedules": {cid: [{"t_h": 0.0, "setpoint_c": v}] for cid, v in sp.items()},
        },
        "mode_label": "placeholder (agents pipeline unavailable)",
        "provenance": {"source": "placeholder", "decision_source": "placeholder", "reason": why},
    }


def call_pipeline(fb: TasteFeedbackIn, context: dict) -> tuple[dict, bool]:
    try:
        from fermentai.agents.pipeline import run_taste_feedback_cycle
    except ImportError as e:
        return placeholder_result(fb, context, f"ImportError: {e}"), False
    try:
        result = run_taste_feedback_cycle(fb.text, context, mode=fb.mode)
        if not isinstance(result, dict):
            raise TypeError(f"pipeline returned {type(result).__name__}, expected dict")
    except Exception as e:  # the demo must survive pipeline failures
        log.exception("taste feedback pipeline failed")
        return placeholder_result(fb, context, f"pipeline error: {type(e).__name__}: {e}"), False
    result = jsonable(result)
    for k in RESULT_KEYS:
        result.setdefault(k, None)
    return result, True


_ARROW = re.compile(r"^\s*\[\s*([^\]\s]+)\s*(?:→|->)\s*([^\]\s]+)\s*\]")


def _store_messages(svc: ControlService, feedback_id: int, messages: Any) -> list[dict]:
    """Store agent messages. Accepts Finding dicts (agent_id, id, in_reply_to, ...) as produced
    by fermentai.agents.communication, and looser {from, to, type, content} dicts."""
    stored = []
    if not isinstance(messages, list):
        messages = [] if messages is None else [messages]
    senders_by_uid: dict[str, str] = {}
    with svc.db.tx() as conn:
        for m in messages:
            md = m if isinstance(m, dict) else {"content": m}
            sender = (md.get("agent_id") or md.get("from") or md.get("sender") or md.get("agent")
                      or md.get("author"))
            uid = md.get("id") if isinstance(md.get("id"), str) else None
            in_reply_to = md.get("in_reply_to")
            recipient = md.get("to_agent_id") or md.get("to") or md.get("recipient")
            if recipient is None and isinstance(md.get("content"), str):
                hit = _ARROW.match(md["content"])  # "[scientist_A → scientist_B] ..."
                if hit:
                    recipient = hit.group(2)
            if recipient is None and in_reply_to:
                recipient = senders_by_uid.get(in_reply_to)
                if recipient is None:
                    row = conn.execute("SELECT sender FROM agent_messages WHERE message_uid=? LIMIT 1",
                                       (in_reply_to,)).fetchone()
                    recipient = row["sender"] if row else None
            kind = md.get("type") or md.get("kind") or md.get("message_type")
            now = iso(svc.clock())
            mid = conn.execute(
                "INSERT INTO agent_messages(created_at_utc, feedback_id, sender, recipient, sender_key,"
                " recipient_key, kind, content_json, message_uid, in_reply_to, chamber_id, cycle_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, feedback_id, None if sender is None else str(sender),
                 None if recipient is None else str(recipient), agent_key(sender),
                 agent_key(recipient), None if kind is None else str(kind), dumps(md), uid,
                 in_reply_to, md.get("chamber_id"), md.get("cycle_id")),
            ).lastrowid
            if uid and sender:
                senders_by_uid[uid] = str(sender)
            rec = {"message_id": mid, "message_uid": uid, "feedback_id": feedback_id,
                   "created_at_utc": now, "sender": sender, "recipient": recipient,
                   "in_reply_to": in_reply_to, "kind": kind, "content": md}
            svc.db.emit("message", rec, None, conn=conn)
            stored.append(rec)
    return stored


def store_experiment(svc: ControlService, exp: dict, *, proposed_by: str, provenance: str,
                     feedback_id: int | None = None, status: str = "proposed") -> dict:
    exp_id = str(exp.get("experiment_id") or exp.get("id")
                 or f"exp_{svc.clock():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}")[:64]
    if svc.db.query_one("SELECT 1 FROM experiments WHERE experiment_id=?", (exp_id,)):
        exp_id = f"{exp_id[:56]}_{uuid.uuid4().hex[:6]}"
    schedules = exp.get("schedules") or {}
    now = iso(svc.clock())
    hyp = exp.get("hypothesis")
    hyp_text = hyp if isinstance(hyp, str) or hyp is None else (
        hyp.get("statement") or hyp.get("text") if isinstance(hyp, dict) else None) or json.dumps(hyp, default=str)
    with svc.db.tx() as conn:
        conn.execute(
            "INSERT INTO experiments(experiment_id, created_at_utc, title, hypothesis, status,"
            " proposed_by, provenance, feedback_id, schedules_json, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (exp_id, now, str(exp.get("title") or "Untitled experiment")[:200], hyp_text,
             str(exp.get("status") or status), proposed_by, provenance, feedback_id,
             dumps(jsonable(schedules)), dumps(jsonable(exp))),
        )
        if isinstance(schedules, dict):
            for cid, steps in schedules.items():
                conn.execute(
                    "INSERT INTO temperature_schedules(experiment_id, chamber_id, created_at_utc, source,"
                    " steps_json) VALUES (?,?,?,?,?)",
                    (exp_id, str(cid), now, provenance, dumps(normalize_steps(steps))))
        rec = {"experiment_id": exp_id, "created_at_utc": now, "title": exp.get("title"),
               "hypothesis": hyp_text, "status": exp.get("status") or status,
               "proposed_by": proposed_by, "provenance": provenance, "feedback_id": feedback_id,
               "schedules": jsonable(schedules)}
        svc.db.emit("experiment", rec, None, conn=conn)
    return rec


def normalize_steps(steps: Any) -> list[dict]:
    """Best-effort [{t_h, setpoint_c}] view of a schedule (pairs, dicts, {points: ...})."""
    if isinstance(steps, dict):
        steps = steps.get("points") or steps.get("steps") or steps.get("schedule") or []
    out = []
    for st in steps if isinstance(steps, (list, tuple)) else []:
        if isinstance(st, (list, tuple)) and len(st) >= 2:
            out.append({"t_h": st[0], "setpoint_c": st[1]})
        elif isinstance(st, dict):
            sp = next((st[k] for k in SETPOINT_KEYS if k in st), None)
            out.append({"t_h": st.get("t_h", st.get("time_h")), "setpoint_c": sp})
    return out


def apply_schedules(svc: ControlService, schedules: Any, source: str, rationale: str,
                    experiment_id: str | None = None) -> dict:
    """Push the FIRST step of each chamber's schedule through the safety-clamped setpoint path."""
    out: dict[str, Any] = {}
    if not isinstance(schedules, dict):
        return out
    for cid, steps in schedules.items():
        if cid not in svc.hw.chambers:
            out[str(cid)] = {"skipped": True, "reason": "unknown chamber"}
            continue
        sp = first_setpoint(steps)
        if sp is None:
            out[cid] = {"skipped": True, "reason": "could not parse first schedule step"}
            continue
        out[cid] = svc.set_desired_setpoint(cid, sp, source, rationale[:1000])
        if experiment_id and out[cid]["changed"]:
            svc.db.execute("UPDATE chamber_control SET experiment_id=? WHERE chamber_id=?",
                           (experiment_id, cid))
    return out


def _store_hypothesis_and_calibration(svc: ControlService, fid: int, experiment_id: str,
                                      result: dict, provenance: str) -> None:
    hyp = result.get("hypothesis")
    now = iso(svc.clock())
    with svc.db.tx() as conn:
        if hyp:
            statement = hyp if isinstance(hyp, str) else (
                (hyp.get("statement") or hyp.get("text")) if isinstance(hyp, dict) else None) or str(hyp)
            agent = hyp.get("proposed_by") if isinstance(hyp, dict) else None
            conn.execute(
                "INSERT INTO scientist_hypotheses(created_at_utc, feedback_id, experiment_id, agent_id,"
                " statement, provenance, payload_json) VALUES (?,?,?,?,?,?,?)",
                (now, fid, experiment_id, agent, statement, provenance, dumps(hyp)))
        prov = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
        cal = prov.get("calibration")
        if isinstance(cal, dict):
            for model, c in cal.items():
                c = c if isinstance(c, dict) else {"value": c}
                conn.execute(
                    "INSERT INTO model_calibrations(created_at_utc, model, status, n_points, source,"
                    " feedback_id, payload_json) VALUES (?,?,?,?,?,?,?)",
                    (now, f"sensory_map:{model}", c.get("status"),
                     c.get("n_points") if isinstance(c.get("n_points"), int) else None,
                     provenance, fid, dumps(c)))


def insert_feedback(svc: ControlService, fb: TasteFeedbackIn, chat_session_id: str | None = None
                    ) -> tuple[int, str]:
    """Store the judge's feedback and emit the SSE `feedback` event."""
    now = iso(svc.clock())
    with svc.db.tx() as conn:
        fid = conn.execute(
            "INSERT INTO taste_feedback(created_at_utc, text, loaf_a_notes, loaf_b_notes, judge_name,"
            " mode, chat_session_id) VALUES (?,?,?,?,?,?,?)",
            (now, fb.text, fb.loaf_a_notes, fb.loaf_b_notes, fb.judge_name, fb.mode, chat_session_id),
        ).lastrowid
        svc.db.emit("feedback", {"feedback_id": fid, "created_at_utc": now, "text": fb.text,
                                 "loaf_a_notes": fb.loaf_a_notes, "loaf_b_notes": fb.loaf_b_notes,
                                 "judge_name": fb.judge_name, "mode": fb.mode,
                                 "chat_session_id": chat_session_id}, None, conn=conn)
    return fid, now


def run_feedback(svc: ControlService, settings: Settings, fb: TasteFeedbackIn) -> dict:
    from .thinking import ThinkingPulses

    fid, now = insert_feedback(svc, fb)
    context = build_context(svc, settings, fb, fid)
    with ThinkingPulses(svc, svc.lab_ids, "taste cycle") as tp:   # both stations' hats nod
        result, available = call_pipeline(fb, context)
    pulse_ids = tp.finish(clear_for_final_wiggle=fb.trigger_motors)
    response = persist_cycle(svc, settings, fb, fid, now, result, available)
    response["thinking_pulse_ids"] = pulse_ids
    return response


def normalize_cycle_result(result: dict) -> dict:
    result = jsonable(result)
    for k in RESULT_KEYS:
        result.setdefault(k, None)
    return result


def persist_cycle(svc: ControlService, settings: Settings, fb: TasteFeedbackIn, fid: int, now: str,
                  result: dict, available: bool, *, chat_session_id: str | None = None) -> dict:
    """Everything that follows a taste cycle (shared by POST /taste-feedback and the chat
    hand-off): agent messages, proposed experiment (+ hypotheses/schedules), optional clamped
    schedule application, bounded motor commands, stored result and the SSE feedback_result."""
    messages = _store_messages(svc, fid, result.get("agent_messages"))

    nxt = result.get("next_experiment")
    nxt = dict(nxt) if isinstance(nxt, dict) else {"title": str(nxt) if nxt else "Next experiment"}
    nxt.setdefault("schedules", result.get("recommended_schedules") or {})
    nxt.setdefault("hypothesis", result.get("hypothesis"))
    prov = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    # Explicit provenance (dashboard request): the pipeline's decision_source
    # (offline_policy | llm | placeholder), never the free-text mode_label.
    prov_label = str(prov.get("decision_source") or prov.get("source") or "unknown")[:64]
    experiment = store_experiment(svc, nxt, proposed_by="agents_taste_feedback",
                                  provenance=prov_label, feedback_id=fid)
    _store_hypothesis_and_calibration(svc, fid, experiment["experiment_id"], result, prov_label)

    apply = settings.hardware.taste_feedback.apply_first_schedule_step if fb.apply_schedule is None \
        else fb.apply_schedule
    applied = {}
    if apply and available:
        applied = apply_schedules(svc, result.get("recommended_schedules"), "taste_feedback_schedule",
                                  f"feedback #{fid}: {result.get('hypothesis')}",
                                  experiment["experiment_id"])

    motors = []
    if fb.trigger_motors:
        tf = settings.hardware.taste_feedback
        dur = min(tf.motor_duration_s, settings.hardware.motor.max_duration_s)
        for cid in svc.lab_ids:
            motors.append(svc.issue_motor_command(
                cid, dur, tf.motor_pattern, f"new experiment proposed ({experiment['experiment_id']})",
                "taste_feedback", experiment_id=experiment["experiment_id"]))

    response = {
        "feedback_id": fid,
        "received_at_utc": now,
        "mode": fb.mode,
        "pipeline_available": available,
        "result": result,
        "experiment": experiment,
        "stored_messages": messages,
        "schedule_applied": bool(apply and available),
        "applied_setpoints": applied,
        "motor_commands": motors,
        **({"chat_session_id": chat_session_id} if chat_session_id else {}),
    }
    svc.db.execute(
        "UPDATE taste_feedback SET mode_label=?, pipeline_available=?, result_json=? WHERE id=?",
        (str(result.get("mode_label")), int(available), dumps(response), fid),
    )
    svc.db.emit("feedback_result", {
        "feedback_id": fid, "pipeline_available": available, "mode_label": result.get("mode_label"),
        "sensory_interpretation": result.get("sensory_interpretation"),
        "hypothesis": result.get("hypothesis"),
        "recommended_schedules": result.get("recommended_schedules"),
        "next_experiment": experiment, "provenance": result.get("provenance"),
        "applied_setpoints": applied,
        "motor_command_ids": [m["command_id"] for m in motors],
        "chat_session_id": chat_session_id,
    })
    return response
