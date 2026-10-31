"""Calibration: recover known parameters from synthetic (simulated) observations."""

import numpy as np
import pandas as pd
import pytest

from fermentai.fermentation.calibration import calibrate, prepare_observations
from fermentai.fermentation.simulation import TemperatureSchedule, simulate

SCH = TemperatureSchedule([(0, 25), (8, 23)])


def _synthetic(true, times, noise_ph=0.01, noise_e=0.01, seed=0):
    truth = simulate(SCH, params=true, t_end_h=max(times), dt_h=0.05)
    rng = np.random.default_rng(seed)
    obs = pd.DataFrame({"time_h": times})
    obs["ph"] = np.interp(times, truth.time_h, truth.ph) + rng.normal(0, noise_ph, len(times))
    obs["expansion_ratio"] = np.interp(times, truth.time_h, truth.expansion_ratio) + rng.normal(
        0, noise_e, len(times)
    )
    obs["temperature_c"] = SCH.temperature_at(times)
    return obs


def test_recovers_single_parameter():
    obs = _synthetic({"beta_lactic": 0.135}, np.arange(0, 14.01, 1.0))
    res = calibrate(obs, SCH, free_params=["beta_lactic"])
    assert res.status == "calibrated", res.message
    assert res.fitted_params["beta_lactic"] == pytest.approx(0.135, rel=0.08)
    assert res.rmse["ph"] < 0.03
    assert res.provenance == "simulated"
    assert set(res.residuals.columns) >= {"time_h", "variable", "observed", "predicted", "residual"}


def test_recovers_two_parameters_with_sparse_missing_data_and_schedule_from_temps():
    obs = _synthetic({"beta_lactic": 0.07, "qs_yeast": 0.8}, np.arange(0, 14.01, 0.5))
    obs.loc[obs.index % 4 != 0, "ph"] = np.nan  # pH only every 2 h (single probe)
    obs.loc[obs.sample(frac=0.2, random_state=1).index, "expansion_ratio"] = np.nan
    res = calibrate(obs, schedule=None, free_params=["beta_lactic", "qs_yeast"])
    assert res.success
    assert res.schedule is not None and res.schedule.mode == "piecewise_linear"
    assert res.fitted_params["beta_lactic"] == pytest.approx(0.07, rel=0.15)
    assert res.fitted_params["qs_yeast"] == pytest.approx(0.8, rel=0.15)


def test_height_in_mm_is_converted():
    obs = _synthetic({"qs_yeast": 0.5}, np.arange(0, 12.01, 1.0))
    obs["height_mm"] = obs.pop("expansion_ratio") * 40.0
    obs["initial_height_mm"] = 40.0
    long, _ = prepare_observations(obs)
    assert "expansion_ratio" in set(long.variable)
    res = calibrate(obs, SCH, free_params=["qs_yeast"])
    assert res.fitted_params["qs_yeast"] == pytest.approx(0.5, rel=0.1)


def test_sparse_and_empty_observations_labelled():
    one = pd.DataFrame({"time_h": [6.0], "ph": [4.6]})
    res = calibrate(one, SCH, free_params=["beta_lactic", "qs_yeast"])
    assert res.status == "under_determined"
    empty = pd.DataFrame({"time_h": [0.0, 3.0], "ph": [np.nan, np.nan]})
    res2 = calibrate(empty, SCH)
    assert res2.status == "uncalibrated" and not res2.success
    assert res2.prediction is not None  # still returns the prior prediction


def test_timestamp_input_and_bounds():
    obs = _synthetic({"beta_lactic": 0.1}, np.arange(0, 10.01, 1.0))
    obs["timestamp_utc"] = pd.Timestamp("2026-10-31T08:00:00Z") + pd.to_timedelta(
        obs.pop("time_h"), unit="h"
    )
    res = calibrate(obs, SCH, free_params=["beta_lactic"], prior_weight=0.1)
    assert 0.05 <= res.fitted_params["beta_lactic"] <= 0.16
    assert res.to_dict()["provenance"] == "simulated"
    with pytest.raises(KeyError):
        calibrate(obs, SCH, free_params=["nope"])
