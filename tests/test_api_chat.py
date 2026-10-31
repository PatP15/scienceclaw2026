"""POST/GET/DELETE /api/v1/chat (kitchen-brigade conversation) with a fake and the real chat module."""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import auth, make_app, telemetry

HANDOFF = {
    "cycle_id": "cyc_fake_1",
    "sensory_interpretation": {"sourness": {"A": "low", "B": "high"}},
    "hypothesis": {"statement": "Warm late phase raises acidity", "proposed_by": "scientist_A"},
    "recommended_schedules": {"A": [[0, 90.0], [6, 24.0]], "B": [[0, 26.0]]},
    "agent_messages": [
        {"id": "f1", "agent_id": "scientist_A", "type": "hypothesis", "content": "[scientist_A → scientist_B] try warm"},
        {"id": "f2", "agent_id": "scientist_B", "type": "critique", "content": "overproof risk", "in_reply_to": "f1"},
    ],
    "next_experiment": {"id": "trial_chat_1", "title": "Warm finish", "schedules": {"A": {"points": [[0, 24.0]]}}},
    "mode_label": "Offline Policy",
    "provenance": {"decision_source": "offline_policy"},
}


def fake_chat_module(calls):
    mod = types.ModuleType("fermentai.agents.chat")
    sessions = {}

    def chat_turn(session_id, text, context=None, mode="offline"):
        calls.append({"session_id": session_id, "text": text, "context": context, "mode": mode})
        s = sessions.setdefault(session_id, {"transcript": [], "turn": 0})
        s["turn"] += 1
        s["transcript"].append({"role": "guest", "text": text, "ts": "t"})
        stage = {1: "collecting", 2: "handoff"}.get(s["turn"], "done")
        s["transcript"].append({"role": "sous_chef", "text": f"reply {s['turn']}", "ts": "t"})
        if s["turn"] == 2:
            s["transcript"].append({"role": "head_chef", "text": "plan ready", "ts": "t"})
        return {"session_id": session_id, "reply": f"reply {s['turn']}", "stage": stage,
                "understood": {"A": "mild"}, "questions_remaining": 1, "transcript": list(s["transcript"]),
                "handoff_result": HANDOFF if stage in ("handoff", "done") else None,
                "reasoning_mode_label": "Offline Policy"}

    def chat_reset(session_id, context=None):
        calls.append({"reset": session_id, "context": context})
        sessions.pop(session_id, None)
        return {"session_id": session_id, "reset": True}

    mod.chat_turn, mod.chat_reset = chat_turn, chat_reset
    return mod


@pytest.fixture
def client(tmp_path):
    return TestClient(make_app(tmp_path, device_tokens={"A": "dev-a"}))


@pytest.fixture
def fake(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "fermentai.agents.chat", fake_chat_module(calls))
    return calls


def chat(client, **body):
    return client.post("/api/v1/chat", headers=auth(), json=body)


def test_chat_flow_with_fake_module(client, fake, tmp_path):
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", chamber_temperature_c=24.0))
    before = client.get("/api/v1/chambers/A/state").json()["desired_setpoint_c"]
    r1 = chat(client, text="hello", sous_chef="B", judge_name="Judge 1")
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    sid = j1["session_id"]
    assert sid.startswith("chat_") and j1["stage"] == "collecting" and j1["handoff_persisted"] is False
    ctx = fake[0]["context"]
    for k in ("telemetry", "recent_telemetry", "current_setpoints", "experiments", "prior_feedback", "limits",
              "hardware_limits", "now_utc"):
        assert k in ctx, k
    assert ctx["sous_chef"] == "B" and ctx["chat_session_id"] == sid and ctx["judge_name"] == "Judge 1"
    assert ctx["chat_sessions_path"] == str(tmp_path / "chat_sessions.jsonl")
    assert ctx["telemetry"]["A"][-1]["chamber_temperature_c"] == 24.0

    j2 = chat(client, text="A mild, B sour and dense; want sour and airy", session_id=sid).json()
    assert j2["stage"] == "handoff" and j2["handoff_persisted"] is True and j2["feedback_id"]
    p = j2["persisted"]
    assert p["experiment"]["experiment_id"] == "trial_chat_1" and p["experiment"]["provenance"] == "offline_policy"
    assert len(p["motor_commands"]) == 2 and all(m["duration_s"] == 3.0 for m in p["motor_commands"])
    assert p["schedule_applied"] is False                                   # 90 C recommendation never applied
    assert client.get("/api/v1/chambers/A/state").json()["desired_setpoint_c"] == before
    fb = client.get("/api/v1/taste-feedback").json()[0]
    assert fb["chat_session_id"] == sid and fb["judge_name"] == "Judge 1"
    assert "A mild, B sour" in fb["text"] and "hello" in fb["text"]         # guest lines -> feedback text
    msgs = client.get("/api/v1/agents/all/messages").json()
    assert [m["sender"] for m in msgs] == ["scientist_A", "scientist_B"] and msgs[1]["recipient"] == "scientist_A"

    # a later turn re-returns the same hand-off -> not persisted twice
    j3 = chat(client, text="thanks!", session_id=sid).json()
    assert j3["stage"] == "done" and j3["handoff_persisted"] is False and j3["feedback_id"] == j2["feedback_id"]
    assert len(client.get("/api/v1/taste-feedback").json()) == 1
    assert len(client.get("/api/v1/commands?action=animate_motor").json()) == 2

    # SSE: one chat event per transcript line (no repeats), + feedback/feedback_result once
    ev = client.get("/api/v1/events?since=0&limit=500").json()
    chat_ev = [e["data"] for e in ev if e["type"] == "chat"]
    assert [(e["role"], e["text"]) for e in chat_ev] == [
        ("guest", "hello"), ("sous_chef", "reply 1"),
        ("guest", "A mild, B sour and dense; want sour and airy"), ("sous_chef", "reply 2"), ("head_chef", "plan ready"),
        ("guest", "thanks!"), ("sous_chef", "reply 3")]
    assert all(e["session_id"] == sid for e in chat_ev)
    fr = [e["data"] for e in ev if e["type"] == "feedback_result"]
    assert len(fr) == 1 and fr[0]["chat_session_id"] == sid

    st = client.get(f"/api/v1/chat/{sid}").json()
    assert st["stage"] == "done" and st["turns"] == 3 and len(st["transcript"]) == 7 and st["sous_chef"] == "B"

    d = client.delete(f"/api/v1/chat/{sid}", headers=auth()).json()
    assert d["reset"] and d["existed"] and d["module_reset"]
    assert fake[-1]["reset"] == sid and fake[-1]["context"]["chat_sessions_path"]
    assert client.get(f"/api/v1/chat/{sid}").status_code == 404


def test_chat_unavailable_is_labelled_503(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "fermentai.agents.chat", None)
    r = chat(client, text="hi")
    assert r.status_code == 503 and "chat unavailable" in r.json()["detail"]
    assert client.get("/api/v1/health").json()["chat_available"] is False
    assert client.delete("/api/v1/chat/x", headers=auth()).json()["module_reset"] is False


def test_chat_module_error_is_503(client, monkeypatch):
    mod = types.ModuleType("fermentai.agents.chat")

    def boom(session_id, text, context=None, mode="offline"):
        raise RuntimeError("LLM timeout")
    mod.chat_turn = boom
    monkeypatch.setitem(sys.modules, "fermentai.agents.chat", mod)
    r = chat(client, text="hi")
    assert r.status_code == 503 and "LLM timeout" in r.json()["detail"]


def test_chat_auth_and_validation(client, fake):
    assert client.post("/api/v1/chat", json={"text": "hi"}).status_code == 401
    assert client.post("/api/v1/chat", headers={"Authorization": "Bearer dev-a"}, json={"text": "hi"}).status_code == 403
    assert chat(client, text="").status_code == 422
    assert chat(client, text="hi", session_id="../../etc").status_code == 422
    assert chat(client, text="hi", sous_chef="C").status_code == 400   # demo lab: no chat station
    assert chat(client, text="hi", temperature_setpoint_c=90).status_code == 422
    assert client.get("/api/v1/chat/nope").status_code == 404
    assert client.delete("/api/v1/chat/x").status_code == 401


def test_real_chat_module_end_to_end(client, tmp_path):
    pytest.importorskip("fermentai.agents.chat")
    sid = None
    stages = []
    answers = iter(["Hi!", "Loaf A was mild and fluffy. Loaf B was more sour but dense. I want sour and airy.",
                    "I preferred Loaf B for its sourness, but I want it airier like Loaf A.",
                    "Loaf A crust was soft, Loaf B crust was crisp and dark. Aroma of A was milky, B vinegary.",
                    "that's everything", "that's everything", "that's everything", "that's everything"])
    for _ in range(10):
        # adaptive guest: confirm when the sous chef asks for confirmation, otherwise describe
        text = "yes, that's right" if stages and stages[-1] == "confirming" else next(answers, "yes")
        r = chat(client, text=text, session_id=sid, sous_chef="A")
        assert r.status_code == 200, r.text
        j = r.json()
        sid, stages = j["session_id"], stages + [j["stage"]]
        if j["handoff_persisted"]:
            break
    assert j["handoff_persisted"], stages
    assert j["persisted"]["experiment"]["provenance"] in ("offline_policy", "llm")
    assert {m["chamber_id"] for m in j["persisted"]["motor_commands"]} == {"A", "B"}
    assert client.get("/api/v1/taste-feedback").json()[0]["chat_session_id"] == sid
    roles = {e["data"]["role"] for e in client.get("/api/v1/events?types=chat&limit=200").json()}
    assert {"guest", "sous_chef", "head_chef"} <= roles
    assert (tmp_path / "chat_sessions.jsonl").exists()                   # module store kept out of the repo
    assert client.delete(f"/api/v1/chat/{sid}", headers=auth()).json()["module_reset"] is True


def test_real_chat_chosen_suggestion_persists_proposal_not_applied(client):
    pytest.importorskip("fermentai.agents.chat")
    sid, j = None, None
    for t in ["", "It was pretty mild and a bit dense.", "no", "no, only this one", "more tang and a lighter crumb", "yes"]:
        j = chat(client, text=t or "hi", session_id=sid, sous_chef="A").json()
        sid = j["session_id"]
    assert j["handoff_persisted"] and [s["rank"] for s in j["handoff_result"]["suggestions"]] == [1, 2, 3]
    assert chat(client, text="what?", session_id=sid, sous_chef="A").json()["stage"] == "choosing"
    r = chat(client, text="2", session_id=sid, sous_chef="A").json()
    assert r["stage"] == "done" and r["handoff_persisted"] is False
    ch = r["persisted"]["chosen"]
    sg = j["handoff_result"]["suggestions"][1]
    assert ch["status"] == "proposed" and ch["requires_operator_apply"] and ch["schedules"]["A"]["schedule_id"] == sg["schedule_id"]
    assert ch["experiment_id"] == j["persisted"]["experiment"]["experiment_id"]
    exps = {e["experiment_id"]: e for e in client.get("/api/v1/experiments").json()}
    assert exps[ch["experiment_id"]]["status"] == "proposed"
    ev = client.get("/api/v1/events?types=experiment,chat&limit=200").json()
    assert any(e["type"] == "experiment" and e["data"].get("chosen_suggestion") for e in ev)
    assert any(e["type"] == "chat" and "Chosen for Station A" in (e["data"].get("text") or "") for e in ev)
