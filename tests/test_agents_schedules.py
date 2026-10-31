"""Candidate schedule library, validation and predictions (fermentai.agents.schedules)."""

import pytest

from fermentai.agents import schedules as S


def test_library_is_valid_and_bounded():
    assert len(S.LIBRARY) >= 12
    assert len({c.id for c in S.LIBRARY}) == len(S.LIBRARY)
    for c in S.LIBRARY:
        assert S.validate_schedule(c.points) == [], c.id
        for _t, temp in c.points:
            assert S.MIN_TEMP_C <= temp <= S.MAX_TEMP_C
        assert S.max_contiguous_hold_at_or_above(c.points, S.HOT_THRESHOLD_C) <= S.MAX_HOT_HOLD_H + 0.05
        assert c.summary and c.mechanism and c.route in S.ROUTE_TEXT


@pytest.mark.parametrize("points,needle", [
    ([(0, 27), (14, 27)], "without a step-down"),
    ([(0, 40), (10, 40)], "outside"),
    ([(0, 2), (10, 2)], "outside"),
    ([(1, 22), (10, 22)], "t=0"),
    ([(0, 30), (0.2, 18), (10, 18)], "ramp"),
    ([(0, 22), (30, 22)], "duration"),
    ([], "empty"),
    ([(0, 22), (5, float("nan"))], "non-finite"),
])
def test_validation_rejects(points, needle):
    errs = S.validate_schedule(points)
    assert errs and any(needle in e for e in errs), errs


def test_recorded_27c_14h_is_flagged_but_predictable():
    pts = S.steady_schedule(27.0, 14.0)
    assert S.validate_schedule(pts)
    p = S.predict_schedule(pts, use_simulator=False)
    assert p["overproof_risk"] > 0.9


def test_heuristic_kinetics_directions():
    cool = S.predict_schedule(S.steady_schedule(20, 12), use_simulator=False)
    warm = S.predict_schedule(S.steady_schedule(27, 10), use_simulator=False)
    # warmer -> faster acidification and faster rise (higher overproof risk)
    assert warm["acid_progress"] / 10 > cool["acid_progress"] / 12
    assert warm["overproof_risk"] > cool["overproof_risk"]
    # cooler -> more acetic-leaning
    assert cool["acetic_index"] > warm["acetic_index"]
    assert warm["provenance"] == "estimated"


def test_predictions_are_deterministic_and_schema_complete():
    keys = {"sourness_index", "openness_index", "overproof_risk", "rise_progress", "acetic_index", "provenance"}
    for c in S.LIBRARY:
        a = S.predict_schedule(c.points)
        b = S.predict_schedule(c.points)
        assert a == b
        assert keys <= set(a)
        assert 0.0 <= a["sourness_index"] <= 1.0 and 0.0 <= a["openness_index"] <= 1.0


def test_simulator_used_when_available():
    p = S.predict_schedule(S.LIBRARY[0].points, use_simulator=True)
    if S.simulator_available():
        assert p["provenance"] in ("simulated", "estimated")
        if p["provenance"] == "estimated":
            assert "simulator_error" in p
    else:
        assert p["provenance"] == "estimated"
