"""The calibrated-evidence adapter must reproduce the deployed agents' evidence at default parameters."""

from fermentai.agents import offline_policy as P
from fermentai.agents import schedules as S
from fermentai.design.agent_adapter import (
    CONTEXT,
    build_evidence_calibrated,
    objective_interp,
    predict_schedule_with_params,
)
from fermentai.design.world import draw_world


def test_prediction_matches_agents_simulator_prediction():
    for c in S.LIBRARY:
        assert predict_schedule_with_params(c.points) == S.predict_schedule(c.points, True), c.id


def test_evidence_and_ranking_match_offline_policy_at_defaults():
    interp = objective_interp()
    ours = build_evidence_calibrated(None, interp, CONTEXT)
    theirs = P.build_evidence(interp, CONTEXT, use_simulator=True)
    assert ours["anchors"] == theirs["anchors"]
    assert [r["id"] for r in P.rank_candidates(ours)] == [r["id"] for r in P.rank_candidates(theirs)]
    assert ours["targets"] == theirs["targets"]


def test_calibrated_params_change_predictions():
    w = draw_world(0)
    a = build_evidence_calibrated(None)
    b = build_evidence_calibrated(w.true_params)
    assert [c["model"]["final_ph"] for c in a["candidates"]] != [c["model"]["final_ph"] for c in b["candidates"]]
