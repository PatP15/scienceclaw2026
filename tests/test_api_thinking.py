"""Chef-hat 'thinking' pulses while a taste cycle / chat turn is running."""

import sys
import time
import types

from fastapi.testclient import TestClient

from fermentai.api.schemas import ControlAckIn
from fermentai.api.testing import auth, make_app

FAST = {"thinking_pulses": {"motor_on_thinking": True, "pulse_ms": 100, "min_gap_s": 0.05,
                            "max_pulses_per_cycle": 2, "pattern": "nod"}}
RESULT = {"sensory_interpretation": {}, "hypothesis": "h", "recommended_schedules": {"A": [[0, 22]], "B": [[0, 27]]},
          "agent_messages": [], "next_experiment": {"title": "t"}, "mode_label": "Offline Policy",
          "provenance": {"decision_source": "offline_policy"}}


def slow_pipeline(monkeypatch, seconds, during=None):
    mod = types.ModuleType("fermentai.agents.pipeline")

    def run_taste_feedback_cycle(feedback_text, context, mode="offline"):
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            if during:
                during()
            time.sleep(0.02)
        return dict(RESULT)
    mod.run_taste_feedback_cycle = run_taste_feedback_cycle
    monkeypatch.setitem(sys.modules, "fermentai.agents.pipeline", mod)


def motors(c):
    return c.get("/api/v1/commands?command_type=motor&limit=200").json()


def test_taste_cycle_pulses_both_stations_then_final_wiggle(tmp_path, monkeypatch):
    c = TestClient(make_app(tmp_path, hw_overrides=FAST))
    slow_pipeline(monkeypatch, 0.5)
    control_before = len(c.get("/api/v1/commands?command_type=control").json())
    sp_before = {x: c.get(f"/api/v1/chambers/{x}/state").json()["desired_setpoint_c"] for x in "AB"}
    j = c.post("/api/v1/taste-feedback", headers=auth(), json={"text": "A mild, B sour"}).json()
    rows = motors(c)
    pulses = [r for r in rows if r["source"] == "thinking"]
    assert sorted(r["chamber_id"] for r in pulses) == ["A", "A", "B", "B"]      # max 2 per station
    assert all(r["payload"]["duration_s"] == 0.1 and r["payload"]["pattern"] == "nod" for r in pulses)
    assert sorted(j["thinking_pulse_ids"]) == sorted(r["command_id"] for r in pulses)
    # reply ready -> unpicked pulses withdrawn, final hand-off wiggle is the live command
    assert all(r["status"] == "superseded" for r in pulses)
    finals = [r for r in rows if r["source"] == "taste_feedback"]
    assert len(finals) == 2 and all(r["status"] == "issued" for r in finals)
    # SSE command events for the pulses
    ev = c.get("/api/v1/events?types=command&limit=500").json()
    assert sum(1 for e in ev if e["data"].get("source") == "thinking") == 4
    # never touches the Peltier path
    assert len(c.get("/api/v1/commands?command_type=control").json()) == control_before
    assert {x: c.get(f"/api/v1/chambers/{x}/state").json()["desired_setpoint_c"] for x in "AB"} == sp_before


def test_running_pulse_is_cancelled_before_final_wiggle(tmp_path, monkeypatch):
    app = make_app(tmp_path, hw_overrides=FAST)
    c = TestClient(app)
    svc = app.state.service
    acked = set()

    def device_picks_up_pulses():
        for r in svc.db.query("SELECT command_id, chamber_id FROM commands WHERE source='thinking' AND status='issued'"):
            if r["command_id"] not in acked:
                acked.add(r["command_id"])
                svc.record_ack(ControlAckIn(command_id=r["command_id"], chamber_id=r["chamber_id"], status="accepted"))
    slow_pipeline(monkeypatch, 0.3, during=device_picks_up_pulses)
    c.post("/api/v1/taste-feedback", headers=auth(), json={"text": "x"})
    pulses = [r for r in motors(c) if r["source"] == "thinking"]
    assert pulses and all(r["status"] == "cancelled" for r in pulses)          # running ones stopped
    stops = c.get("/api/v1/commands?action=stop_motor").json()
    assert {s["payload"]["target_command_id"] for s in stops} == {p["command_id"] for p in pulses}
    seq = {r["command_id"]: r["sequence_number"] for r in svc.db.query("SELECT * FROM commands")}
    final_a = next(r for r in motors(c) if r["source"] == "taste_feedback" and r["chamber_id"] == "A")
    assert all(seq[s["command_id"]] < seq[final_a["command_id"]] for s in stops if s["chamber_id"] == "A")


def test_pulse_capped_at_motor_max_and_disabled(tmp_path, monkeypatch):
    c = TestClient(make_app(tmp_path, hw_overrides={"thinking_pulses": {
        "motor_on_thinking": True, "pulse_ms": 60000, "min_gap_s": 0, "max_pulses_per_cycle": 1}}))
    slow_pipeline(monkeypatch, 0.1)
    c.post("/api/v1/taste-feedback", headers=auth(), json={"text": "x", "trigger_motors": False})
    pulses = [r for r in motors(c) if r["source"] == "thinking"]
    assert len(pulses) == 2 and all(r["payload"]["duration_s"] == 10.0 for r in pulses)
    assert all(r["status"] == "issued" for r in pulses)       # no final wiggle -> pulses left to play
    off = TestClient(make_app(tmp_path / "off"))               # helper default: disabled
    off.post("/api/v1/taste-feedback", headers=auth(), json={"text": "x"})
    assert [r for r in motors(off) if r["source"] == "thinking"] == []


def fake_chat(monkeypatch, seconds, handoff_on_turn=None):
    mod = types.ModuleType("fermentai.agents.chat")
    turns = {}

    def chat_turn(session_id, text, context=None, mode="offline"):
        time.sleep(seconds)
        turns[session_id] = turns.get(session_id, 0) + 1
        n = turns[session_id]
        h = dict(RESULT, cycle_id=f"cyc{n}") if n == handoff_on_turn else None
        return {"session_id": session_id, "reply": "ok", "stage": "handoff" if h else "collecting",
                "transcript": [{"role": "guest", "text": text, "ts": "t"}], "handoff_result": h,
                "reasoning_mode_label": "Offline Policy"}
    mod.chat_turn = chat_turn
    monkeypatch.setitem(sys.modules, "fermentai.agents.chat", mod)


def test_chat_pulses_only_the_sous_chef_station(tmp_path, monkeypatch):
    c = TestClient(make_app(tmp_path, hw_overrides=FAST))
    fake_chat(monkeypatch, 0.25, handoff_on_turn=2)
    j = c.post("/api/v1/chat", headers=auth(), json={"text": "hi", "sous_chef": "B"}).json()
    p1 = [r for r in motors(c) if r["source"] == "thinking"]
    assert p1 and {r["chamber_id"] for r in p1} == {"B"} and len(j["thinking_pulse_ids"]) == len(p1)
    assert all(r["status"] == "issued" for r in p1)            # plain reply: short pulses still play
    time.sleep(0.2)
    j2 = c.post("/api/v1/chat", headers=auth(), json={"text": "A mild, B sour", "session_id": j["session_id"]}).json()
    assert j2["handoff_persisted"] is True
    p2 = [r for r in motors(c) if r["source"] == "thinking" and r["command_id"] in j2["thinking_pulse_ids"]]
    assert p2 and {r["chamber_id"] for r in p2} == {"B"} and all(r["status"] == "superseded" for r in p2)
    finals = [r for r in motors(c) if r["source"] == "taste_feedback"]
    assert {r["chamber_id"] for r in finals} == {"A", "B"} and all(r["status"] == "issued" for r in finals)


def test_rate_limit_across_turns(tmp_path, monkeypatch):
    c = TestClient(make_app(tmp_path, hw_overrides={"thinking_pulses": {
        "motor_on_thinking": True, "pulse_ms": 100, "min_gap_s": 30, "max_pulses_per_cycle": 3}}))
    fake_chat(monkeypatch, 0.05)
    sid = c.post("/api/v1/chat", headers=auth(), json={"text": "hi", "sous_chef": "A"}).json()["session_id"]
    c.post("/api/v1/chat", headers=auth(), json={"text": "more", "session_id": sid})
    assert len([r for r in motors(c) if r["source"] == "thinking"]) == 1       # one per 30.1 s per station
