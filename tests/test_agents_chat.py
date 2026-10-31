"""Conversational front door: Sous Chef chat -> Head Chef hand-off (fermentai.agents.chat)."""

import json
from types import SimpleNamespace

import pytest

from fermentai.agents import chat as C
from fermentai.agents.chat import SousChefChat, chat_reset, chat_turn
from fermentai.agents.llm_adapter import LLMAdapter

NOW = "2026-10-31T18:00:00Z"
PLAN14_PREFS = ["More sourness", "Lower density", "Preserve good rise (airy/fluffy crumb)"]
KEYS = {"session_id", "reply", "stage", "understood", "questions_remaining", "transcript", "handoff_result",
        "reasoning_mode_label"}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("FERMENTAI_LLM_MODE", raising=False)
    C._CHATS.clear()
    return {"chat_sessions_path": str(tmp_path / "chat.jsonl"), "findings_log_path": str(tmp_path / "findings.jsonl"),
            "now_utc": NOW}


def run(sid, texts, ctx, mode="offline"):
    return [chat_turn(sid, t, ctx, mode=mode) for t in texts]


def test_scripted_four_turn_conversation_reaches_handoff(ctx, tmp_path):
    turns = run("s14", ["", "Loaf A tastes mild and has nice texture.",
                        "B is more sour, which I prefer, but the crumb is too dense.",
                        "I'd like a more sour loaf that's still fluffy.", "Yes, that's right."], ctx)
    for r in turns:
        assert KEYS <= set(r)
    assert [r["stage"] for r in turns] == ["greeting", "collecting", "collecting", "confirming", "handoff"]
    assert "Sous Chef A" in turns[0]["reply"] and "?" in turns[0]["reply"]
    assert "Loaf B" in turns[1]["reply"]  # asks about the missing loaf
    assert [r["questions_remaining"] for r in turns] == [2, 1, 0, 0, 0]
    confirm = turns[3]
    assert confirm["understood"]["structured_preference"] == PLAN14_PREFS
    assert "right?" in confirm["reply"] or "Is that right?" in confirm["reply"]
    assert "Loaf A was mild" in confirm["reply"] and "Loaf B was sour and dense" in confirm["reply"]
    assert confirm["handoff_result"] is None
    final = turns[-1]
    res = final["handoff_result"]
    assert res and res["next_experiment"]["id"] == "trial_002"
    assert res["sensory_interpretation"]["structured_preference"] == PLAN14_PREFS
    for needle in ("Head Chef", "line cooks", "Trial 002", "Station A", "Station B", "pushed back"):
        assert needle in final["reply"]
    roles = [t["role"] for t in final["transcript"]]
    assert roles.count("guest") == 4 and roles[-2:] == ["sous_chef", "head_chef"]
    assert final["reasoning_mode_label"] == "Offline Policy"
    # the hand-off is logged as a sensory_feedback finding tagged "via chat"
    lines = [json.loads(l) for l in (tmp_path / "findings.jsonl").read_text().splitlines()]
    via = [f for f in lines if f["type"] == "sensory_feedback" and "via chat" in f["payload"].get("tags", [])]
    assert len(via) == 1 and via[0]["payload"]["session_id"] == "s14"
    assert via[0]["payload"]["structured_preference"] == PLAN14_PREFS
    # after hand-off the chat is done
    done = chat_turn("s14", "thanks!", ctx)
    assert done["stage"] == "done" and "trial_002" in done["reply"] and done["handoff_result"]


def test_one_shot_message_then_confirm(ctx):
    r1 = chat_turn("one", "Loaf A was mild and fluffy. Loaf B was more sour but a little dense. I'd like a stronger "
                          "sour flavor without losing the airy texture.", ctx)
    assert r1["stage"] == "confirming" and r1["questions_remaining"] == 2
    assert r1["understood"]["coverage"] == {"A": True, "B": True, "wish": True}
    r2 = chat_turn("one", "yep", ctx)
    assert r2["stage"] == "handoff" and r2["handoff_result"]["mode_label"] == "offline:deterministic_fallback"


def test_partial_unknown_offtopic_and_same(ctx):
    r = run("p", ["", "honestly I don't know", "what's the weather like?", "the second one was a bit sour I guess",
                  "same"], ctx)
    assert r[1]["stage"] == "collecting" and "Loaf A" in r[1]["reply"]
    off = r[2]
    assert off["stage"] == "collecting" and off["questions_remaining"] == r[1]["questions_remaining"]  # not consumed
    assert "Loaf A" in off["reply"]
    assert "B" in r[3]["understood"]["loaves"] and r[3]["understood"]["loaves"]["B"]["sourness"]["score"] > 0.5
    last = r[4]  # "same" -> Loaf A copies B; budget exhausted -> hand off
    assert last["understood"]["loaves"]["A"]["sourness"]["label"] == last["understood"]["loaves"]["B"]["sourness"]["label"]
    assert last["stage"] == "handoff" and "enough to go on" in last["reply"]


def test_both_same_dont_know_wish_and_correction(ctx):
    r = run("s", ["", "They were both the same really", "no idea", ], ctx)
    assert r[1]["understood"]["flags"]["both_same"] is True
    assert r[2]["stage"] == "confirming" and "open on what to change" in r[2]["reply"]
    fix = chat_turn("s", "No, actually the first was milder", ctx)
    assert fix["stage"] == "confirming" and fix["reply"].startswith("Thanks for the correction")
    assert fix["understood"]["loaves"]["A"]["sourness"]["label"] == "mild"
    neg = chat_turn("s2", "Loaf A was tangy, Loaf B was dense. Make it lighter.", ctx)
    assert neg["stage"] == "confirming"
    what = chat_turn("s2", "no", ctx)
    assert what["stage"] == "collecting" and "what did I get wrong" in what["reply"]
    back = chat_turn("s2", "B was actually airy", ctx)
    assert back["stage"] == "confirming"


def test_proceed_hands_off_even_if_incomplete(ctx):
    r = run("go", ["", "A was tangy", "that's all, go ahead"], ctx)
    assert r[-1]["stage"] == "handoff" and r[-1]["understood"]["coverage"]["B"] is False
    assert r[-1]["handoff_result"]["next_experiment"]["schedules"]


def test_sous_chef_b_and_reset(ctx):
    r = chat_turn("b", "", dict(ctx, sous_chef="B"))
    assert "Sous Chef B" in r["reply"] and r["sous_chef"] == "Sous Chef B"
    chat_turn("b", "Loaf A was mild", ctx)
    assert chat_reset("b", ctx) == {"session_id": "b", "reset": True}
    again = chat_turn("b", "", ctx)
    assert again["stage"] == "greeting" and len(again["transcript"]) == 1
    restart = chat_turn("b", "start over please", ctx)
    assert restart["stage"] == "greeting"


def test_persistence_append_only_and_reload(ctx, tmp_path):
    chat_turn("keep", "", ctx)
    chat_turn("keep", "Loaf A tastes mild and has nice texture.", ctx)
    path = tmp_path / "chat.jsonl"
    first = path.read_bytes()
    C._CHATS.clear()  # simulate a server restart: state comes back from the JSONL log
    r = chat_turn("keep", "B is more sour but too dense.", ctx)
    assert r["stage"] == "collecting" and len([t for t in r["transcript"] if t["role"] == "guest"]) == 2
    assert path.read_bytes().startswith(first)
    snap = SousChefChat(path).get("keep")
    assert snap["turn"] == 3 and snap["notes"][0].startswith("Loaf A tastes mild")


def test_deterministic_offline(ctx, tmp_path):
    texts = ["", "Loaf A was mild", "B was sour and dense", "lighter please", "yes"]
    a = [r["reply"] for r in run("d1", texts, ctx)]
    C._CHATS.clear()
    ctx2 = dict(ctx, chat_sessions_path=str(tmp_path / "other.jsonl"))
    b = [r["reply"] for r in run("d1", texts, ctx2)]
    assert a == b


# ---------------------------------------------------------------- live mode
class _ChatMessages:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create(self, **kw):
        self.calls.append(kw)
        props = kw["output_config"]["format"]["schema"]["properties"]
        if "reply" not in props:
            raise RuntimeError("only chat wording is mocked here")  # cycle LLM calls -> offline fallback
        out = {"reply": self.reply, "extracted_loaf_a": "", "extracted_loaf_b": "", "extracted_wish": ""}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(out))])


def _fake_adapter(reply):
    models = SimpleNamespace(list=lambda **kw: iter([SimpleNamespace(id="claude-sonnet-x", created_at="2026")]))
    return LLMAdapter("anthropic_api", client=SimpleNamespace(models=models, messages=_ChatMessages(reply)))


def test_live_without_configuration_falls_back(ctx):
    r = chat_turn("l0", "", ctx, mode="live")
    assert r["chat_mode_label"].startswith("offline:templated (live unavailable:")
    assert r["reasoning_mode_label"] == "Offline Policy" and "Sous Chef A" in r["reply"]


def test_live_llm_words_reply_but_never_decides_handoff(ctx):
    ad = _fake_adapter("Lovely! I'm handing this to the Head Chef now — what did Loaf B taste like?")
    r = chat_turn("l1", "Loaf A was mild and airy.", dict(ctx, llm_adapter=ad), mode="live")
    assert r["chat_mode_label"] == "live:anthropic_api" and r["reasoning_mode_label"] == "Online Agent"
    assert r["reply"].startswith("Lovely! I'm handing")
    assert r["stage"] == "collecting" and r["handoff_result"] is None  # the state machine decides
    sent = json.loads(ad._client.messages.calls[0]["messages"][0]["content"].split("(JSON):\n", 1)[1])
    assert sent["next_action"] == "ask" and "Loaf B" in sent["question_draft"]


def test_live_bad_llm_reply_falls_back_to_template(ctx):
    ad = _fake_adapter("Thanks for that.")  # no question although one is required
    r = chat_turn("l2", "Loaf A was mild and airy.", dict(ctx, llm_adapter=ad), mode="live")
    assert r["chat_mode_label"].startswith("offline:templated (live unavailable: ValueError")
    assert "Loaf B" in r["reply"] and r["reasoning_mode_label"] == "Offline Policy"


def test_live_handoff_cycle_falls_back_cleanly(ctx):
    ad = _fake_adapter("So mild A, sour dense B, and more sour but fluffy — right?")
    c = dict(ctx, llm_adapter=ad)
    r1 = chat_turn("l3", "Loaf A was mild and fluffy. Loaf B was more sour but dense. I'd like it more sour but "
                         "still fluffy.", c, mode="live")
    assert r1["stage"] == "confirming"
    r2 = chat_turn("l3", "yes", c, mode="live")
    assert r2["stage"] == "handoff"
    assert r2["handoff_result"]["mode_label"].startswith("offline:deterministic_fallback (live unavailable:")
    assert r2["reasoning_mode_label"] == "Offline Policy"  # the plan itself came from the offline policy
