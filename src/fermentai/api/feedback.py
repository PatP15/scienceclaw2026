"""Taste feedback -> agents pipeline -> proposed experiment + motor animation.

The judge's free text is passed to `fermentai.agents.pipeline.run_taste_feedback_cycle`
(written by the agents workstream). Its output can influence temperature ONLY through
`ControlService.set_desired_setpoint()` (clamped, step-limited, rate-limited), and only if
schedule application is enabled. The motor animation goes through the separate motor path.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import uuid
from typing import Any

from .config import Settings
from .db import dumps, iso, loads_cols
from .devices import ControlService, agent_key
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


def jsonable(obj: Any) -> Any:
    """Best-effort conversion of pipeline/simulation outputs into JSON-safe structures."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if hasattr(obj, "model_dump"):
        return jsonable(obj.model_dump(mode="json"))
    if hasattr(obj, "to_dict") and hasattr(obj, "columns"):  # pandas DataFrame
        return {"columns": [str(c) for c in obj.columns],
                "data": jsonable(obj.to_dict(orient="list"))}
    if hasattr(obj, "tolist"):  # numpy
        return jsonable(obj.tolist())
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return jsonable(obj.to_dict())
    return str(obj)


def first_setpoint(steps: Any) -> float | None:
    """Extract the first step's setpoint from a schedule of unknown-but-plausible shape:
    [{"t_h":0,"setpoint_c":24}, ...] | [[0, 24], ...] | [24, ...] | {"steps": [...]}."""
    if isinstance(steps, dict):
        steps = steps.get("steps") or steps.get("schedule") or [steps]
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


def build_context(svc: ControlService, settings: Settings, fb: TasteFeedbackIn, feedback_id: int) -> dict:
    keep = ("id", "timestamp_utc", "source", "chamber_temperature_c", "starter_temperature_c",
            "relative_humidity_percent", "gas_resistance_ohms", "ph", "ph_method",
            "starter_height_mm", "door_open", "peltier_mode", "temperature_setpoint_c",
            "quality_flags", "experiment_id")
    chambers, recent, setpoints = {}, {}, {}
    for cid in svc.chamber_ids:
        ctl = svc.current_control(cid)
        rows = [{k: r.get(k) for k in keep} for r in svc.telemetry_history(cid, limit=50)]
        recent[cid] = rows
        setpoints[cid] = ctl["setpoint_c"]
        chambers[cid] = {"label": svc.hw.chambers[cid].label, "current_setpoint_c": ctl["setpoint_c"],
                         "setpoint_source": ctl["source"], "experiment_id": ctl.get("experiment_id")}
    experiments = [loads_cols(r, "schedules_json", "payload_json") for r in svc.db.query(
        "SELECT * FROM experiments ORDER BY id DESC LIMIT 20")]
    prior = [loads_cols(r, "result_json") for r in svc.db.query(
        "SELECT * FROM taste_feedback WHERE id<? ORDER BY id DESC LIMIT 10", (feedback_id,))]
    t = svc.hw.temperature
    return {
        "feedback_id": feedback_id,
        "judge_name": fb.judge_name,
        "loaf_notes": {"A": fb.loaf_a_notes, "B": fb.loaf_b_notes},
        "chambers": chambers,
        "current_setpoints": setpoints,
        "recent_telemetry": recent,
        "experiments": experiments,
        "prior_feedback": prior,
        "limits": {"min_setpoint_c": t.min_setpoint_c, "max_setpoint_c": t.max_setpoint_c,
                   "max_step_c_per_update": t.max_step_c_per_update},
        "llm_mode": settings.llm_mode,
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
        "provenance": {"source": "placeholder", "reason": why},
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


def _store_messages(svc: ControlService, feedback_id: int, messages: Any) -> list[dict]:
    stored = []
    if not isinstance(messages, list):
        messages = [] if messages is None else [messages]
    with svc.db.tx() as conn:
        for m in messages:
            md = m if isinstance(m, dict) else {"content": m}
            sender = md.get("from") or md.get("sender") or md.get("agent") or md.get("author")
            recipient = md.get("to") or md.get("recipient")
            kind = md.get("type") or md.get("kind") or md.get("message_type")
            now = iso(svc.clock())
            mid = conn.execute(
                "INSERT INTO agent_messages(created_at_utc, feedback_id, sender, recipient, sender_key,"
                " recipient_key, kind, content_json) VALUES (?,?,?,?,?,?,?,?)",
                (now, feedback_id, None if sender is None else str(sender),
                 None if recipient is None else str(recipient), agent_key(sender),
                 agent_key(recipient), None if kind is None else str(kind), dumps(md)),
            ).lastrowid
            rec = {"message_id": mid, "feedback_id": feedback_id, "created_at_utc": now,
                   "sender": sender, "recipient": recipient, "kind": kind, "content": md}
            svc.db.emit("message", rec, None, conn=conn)
            stored.append(rec)
    return stored


def store_experiment(svc: ControlService, exp: dict, *, proposed_by: str, provenance: str,
                     feedback_id: int | None = None, status: str = "proposed") -> dict:
    exp_id = str(exp.get("experiment_id") or f"exp_{svc.clock():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}")
    if svc.db.query_one("SELECT 1 FROM experiments WHERE experiment_id=?", (exp_id,)):
        exp_id = f"{exp_id}_{uuid.uuid4().hex[:6]}"
    schedules = exp.get("schedules") or {}
    now = iso(svc.clock())
    with svc.db.tx() as conn:
        conn.execute(
            "INSERT INTO experiments(experiment_id, created_at_utc, title, hypothesis, status,"
            " proposed_by, provenance, feedback_id, schedules_json, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (exp_id, now, str(exp.get("title") or "Untitled experiment")[:200],
             None if exp.get("hypothesis") is None else str(exp.get("hypothesis")),
             str(exp.get("status") or status), proposed_by, provenance, feedback_id,
             dumps(jsonable(schedules)), dumps(jsonable(exp))),
        )
        rec = {"experiment_id": exp_id, "created_at_utc": now, "title": exp.get("title"),
               "hypothesis": exp.get("hypothesis"), "status": exp.get("status") or status,
               "proposed_by": proposed_by, "provenance": provenance, "feedback_id": feedback_id,
               "schedules": jsonable(schedules)}
        svc.db.emit("experiment", rec, None, conn=conn)
    return rec


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


def run_feedback(svc: ControlService, settings: Settings, fb: TasteFeedbackIn) -> dict:
    now = iso(svc.clock())
    with svc.db.tx() as conn:
        fid = conn.execute(
            "INSERT INTO taste_feedback(created_at_utc, text, loaf_a_notes, loaf_b_notes, judge_name,"
            " mode) VALUES (?,?,?,?,?,?)",
            (now, fb.text, fb.loaf_a_notes, fb.loaf_b_notes, fb.judge_name, fb.mode),
        ).lastrowid
        svc.db.emit("feedback", {"feedback_id": fid, "created_at_utc": now, "text": fb.text,
                                 "loaf_a_notes": fb.loaf_a_notes, "loaf_b_notes": fb.loaf_b_notes,
                                 "judge_name": fb.judge_name, "mode": fb.mode}, None, conn=conn)

    context = build_context(svc, settings, fb, fid)
    result, available = call_pipeline(fb, context)

    messages = _store_messages(svc, fid, result.get("agent_messages"))

    nxt = result.get("next_experiment")
    nxt = dict(nxt) if isinstance(nxt, dict) else {"title": str(nxt) if nxt else "Next experiment"}
    nxt.setdefault("schedules", result.get("recommended_schedules") or {})
    nxt.setdefault("hypothesis", result.get("hypothesis") if isinstance(result.get("hypothesis"), str)
                   else json.dumps(result.get("hypothesis"), default=str))
    prov = result.get("provenance")
    prov_label = (prov.get("source") if isinstance(prov, dict) else None) or str(result.get("mode_label") or "unknown")
    experiment = store_experiment(svc, nxt, proposed_by="agents_taste_feedback",
                                  provenance=str(prov_label)[:64], feedback_id=fid)

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
        for cid in svc.chamber_ids:
            motors.append(svc.issue_motor_command(
                cid, dur, tf.motor_pattern, f"new experiment proposed ({experiment['experiment_id']})",
                "taste_feedback"))

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
    })
    return response
