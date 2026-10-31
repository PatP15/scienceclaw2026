"""Free-text sensory parsing (fermentai.agents.sensory)."""

import pytest

from fermentai.agents.sensory import parse_feedback

PLAN_EXAMPLE = ("Loaf A was mild and fluffy. Loaf B was more sour but a little dense. "
                "I'd like a stronger sour flavor without losing the airy texture.")
SP = {"A": 22.0, "B": 27.0}


def attr(r, loaf, dim):
    return r["loaves"][loaf]["attributes"].get(dim)


def all_evidence(r):
    for l in ("A", "B"):
        for a in r["loaves"][l]["attributes"].values():
            yield from a["evidence"]
    for a in r["unattributed"]["attributes"].values():
        yield from a["evidence"]
    for d in r["desired"].values():
        yield from d["evidence"]


def test_plan_example():
    r = parse_feedback(PLAN_EXAMPLE, SP)
    a_s, b_s = attr(r, "A", "sourness"), attr(r, "B", "sourness")
    assert a_s["label"] == "mild" and a_s["score"] < 0.4 and a_s["confidence"] > 0.7
    assert b_s["score"] > a_s["score"] + 0.3
    assert attr(r, "A", "texture")["score"] >= 0.8
    assert 0.6 <= attr(r, "B", "density")["score"] < 0.8  # "a little dense" is softened
    assert r["desired"]["sourness"]["direction"] == "increase"
    assert r["desired"]["texture"]["direction"] == "maintain"
    assert r["desired"]["texture"]["preferred_pole"] == "high"
    assert r["primary_dimension"] == "sourness"
    assert any(c["dimension"] == "sourness" and c["higher"] == "B" for c in r["comparisons"])
    assert "without losing the airy" in " ".join(e["text"] for e in r["desired"]["texture"]["evidence"])
    assert r["provenance"] == "judge_feedback"


@pytest.mark.parametrize("text", [
    PLAN_EXAMPLE,
    "The first one was not very sour. The second was really tangy and kind of gummy.",
    "B was way too vinegary for me, A was nicer. Make it milder next time but keep the open crumb.",
    "Loaf A: tight crumb, faint aroma. Loaf B: airy, tangy.",
])
def test_evidence_spans_point_into_the_text(text):
    r = parse_feedback(text, SP)
    ev = list(all_evidence(r))
    assert ev
    for e in ev:
        assert text[e["start"]:e["end"]] == e["text"]


def test_negation_not_very_sour():
    r = parse_feedback("The first one was not very sour. The second was really tangy and kind of gummy.", SP)
    a = attr(r, "A", "sourness")["score"]
    assert 0.3 < a < 0.5
    assert attr(r, "B", "sourness")["score"] > 0.65
    assert attr(r, "B", "texture")["score"] < 0.4
    assert any("gummy" in f for f in r["flags"])


def test_negation_not_sour_at_all():
    r = parse_feedback("Loaf B wasn't sour at all.", SP)
    assert attr(r, "B", "sourness")["score"] < 0.4


def test_no_loaf_names_unattributed_and_too():
    r = parse_feedback("It was too sour and a bit dense.", SP)
    assert not r["loaves"]["A"]["attributes"] and not r["loaves"]["B"]["attributes"]
    assert r["unattributed"]["attributes"]["sourness"]["score"] > 0.75
    assert r["desired"]["sourness"]["direction"] == "decrease"
    assert r["desired"]["sourness"]["implied"] is True
    assert any("No loaf was named" in n for n in r["notes"])


def test_warmer_cooler_one_uses_setpoints():
    text = "The warmer one was sharper than the cooler one, and the cooler one had a lovely open crumb."
    r = parse_feedback(text, {"A": 27.0, "B": 22.0})  # swapped setpoints -> warmer is A
    assert attr(r, "A", "sourness")["score"] > 0.7
    assert attr(r, "B", "texture")["score"] > 0.7
    r2 = parse_feedback(text, SP)
    assert attr(r2, "B", "sourness")["score"] > 0.7


def test_comparative_than_numbered_chambers():
    r = parse_feedback("Chamber 2 was less sour than chamber 1.", SP)
    assert attr(r, "B", "sourness")["score"] < attr(r, "A", "sourness")["score"]
    assert {"dimension": "sourness", "higher": "A", "lower": "B"}.items() <= r["comparisons"][0].items()


def test_negated_desire_and_lighter():
    r = parse_feedback("Loaf A: tight crumb, faint aroma. Loaf B: airy, tangy. I don't want it any more sour, just lighter.", SP)
    assert r["desired"]["sourness"]["direction"] == "maintain"
    assert r["desired"]["sourness"]["preferred_pole"] == "low"
    assert r["desired"]["texture"]["direction"] == "increase"
    assert attr(r, "A", "texture")["score"] < 0.3


def test_preference():
    r = parse_feedback("I preferred loaf B. It smelled amazing, nutty and complex.", SP)
    assert r["preferred_loaf"] == "B"
    assert attr(r, "B", "aroma")["score"] > 0.6
    assert "nutty" in r["loaves"]["B"]["aroma_tags"]


def test_indifference_is_not_a_preference():
    r = parse_feedback("I want it much more sour, texture doesn't matter.", SP)
    assert r["desired"]["sourness"]["direction"] == "increase"
    assert r["desired"]["sourness"]["strength"] == "strong"
    assert "texture" not in r["desired"]


def test_neither_and_more_tang():
    r = parse_feedback("Neither was very sour. I want more tang!", SP)
    assert attr(r, "A", "sourness")["score"] < 0.5 and attr(r, "B", "sourness")["score"] < 0.5
    assert r["desired"]["sourness"]["direction"] == "increase"


def test_article_a_is_not_loaf_a():
    r = parse_feedback("A bit more flavor would be nice, but not too sour.", SP)
    assert not r["loaves"]["A"]["references"]
    assert r["desired"]["flavor_intensity"]["direction"] == "increase"
    assert r["desired"]["sourness"]["direction"] == "maintain"
    assert r["desired"]["sourness"]["preferred_pole"] == "low"


def test_bare_letters():
    r = parse_feedback("A was gummy, B was airy and smelled great.", SP)
    assert attr(r, "A", "texture")["score"] < 0.3
    assert attr(r, "B", "texture")["score"] > 0.8
    assert attr(r, "B", "aroma")["score"] > 0.6


def test_vinegary_too_much():
    r = parse_feedback("B was way too vinegary for me, A was nicer. Make it milder next time but keep the open crumb.", SP)
    assert attr(r, "B", "sourness")["score"] > 0.9
    assert "acetic" in r["loaves"]["B"]["aroma_tags"]
    assert r["desired"]["sourness"]["direction"] == "decrease"
    assert r["desired"]["texture"]["direction"] == "maintain"
    assert r["preferred_loaf"] == "A"


def test_both_loaves_not_sour_enough():
    r = parse_feedback("Both loaves were lovely and fluffy, though not sour enough.", SP)
    for l in "AB":
        assert attr(r, l, "texture")["score"] > 0.8
        assert attr(r, l, "sourness")["score"] < 0.5
    assert r["desired"]["sourness"]["direction"] == "increase"


@pytest.mark.parametrize("text", ["", "   ", "Honestly both tasted great.", "asdf qwerty 1234", "😀👍"])
def test_degenerate_inputs_do_not_crash(text):
    r = parse_feedback(text, SP)
    assert r["desired"] == {} or isinstance(r["desired"], dict)
    assert isinstance(r["notes"], list)
    assert r["desired_summary"]


def test_deterministic():
    assert parse_feedback(PLAN_EXAMPLE, SP) == parse_feedback(PLAN_EXAMPLE, SP)
