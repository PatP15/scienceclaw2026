"""World, forward model, observation noise and redundancy detection (design benchmark)."""

import numpy as np
import pytest

from fermentai.agents import schedules as S
from fermentai.design.world import (
    FREE_PARAMS,
    HELD_OUT,
    draw_world,
    forward,
    held_out_rmse,
    near_identical,
    observation_times,
    redundancy,
    run_experiment,
)
from fermentai.fermentation import kinetics as K
from fermentai.fermentation.simulation import TemperatureSchedule, simulate

LIB = S.LIBRARY_BY_ID


def test_draw_world_deterministic_and_in_range():
    a, b, c = draw_world(3), draw_world(3), draw_world(4)
    assert a.true_params == b.true_params
    assert a.true_free != c.true_free
    specs = K.parameter_specs()
    for n, v in a.true_free.items():
        assert specs[n].min <= v <= specs[n].max
    # well-specified: everything except the free parameters is nominal
    d = K.default_params()
    assert all(a.true_params[k] == d[k] for k in d if k not in FREE_PARAMS)
    m = draw_world(3, misspecified=True)
    assert any(m.true_params[k] != d[k] for k in d if k not in FREE_PARAMS)


def test_forward_matches_simulate():
    pts = LIB["warm_then_cool_27_19"].points
    times = observation_times(pts)
    out = forward(pts, K.default_params(), times)
    df = simulate(TemperatureSchedule(list(pts), mode="piecewise_linear"), t_end_h=pts[-1][0], dt_h=0.5)
    for var in ("ph", "expansion_ratio"):
        ref = np.interp(times[var], df.time_h, df[var])
        assert np.allclose(out[var], ref, atol=2e-3), var


def test_observations_deterministic_and_common_random_numbers():
    w = draw_world(1)
    pts = LIB["steady_22_14h"].points
    e1 = run_experiment(w, "steady_22_14h", pts, replicate=0)
    e2 = run_experiment(w, "steady_22_14h", pts, replicate=0)
    e3 = run_experiment(w, "steady_22_14h", pts, replicate=1)
    assert e1["observed"] == e2["observed"]  # same world/schedule/replicate -> identical data, any policy
    assert e1["observed"] != e3["observed"]  # a replicate gets fresh noise
    assert e1["provenance"] == "simulated"
    assert e1["times"]["ph"][-1] == pytest.approx(14.0)


def test_held_out_rmse_zero_at_truth_and_not_in_library():
    w = draw_world(2)
    r = held_out_rmse(w.true_params, w.true_params)
    assert r["ph"] == pytest.approx(0.0, abs=1e-12) and r["expansion_ratio"] == pytest.approx(0.0, abs=1e-12)
    assert held_out_rmse(K.default_params(), w.true_params)["ph"] > 0
    for pts in HELD_OUT.values():
        assert not S.validate_schedule(pts)
        assert all(not near_identical(pts, c.points) for c in S.LIBRARY)


def test_redundancy_detection():
    p = {k: LIB[k].points for k in ("steady_22_14h", "steady_26_10h", "steady_27_10h", "steady_18_16h")}
    assert near_identical(p["steady_26_10h"], p["steady_27_10h"])
    assert not near_identical(p["steady_22_14h"], p["steady_18_16h"])
    # same schedule in both chambers
    assert redundancy([("steady_22_14h", p["steady_22_14h"])] * 2, []) == {"within_round": 1, "repeats": 0,
                                                                           "redundant": 1}
    # near-identical pair within a round
    r = redundancy([("steady_26_10h", p["steady_26_10h"]), ("steady_27_10h", p["steady_27_10h"])], [])
    assert r["within_round"] == 1
    # distinct, but one repeats history; a duplicate of both kinds counts once in 'redundant'
    hist = [("steady_18_16h", p["steady_18_16h"])]
    assert redundancy([("steady_22_14h", p["steady_22_14h"]), ("steady_18_16h", p["steady_18_16h"])], hist) == {
        "within_round": 0, "repeats": 1, "redundant": 1}
    assert redundancy([("steady_18_16h", p["steady_18_16h"])] * 2, hist) == {"within_round": 1, "repeats": 2,
                                                                             "redundant": 2}
    assert redundancy([("steady_22_14h", p["steady_22_14h"]), ("steady_18_16h", p["steady_18_16h"])], []) == {
        "within_round": 0, "repeats": 0, "redundant": 0}
