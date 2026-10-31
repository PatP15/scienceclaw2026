"""End-to-end taste-feedback cycle (fermentai.agents.pipeline.run_taste_feedback_cycle)."""

import copy
import json

import pytest

from fermentai.agents import schedules as S
from fermentai.agents.pipeline import run_taste_feedback_cycle
from fermentai.agents.scientist import assess_overproofing, starter_readiness

PLAN_EXAMPLE = ("Loaf A was mild and fluffy. Loaf B was more sour but a little dense. "
                "I'd like a stronger sour flavor without losing the airy texture.")
NOW = "2026-10-31T18:00:00Z"
REQUIRED = {"sensory_interpretation", "hypothesis", "recommended_schedules", "agent_messages", "next_experiment",
            "mode_label", "provenance", "safety_note"}

VARIED = [
    PLAN_EXAMPLE,
    "B was way too vinegary for me, A was nicer. Make it milder next time but keep the open crumb.",
    "Both loaves were lovely and fluffy, though not sour enough.",
    "Honestly both tasted great.",
    "A tasted bland and flat. B had a nice tang but was heavy like a brick.",
    "I want it much more sour, texture doesn't matter.",
    "The first one was gummy. The second one collapsed and smelled boozy.",
    "I preferred loaf B. It smelled amazing, nutty and complex.",
    "",
    "Could the crumb be more open? Flavor was fine.",
]


def ctx(**kw):
    base = {"persist_findings": False, "now_utc": NOW}
    base.update(kw)
    return base


def _strip(d):
    """Remove volatile fields (ids/timestamps/cycle ids) for comparisons without a fixed clock."""
    s = json.dumps(d, sort_keys=True, default=str)
    import re
    s = re.sub(r'"(f|cyc)_[0-9a-f]{12,16}"', '"<id>"', s)
    s = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', "<ts>", s)
    return s


def test_plan_example_structure():
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx())
    assert REQUIRED <= set(r)
    assert r["mode_label"] == "offline:deterministic_fallback"
    assert r["provenance"]["decision_source"] == "offline_policy"
    si = r["sensory_interpretation"]
    assert si["desired"]["sourness"]["direction"] == "increase"
    assert si["loaves"]["A"]["attributes"]["sourness"]["label"] == "mild"
    h = r["hypothesis"]
    assert h["text"] and h["mechanism"] and h["proposed_by"] != h["challenged_by"]
    ne = r["next_experiment"]
    for k in ("id", "title", "objective", "success_criteria", "measurements", "schedules"):
        assert ne[k]
    assert set(ne["schedules"]) == {"A", "B"}
    assert ne["schedules"]["A"]["points"] == r["recommended_schedules"]["A"]
    assert ne["id"] == "trial_002"
    assert "clamp" in r["safety_note"] and "LLM" in r["safety_note"]
    json.dumps(r)  # JSON-serializable


def test_offline_cycle_is_deterministic():
    a = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx())
    b = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx())
    assert a == b
    c = run_taste_feedback_cycle(PLAN_EXAMPLE, {"persist_findings": False})
    d = run_taste_feedback_cycle(PLAN_EXAMPLE, {"persist_findings": False})
    assert _strip(c) == _strip(d)


def test_agents_are_distinct_and_b_reads_a():
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx())
    proposal, critique, reply = r["agent_messages"]
    assert proposal["agent_id"] != critique["agent_id"]
    assert proposal["type"] == "hypothesis" and critique["type"] == "critique" and reply["type"] == "rationale"
    assert critique["in_reply_to"] == proposal["id"]
    assert reply["in_reply_to"] == critique["id"] and reply["agent_id"] == proposal["agent_id"]
    assert proposal["content"] != critique["content"]
    # the critique references the proposer's concrete choice
    assert proposal["payload"]["own_decision"]["name"] in critique["content"]
    assert critique["payload"]["challenged_agent_id"] == proposal["agent_id"]
    # each agent owns its own chamber's decision
    assert proposal["payload"]["own_decision"]["chamber"] == proposal["chamber_id"]
    assert critique["payload"]["own_decision"]["chamber"] == critique["chamber_id"]
    assert critique["chamber_id"] != proposal["chamber_id"]
    for m in r["agent_messages"]:
        assert m["evidence_source"] in ("real", "simulated", "replay", "estimated", "imported_human", "judge_feedback")


def test_communication_changes_decision_vs_independent_mode():
    talk = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx())
    alone = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx(communicate=False))
    assert alone["provenance"]["agents_communicated"] is False
    crit = talk["agent_messages"][1]["payload"]
    assert crit["independent_choice"]["schedule_id"] == alone["agent_messages"][1]["payload"]["own_decision"]["schedule_id"]
    if crit["decision_changed_by_peer"]:
        assert crit["own_decision"]["schedule_id"] != crit["independent_choice"]["schedule_id"]
    # with communication the two chambers do not run the same schedule
    assert talk["recommended_schedules"]["A"] != talk["recommended_schedules"]["B"]


@pytest.mark.parametrize("use_sim", [True, False])
@pytest.mark.parametrize("text", VARIED)
def test_recommended_schedules_always_within_bounds(text, use_sim):
    r = run_taste_feedback_cycle(text, ctx(use_simulator=use_sim))
    for c in ("A", "B"):
        pts = r["recommended_schedules"][c]
        assert S.validate_schedule(pts) == []
        assert all(S.MIN_TEMP_C <= t <= S.MAX_TEMP_C for _, t in pts)
        assert r["next_experiment"]["schedules"][c]["schedule_id"] in S.LIBRARY_BY_ID
    assert r["mode_label"].startswith("offline:deterministic_fallback")


@pytest.mark.parametrize("context", [None, {}, {"telemetry": "garbage", "setpoints": [1, 2], "experiments": "x"},
                                     {"telemetry": {"A": [None, {"foo": 1}]}, "setpoints": {"A": "hot"}},
                                     {"prior_feedback": [None, 3, {"text": "make it more sour"}]}])
def test_missing_or_malformed_context_is_tolerated(context):
    c = copy.deepcopy(context) if context is not None else None
    if isinstance(c, dict):
        c["persist_findings"] = False
        r = run_taste_feedback_cycle("Both were nice", c)
    else:
        r = run_taste_feedback_cycle("Both were nice", {"persist_findings": False})
    assert REQUIRED <= set(r)


def test_prior_feedback_carries_direction():
    r = run_taste_feedback_cycle("Both were nice.", ctx(prior_feedback=["I'd like it more sour next time."]))
    assert r["sensory_interpretation"]["desired"]["sourness"]["direction"] == "increase"
    assert r["sensory_interpretation"]["desired"]["sourness"]["from_prior_feedback"] is True


def test_recorded_experiments_used_and_next_id():
    exps = [{"experiment_id": "trial_004", "chambers": {
        "A": {"schedule": [[0, 22], [12, 22]]}, "B": {"setpoint_c": 26, "duration_h": 12}}}]
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx(experiments=exps))
    assert r["next_experiment"]["id"] == "trial_005"
    assert r["comparison_with_recorded"]["A"]["recorded_schedule"] == [[0.0, 22.0], [12.0, 22.0]]
    assert r["provenance"]["recorded_schedules"]["A"] == "recorded experiment"


def _telemetry(heights, setpoint, source="simulated"):
    rows = []
    for i, h in enumerate(heights):
        hh, mm = divmod(i * 30, 60)
        rows.append({"schema_version": "1.0", "chamber_id": "X", "timestamp_utc": f"2026-10-31T{8 + hh:02d}:{mm:02d}:00Z",
                     "chamber_temperature_c": setpoint, "starter_height_mm": h, "temperature_setpoint_c": setpoint,
                     "ph": None, "source": source})
    return rows


def test_overproofing_rule_from_telemetry():
    declining = _telemetry([20, 24, 29, 34, 38, 40, 40.5, 39, 37.5], 27.0)
    rising = _telemetry([20, 21, 22.5, 24, 26], 22.0)
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx(telemetry={"A": rising, "B": declining}))
    act = r["current_run_actions"]
    assert act["B"]["status"] == "declining" and act["B"]["action"] == "end_fermentation"
    assert act["B"]["requires_human_confirmation"] is True
    assert act["A"]["action"] == "continue"
    interventions = [f for f in r["findings_logged"] if f["type"] == "proposed_intervention"
                     and f["payload"].get("scope") == "current_run"]
    assert interventions and interventions[0]["chamber_id"] == "B"
    assert interventions[0]["evidence_source"] == "simulated"
    assert "simulated" in r["provenance"]["telemetry_sources"]["B"]


def test_overproofing_rule_variants():
    near = assess_overproofing(_telemetry([20, 25, 31, 36, 38.5, 39, 39.1], 27.0))
    assert near["status"] == "near_peak" and near["action"] == "step_down"
    assert near["suggested_setpoint_c"] <= 23.0
    pred = {"time_h": [0, 2, 4, 6, 8], "expansion_ratio": [1.0, 1.3, 1.8, 2.0, 1.9]}
    by_model = assess_overproofing(_telemetry([20, 21, 22], 27.0), pred, elapsed_h=5.8)
    assert by_model["status"] == "near_peak" and "prediction" in by_model["rationale"]
    assert assess_overproofing(None)["status"] == "insufficient_data"


def test_starter_readiness():
    ok = starter_readiness(6.5, True, True, False, age_days=14, consecutive_doublings=3)
    assert ok["status"] == "ready" and ok["contingency"] is None
    young = starter_readiness(11.0, True, None, False, age_days=4.5)
    assert young["status"] == "not_ready"
    assert "CONTINGENCY" in young["contingency"] and "disclosed" in young["contingency"]
    assert young["risks"]
    assert starter_readiness(mold_or_discoloration=True)["status"] == "unsafe"


def test_findings_log_persistence_is_append_only(tmp_path):
    path = tmp_path / "findings_log.jsonl"
    c = {"findings_log_path": str(path)}
    r1 = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(c, now_utc="2026-10-31T18:00:00Z"))
    first = path.read_bytes()
    assert r1["findings_log"]["persisted"] and r1["findings_log"]["appended"] == len(first.splitlines())
    r2 = run_taste_feedback_cycle("Make it a bit milder.", dict(c, now_utc="2026-10-31T19:00:00Z"))
    second = path.read_bytes()
    assert second.startswith(first) and len(second.splitlines()) > len(first.splitlines())
    assert r2["findings_log"]["appended"] == len(second.splitlines()) - len(first.splitlines())
    types = {json.loads(l)["type"] for l in second.splitlines()}
    assert {"observation", "hypothesis", "prediction", "uncertainty", "proposed_measurement",
            "proposed_intervention", "critique", "rationale"} <= types
    # re-running the identical cycle (same clock) is idempotent: no duplicate lines
    run_taste_feedback_cycle(PLAN_EXAMPLE, dict(c, now_utc="2026-10-31T18:00:00Z"))
    assert path.read_bytes() == second


def test_replay_labels():
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, ctx(replay=True))
    assert all(f["evidence_source"] == "replay" for f in r["findings_logged"])
    assert r["provenance"]["replay"] is True
