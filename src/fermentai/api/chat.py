"""Kitchen-brigade chat (guest <-> sous chef <-> head chef) on top of the agents' chat module.

`fermentai.agents.chat.chat_turn(session_id, text, context, mode) -> dict` owns the conversation;
this module gives it the same context as a taste-feedback cycle, keeps a per-session record for
GET /api/v1/chat/{id}, emits SSE `chat` events for new transcript lines, and — when a turn
returns a `handoff_result` (a full taste-cycle dict) — persists it exactly like
POST /api/v1/taste-feedback (feedback row, agent messages, experiment, motor commands,
SSE feedback/feedback_result). Judge text still only reaches temperature via the clamped
set_desired_setpoint() path, and only if schedule application is enabled.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from typing import Any

from .config import Settings
from .db import dumps, iso, loads_cols
from .devices import CommandError, ControlService
from .feedback import build_context, insert_feedback, normalize_cycle_result, persist_cycle
from .jsonutil import jsonable
from .schemas import TasteFeedbackIn

STAGES = ("greeting", "collecting", "confirming", "handoff", "done")
UNAVAILABLE_LABEL = "chat unavailable: fermentai.agents.chat is not installed (use the taste-feedback form)"


def _chat_module():
    try:
        # import_module consults sys.modules (a `from pkg import sub` would read the package
        # attribute instead, which ignores a module that has been replaced or removed)
        mod = importlib.import_module("fermentai.agents.chat")
    except ImportError as e:
        raise CommandError(f"{UNAVAILABLE_LABEL} [{e}]", 503) from e
    if not hasattr(mod, "chat_turn"):
        raise CommandError(f"{UNAVAILABLE_LABEL} [chat_turn missing]", 503)
    return mod


def chat_available() -> bool:
    try:
        _chat_module()
    except CommandError:
        return False
    return True


def _session_row(svc: ControlService, session_id: str) -> dict | None:
    return loads_cols(svc.db.query_one("SELECT * FROM chat_sessions WHERE session_id=?", (session_id,)),
                      "state_json")


def _handoff_key(h: dict) -> str:
    cid = h.get("cycle_id")
    if isinstance(cid, str) and cid:
        return cid
    return hashlib.sha256(json.dumps(h, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _guest_text(transcript: list, fallback: str) -> str:
    lines = [str(t.get("text", "")) for t in transcript if isinstance(t, dict) and t.get("role") == "guest"]
    return ("\n".join(x for x in lines if x).strip() or fallback)[:4000]


def chat_turn(svc: ControlService, settings: Settings, *, text: str, session_id: str | None,
              mode: str = "offline", sous_chef: str | None = None, judge_name: str | None = None) -> dict:
    mod = _chat_module()
    session_id = session_id or f"chat_{uuid.uuid4().hex[:12]}"
    prev = _session_row(svc, session_id)
    context = build_context(svc, settings, None, None, extra={
        "chat_session_id": session_id, "sous_chef": sous_chef, "judge_name": judge_name,
        "channel": "chat", **_store_ctx(settings)})
    try:
        raw = mod.chat_turn(session_id, text, context, mode=mode)
    except Exception as e:  # keep the API alive; the dashboard can fall back to the form
        raise CommandError(f"chat turn failed: {type(e).__name__}: {e}", 503) from e
    if not isinstance(raw, dict):
        raise CommandError(f"chat_turn returned {type(raw).__name__}, expected dict", 503)
    result: dict[str, Any] = jsonable(raw)
    result.setdefault("session_id", session_id)
    transcript = result.get("transcript") if isinstance(result.get("transcript"), list) else []
    stage = result.get("stage") if result.get("stage") in STAGES else str(result.get("stage") or "collecting")
    now = iso(svc.clock())

    # SSE: one `chat` event per transcript entry we have not announced yet
    already = prev["transcript_len"] if prev else 0
    if len(transcript) < already:          # module restarted / transcript trimmed: re-announce tail
        already = 0
    for entry in transcript[already:]:
        if isinstance(entry, dict):
            svc.db.emit("chat", {"session_id": session_id, "role": entry.get("role"), "text": entry.get("text"),
                                 "ts": entry.get("ts"), "stage": stage,
                                 "reasoning_mode_label": result.get("reasoning_mode_label")})

    # hand-off -> persist exactly like a taste-feedback submission (once per hand-off)
    handoff = result.get("handoff_result")
    persisted = None
    feedback_id = prev["feedback_id"] if prev else None
    handoff_key = prev["handoff_key"] if prev else None
    if isinstance(handoff, dict) and _handoff_key(handoff) != handoff_key:
        handoff_key = _handoff_key(handoff)
        fb = TasteFeedbackIn(text=_guest_text(transcript, str(handoff.get("feedback_text") or text)),
                             judge_name=(judge_name or (prev or {}).get("judge_name") or None),
                             mode=mode if mode in ("live", "offline") else "offline")
        feedback_id, received = insert_feedback(svc, fb, chat_session_id=session_id)
        cycle = normalize_cycle_result(handoff)
        persisted = persist_cycle(svc, settings, fb, feedback_id, received, cycle, True,
                                  chat_session_id=session_id)

    with svc.db.tx() as conn:
        conn.execute(
            "INSERT INTO chat_sessions(session_id, created_at_utc, updated_at_utc, stage, mode, sous_chef,"
            " judge_name, turns, transcript_len, handoff_key, feedback_id, state_json)"
            " VALUES (?,?,?,?,?,?,?,1,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET"
            " updated_at_utc=excluded.updated_at_utc, stage=excluded.stage, mode=excluded.mode,"
            " sous_chef=COALESCE(excluded.sous_chef, chat_sessions.sous_chef),"
            " judge_name=COALESCE(excluded.judge_name, chat_sessions.judge_name),"
            " turns=chat_sessions.turns+1, transcript_len=excluded.transcript_len,"
            " handoff_key=excluded.handoff_key, feedback_id=excluded.feedback_id,"
            " state_json=excluded.state_json",
            (session_id, now, now, stage, mode, sous_chef, judge_name, len(transcript), handoff_key,
             feedback_id, dumps(result)))
    return {**result, "session_id": session_id, "stage": stage, "feedback_id": feedback_id,
            "handoff_persisted": persisted is not None, "persisted": persisted}


def chat_state(svc: ControlService, session_id: str) -> dict:
    row = _session_row(svc, session_id)
    if row is None:
        raise CommandError(f"unknown chat session {session_id!r}", 404)
    state = row.pop("state") or {}
    return {**state, "session_id": session_id, "stage": row["stage"], "turns": row["turns"],
            "feedback_id": row["feedback_id"], "created_at_utc": row["created_at_utc"],
            "updated_at_utc": row["updated_at_utc"], "mode": row["mode"], "sous_chef": row["sous_chef"],
            "judge_name": row["judge_name"]}


def _store_ctx(settings: Settings) -> dict:
    return {"chat_sessions_path": str(settings.chat_sessions_path)} if settings.chat_sessions_path else {}


def chat_reset(svc: ControlService, settings: Settings, session_id: str) -> dict:
    module_reset = False
    try:
        mod = _chat_module()
        if hasattr(mod, "chat_reset"):
            try:
                mod.chat_reset(session_id, _store_ctx(settings) or None)
            except TypeError:                  # module variant without a context argument
                mod.chat_reset(session_id)
            module_reset = True
    except CommandError:
        pass                                   # no module: just forget our record
    existed = svc.db.query_one("SELECT 1 FROM chat_sessions WHERE session_id=?", (session_id,)) is not None
    svc.db.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
    svc.db.emit("chat", {"session_id": session_id, "role": None, "text": None, "stage": "reset"})
    return {"session_id": session_id, "reset": True, "existed": existed, "module_reset": module_reset}
