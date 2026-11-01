"""Per-station Sous Chef chat -> Head Chef hand-off (fermentai.agents.chat)."""

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


def run(sid, texts, ctx, mode="offline", station=None):
    c = dict(ctx, sous_chef=station) if station else ctx
    return [chat_turn(sid, t, c, mode=mode) for t in texts]


def test_plan14_conversation_at_station_a_reaches_handoff(ctx, tmp_path):
    turns = run("s14", ["", "Loaf A tastes mild and has nice texture.", "nothing else",
                        "Yes — B is more sour, which I prefer, but the crumb is too dense.",
                        "I'd like a more sour loaf that's still fluffy.", "Yes, that's right."], ctx, station="A")
    for r in turns:
        assert KEYS <= set(r)
    assert [r["stage"] for r in turns] == ["greeting", "collecting", "collecting", "collecting", "confirming", "handoff"]
    assert "Station A" in turns[0]["reply"] and "Loaf A" in turns[0]["reply"]
    assert "crust" in turns[1]["reply"].lower()  # sourness + crumb answered -> skip to aroma/anything else
    assert "Station B's loaf" in turns[2]["reply"] and "compare" in turns[2]["reply"].lower()
    assert "next loaf" in turns[3]["reply"]  # preference already stated while comparing -> the wish
    confirm = turns[4]
    u = confirm["understood"]
    assert u["structured_preference"] == PLAN14_PREFS
    assert u["station"] == "A" and u["compared_other"] is True and u["preferred_loaf"] == "B"
    assert "Loaf A here was mild" in confirm["reply"] and "Station B's loaf was sour and dense" in confirm["reply"]
    final = turns[-1]
    res = final["handoff_result"]
    assert res["next_experiment"]["id"] == "trial_002"
    assert res["sensory_interpretation"]["structured_preference"] == PLAN14_PREFS
    assert res["sensory_interpretation"]["preferred_loaf"] == "B"
    for needle in ("Head Chef", "line cooks", "Trial 002", "Station A", "Station B"):
        assert needle in final["reply"]
    assert [t["role"] for t in final["transcript"]][-2:] == ["sous_chef", "head_chef"]
    lines = [json.loads(l) for l in (tmp_path / "findings.jsonl").read_text().splitlines()]
    via = [f for f in lines if f["type"] == "sensory_feedback" and "via chat" in f["payload"].get("tags", [])]
    assert len(via) == 1 and via[0]["payload"]["structured_preference"] == PLAN14_PREFS
    assert [x["rank"] for x in res["suggestions"]] == [1, 2, 3] and "Reply 1, 2 or 3" in final["reply"]
    done = chat_turn("s14", "2", ctx)  # the two-loaf path also ends with a choice
    assert done["stage"] == "done" and done["chosen_suggestion"]["rank"] == 2


def test_single_lab_tasting_without_comparison_hands_off(ctx):
    r = run("single", ["", "mild and fluffy", "no", "no, only this one", "a bit more sour please", "yep"],
            ctx, station="A")
    assert [x["stage"] for x in r] == ["greeting", "collecting", "collecting", "collecting", "confirming", "handoff"]
    assert "Station B's loaf" in r[2]["reply"]
    assert "next loaf" in r[3]["reply"] and "stick with Loaf A" in r[3]["reply"] or "one loaf" in r[3]["reply"]
    u = r[-1]["understood"]
    assert u["compared_other"] is False and u["coverage"] == {"A": True, "B": False, "wish": True}
    assert u["preferred_loaf"] is None and "single loaf" in u["preference_basis"]
    assert u["consolidated_note"].startswith("Loaf A: mild and fluffy.")
    assert r[-1]["handoff_result"]["sensory_interpretation"]["loaves"]["B"]["attributes"] == {}


def test_tasting_with_comparison_records_both_loaves_and_preference(ctx):
    r = run("cmp", ["", "really tangy and a bit dense", "nice toasty crust", "yes I tried it",
                    "the other one was mild but fluffier", "This one, because I like the tang",
                    "keep the tang but make it lighter", "correct"], ctx, station="B")
    assert "Which did you prefer" in r[4]["reply"]
    u = r[-1]["understood"]
    assert u["compared_other"] and u["preferred_loaf"] == "B" and u["preference_basis"] == "explicit comparison"
    assert u["preference_reason"] == "you like the tang"
    assert u["loaves"]["A"]["sourness"]["label"] == "mild" and u["loaves"]["B"]["sourness"]["score"] > 0.65
    assert r[-2]["stage"] == "confirming" and "you preferred Loaf B because you like the tang" in r[-2]["reply"]
    assert r[-1]["stage"] == "handoff"


def test_station_b_attributes_loaves_correctly(ctx):
    r = run("stb", ["", "It was pretty sour, with a dense crumb.", "that's all", "yes",
                    "Station A's loaf was mild and airy.", "the other one"], ctx, station="B")
    assert "Sous Chef B" in r[0]["reply"] and "Station B" in r[0]["reply"] and "Loaf B" in r[0]["reply"]
    assert "Station A's loaf" in r[2]["reply"]
    u = r[-1]["understood"]
    assert u["station"] == "B"
    assert u["loaves"]["B"]["sourness"]["score"] > 0.65 and u["loaves"]["B"]["density"]["score"] > 0.6
    assert u["loaves"]["A"]["sourness"]["label"] == "mild" and u["loaves"]["A"]["texture"]["score"] > 0.8
    assert u["preferred_loaf"] == "A"  # "the other one" from Station B = Loaf A
    note = u["consolidated_note"]
    assert note.startswith("Loaf B: It was pretty sour") and "Loaf A was mild and airy" in note
    assert "I preferred Loaf A" in note


def test_one_shot_with_preference_goes_straight_to_confirm(ctx):
    r1 = chat_turn("one", "Loaf A was mild and fluffy. Loaf B was more sour but a little dense, and I preferred B. "
                          "I'd like a stronger sour flavor without losing the airy texture.", ctx)
    assert r1["stage"] == "confirming" and r1["understood"]["preferred_loaf"] == "B"
    r2 = chat_turn("one", "yep", ctx)
    assert r2["stage"] == "handoff" and r2["handoff_result"]["mode_label"] == "offline:deterministic_fallback"
    # without a stated preference the Sous Chef still asks for the explicit comparison
    r3 = chat_turn("one2", "Loaf A was mild and fluffy. Loaf B was more sour but a little dense. I'd like it sourer.", ctx)
    assert r3["stage"] == "collecting" and "which did you prefer" in r3["reply"].lower()


def test_unknown_offtopic_same_and_tie(ctx):
    r = run("p", ["", "I don't know", "what's the weather like?", "not sure", "dense"], ctx, station="A")
    assert "sour" in r[1]["reply"].lower()  # specific follow-up after "I don't know"
    off = r[2]
    assert off["stage"] == "collecting" and off["questions_remaining"] == r[1]["questions_remaining"]
    assert "sour" in off["reply"].lower()  # same question again, nothing consumed
    assert "crumb" in r[3]["reply"].lower() or "texture" in r[3]["reply"].lower()
    s = run("same", ["", "mild and airy", "nothing else", "yes", "same", "neither, they were the same", "no idea", "yes"],
            ctx, station="A")
    u = s[-2]["understood"]
    assert u["loaves"]["B"]["sourness"]["label"] == u["loaves"]["A"]["sourness"]["label"]
    assert u["flags"]["tie"] is True and u["preferred_loaf"] is None
    assert s[-2]["stage"] == "confirming" and "about equally" in s[-2]["reply"]
    assert s[-1]["stage"] == "handoff"


def test_correction_at_confirm(ctx):
    r = run("fix", ["Loaf A was mild and airy. I didn't try B. I'd like it a bit more sour."], ctx, station="A")
    assert r[0]["stage"] in ("collecting", "confirming")
    while r[-1]["stage"] == "collecting":
        r.append(chat_turn("fix", "no", ctx))
    assert r[-1]["stage"] == "confirming"
    what = chat_turn("fix", "no", ctx)
    assert what["stage"] == "collecting" and "what did I get wrong" in what["reply"]
    back = chat_turn("fix", "it was actually quite sour", ctx)
    assert back["stage"] == "confirming" and back["reply"].startswith("Thanks for the correction")
    assert back["understood"]["loaves"]["A"]["sourness"]["score"] > 0.5


def test_go_ahead_hands_off_even_if_incomplete(ctx):
    r = run("go", ["", "tangy", "go ahead"], ctx, station="A")
    assert r[-1]["stage"] == "handoff" and r[-1]["understood"]["coverage"]["B"] is False
    assert r[-1]["handoff_result"]["next_experiment"]["schedules"]


def test_reset_and_start_over(ctx):
    chat_turn("b", "", dict(ctx, sous_chef="B"))
    chat_turn("b", "mild", ctx)
    assert chat_reset("b", ctx) == {"session_id": "b", "reset": True}
    again = chat_turn("b", "", dict(ctx, sous_chef="B"))
    assert again["stage"] == "greeting" and len(again["transcript"]) == 1
    assert chat_turn("b", "start over please", ctx)["stage"] == "greeting"


def test_persistence_append_only_and_reload(ctx, tmp_path):
    chat_turn("keep", "", ctx)
    chat_turn("keep", "Mild, with a nice open crumb.", ctx)
    path = tmp_path / "chat.jsonl"
    first = path.read_bytes()
    C._CHATS.clear()  # simulate a server restart: state comes back from the JSONL log
    r = chat_turn("keep", "nothing else", ctx)
    assert r["stage"] == "collecting" and "Station B's loaf" in r["reply"]
    assert path.read_bytes().startswith(first)
    snap = SousChefChat(path).get("keep")
    assert snap["turn"] == 3 and snap["notes"][0].startswith("Loaf A: Mild")


def test_deterministic_offline(ctx, tmp_path):
    texts = ["", "mild", "airy", "nope", "no", "lighter please", "yes"]
    a = [r["reply"] for r in run("d1", texts, ctx)]
    C._CHATS.clear()
    b = [r["reply"] for r in run("d1", texts, dict(ctx, chat_sessions_path=str(tmp_path / "other.jsonl")))]
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


def _stub_adapter(reply):
    models = SimpleNamespace(list=lambda **kw: iter([SimpleNamespace(id="claude-sonnet-x", created_at="2026")]))
    return LLMAdapter("anthropic_api", client=SimpleNamespace(models=models, messages=_ChatMessages(reply)))


def test_live_without_configuration_falls_back(ctx):
    r = chat_turn("l0", "", ctx, mode="live")
    assert r["chat_mode_label"].startswith("offline:templated (live unavailable:")
    assert r["reasoning_mode_label"] == "Offline Policy" and "Sous Chef A" in r["reply"]


def test_live_llm_words_reply_but_never_decides_handoff(ctx):
    ad = _stub_adapter("Lovely! I'm handing this to the Head Chef now — any aroma or crust notes?")
    r = chat_turn("l1", "Loaf A was mild and airy.", dict(ctx, llm_adapter=ad), mode="live")
    assert r["chat_mode_label"] == "live:anthropic_api" and r["reasoning_mode_label"] == "Online Agent"
    assert r["reply"].startswith("Lovely! I'm handing")
    assert r["stage"] == "collecting" and r["handoff_result"] is None  # the state machine decides
    sent = json.loads(ad._client.messages.calls[0]["messages"][0]["content"].split("(JSON):\n", 1)[1])
    assert sent["next_action"] == "ask" and "crust" in sent["question_draft"].lower()


def test_live_bad_llm_reply_falls_back_to_template(ctx):
    ad = _stub_adapter("Thanks for that.")  # no question although one is required
    r = chat_turn("l2", "Loaf A was mild and airy.", dict(ctx, llm_adapter=ad), mode="live")
    assert r["chat_mode_label"].startswith("offline:templated (live unavailable: ValueError")
    assert "crust" in r["reply"].lower() and r["reasoning_mode_label"] == "Offline Policy"


def test_live_handoff_cycle_falls_back_cleanly(ctx):
    ad = _stub_adapter("So mild A, sour dense B, you preferred B, more sour but fluffy — right?")
    c = dict(ctx, llm_adapter=ad)
    r1 = chat_turn("l3", "Loaf A was mild and fluffy. Loaf B was more sour but dense and I preferred B. I'd like it "
                         "more sour but still fluffy.", c, mode="live")
    assert r1["stage"] == "confirming"
    r2 = chat_turn("l3", "yes", c, mode="live")
    assert r2["stage"] == "handoff"
    assert r2["handoff_result"]["mode_label"].startswith("offline:deterministic_fallback (live unavailable:")
    assert r2["reasoning_mode_label"] == "Offline Policy"


SINGLE = ["", "It was pretty mild and a bit dense.", "no", "no, only this one", "more tang and a lighter crumb", "yes"]


def test_single_loaf_a_gets_three_diverse_suggestions_and_choice(ctx):
    r = run("sg", SINGLE, ctx, station="A")
    h = r[-1]
    assert h["stage"] == "handoff" and "Which should we try? Reply 1, 2 or 3." in h["reply"]
    sg = h["handoff_result"]["suggestions"]
    assert [s["rank"] for s in sg] == [1, 2, 3] and h["suggestions"] == sg
    assert len({s["schedule_id"] for s in sg}) == 3 and len({s["title"] for s in sg}) == 3
    assert sg[0]["head_chef_pick"] and sg[0]["schedule_id"] == h["handoff_result"]["next_experiment"]["schedules"]["A"]["schedule_id"]
    for s in sg:
        assert len(s["title"].split()) <= 6 and s["station"] == "A" and s["provenance"] in ("simulated", "estimated")
        assert set(s["predicted"]) == {"ph_16h", "peak_rise", "overproof_h"} and s["risk"] and s["why"] and s["target"]
        assert s["points"] and s["points"][0][0] == 0.0
        assert f"{s['rank']}. {s['title']}" in h["reply"]
    # the three are different routes, not three near-identical steady holds
    from fermentai.agents.suggestions import category
    from fermentai.agents import schedules as S
    assert len({category(S.LIBRARY_BY_ID[s["schedule_id"]].to_dict()) for s in sg}) == 3
    bad = chat_turn("sg", "hmm, I don't know", ctx)
    assert bad["stage"] == "choosing" and bad["chosen_suggestion"] is None and "Reply 1, 2 or 3" in bad["reply"]
    ok = chat_turn("sg", "2", ctx)
    assert ok["stage"] == "done" and ok["chosen_suggestion"]["schedule_id"] == sg[1]["schedule_id"]
    assert f"Station A will run '{sg[1]['title']}' next. The operator applies it." in ok["reply"]
    assert chat_turn("sg", "thanks", ctx)["chosen_suggestion"] is None


@pytest.mark.parametrize("reply,idx", [("the third one", 2), ("first", 0), ("option 2", 1), ("number three", 2)])
def test_choice_phrasings(ctx, reply, idx):
    run("ph", SINGLE, ctx, station="A")
    r = chat_turn("ph", reply, ctx)
    assert r["chosen_suggestion"]["rank"] == idx + 1


def test_choice_by_title_fragment_and_ambiguity(ctx):
    r = run("tf", SINGLE, ctx, station="A")[-1]
    sg = r["handoff_result"]["suggestions"]
    assert chat_turn("tf", "2 or 3", ctx)["chosen_suggestion"] is None
    word = next(w for w in sg[2]["title"].lower().split() if w not in sg[0]["title"].lower() and w not in sg[1]["title"].lower())
    assert chat_turn("tf", f"let's go with the {word}", ctx)["chosen_suggestion"]["rank"] == 3


def test_start_over_resets_from_choosing(ctx):
    run("so", SINGLE, ctx, station="A")
    assert chat_turn("so", "what?", ctx)["stage"] == "choosing"
    assert chat_turn("so", "start over", ctx)["stage"] == "greeting"
