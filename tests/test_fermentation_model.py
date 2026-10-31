"""Behavioural sanity tests for the sourdough kinetic model (all outputs simulated)."""

import numpy as np
import pytest

from fermentai.fermentation import kinetics as K
from fermentai.fermentation.simulation import (
    OUTPUT_COLUMNS,
    TemperatureSchedule,
    load_scenario,
    simulate,
    simulate_with_uncertainty,
    summarize_prediction,
)


@pytest.fixture(scope="module")
def runs():
    a = simulate(TemperatureSchedule.constant(22.0), t_end_h=24.0, dt_h=0.05)
    b = simulate(TemperatureSchedule.constant(27.0), t_end_h=24.0, dt_h=0.05)
    return a, b


def test_columns_and_provenance(runs):
    a, _ = runs
    for c in OUTPUT_COLUMNS:
        assert c in a.columns
    assert a.attrs["provenance"] == "simulated"
    assert (a["provenance"] == "simulated").all()
    assert a.time_h.iloc[0] == 0.0 and a.time_h.iloc[-1] == pytest.approx(24.0)


def test_nothing_negative(runs):
    for df in runs:
        num = df.select_dtypes("number").drop(columns=["fermentation_quotient"])
        assert (num.to_numpy() >= -1e-9).all()
        assert np.isfinite(num.to_numpy()).all()


def test_warmer_acidifies_faster(runs):
    a, b = runs
    for t in (4.0, 8.0, 12.0, 16.0):
        ia, ib = (int(np.argmin(abs(d.time_h - t))) for d in (a, b))
        assert b.lactic_acid[ib] > a.lactic_acid[ia]
        assert b.ph[ib] < a.ph[ia]
    # pH falls faster early on at 27 degC
    assert (b.ph.iloc[0] - np.interp(6, b.time_h, b.ph)) > (
        a.ph.iloc[0] - np.interp(6, a.time_h, a.ph)
    )


def test_ph_plausible_range(runs):
    for df in runs:
        assert 5.0 <= df.ph.iloc[0] <= 5.8  # dough just inoculated with ripe starter
        assert df.ph.min() > 3.2
        ph16 = np.interp(16, df.time_h, df.ph)
        assert 3.5 <= ph16 <= 4.3
        # pH is monotonically non-increasing (acids only accumulate)
        assert (np.diff(df.ph.to_numpy()) <= 1e-6).all()


def test_acids_and_co2_monotonic(runs):
    for df in runs:
        for c in ("lactic_acid", "acetic_acid", "co2_cumulative"):
            assert (np.diff(df[c].to_numpy()) >= -1e-9).all(), c
        assert (np.diff(df.substrate.to_numpy()) <= 1e-9).all()


def test_expansion_peaks_then_declines(runs):
    for df in runs:
        e = df.expansion_ratio.to_numpy()
        i = int(np.argmax(e))
        assert 1.5 < e[i] < 3.5
        assert 0 < i < len(e) - 1, "peak should be interior, not at the end"
        assert e[-1] < e[i] - 0.05, "expansion should decline/plateau after the peak"
        assert e.min() >= 1.0 - 1e-9


def test_warmer_peaks_earlier_and_flags_overproof(runs):
    a, b = runs
    sa, sb = summarize_prediction(a), summarize_prediction(b)
    assert sb["peak_expansion_time_h"] < sa["peak_expansion_time_h"]
    assert sb["overproof_onset_h"] is not None and sb["overproof_onset_h"] < 16.0
    assert sb["overproof_onset_h"] < (sa["overproof_onset_h"] or 1e9)


def test_ph_mapping_properties():
    p = K.resolve_params()
    assert K.ph_from_acids(0, 0, p) == pytest.approx(p["ph_flour"])
    phs = [K.ph_from_acids(la, la / 5, p) for la in (0.5, 2, 4, 8, 12, 20)]
    assert all(x > y for x, y in zip(phs, phs[1:]))
    assert phs[-1] > 2.8  # buffering prevents absurdly low pH
    # buffering: more buffer -> higher pH for the same acid
    p2 = dict(p, buffer_b1=p["buffer_b1"] * 2)
    assert K.ph_from_acids(6, 1, p2) > K.ph_from_acids(6, 1, p)
    # titratable acidity grows with acid while pH falls (distinct quantities)
    assert K.titratable_acidity(8, 1.5, K.ph_from_acids(8, 1.5, p), p) > K.titratable_acidity(
        3, 0.5, K.ph_from_acids(3, 0.5, p), p
    )


def test_ctmi():
    assert K.ctmi(32, 8, 32, 39) == pytest.approx(1.0)
    assert K.ctmi(8, 8, 32, 39) == 0.0 and K.ctmi(39.5, 8, 32, 39) == 0.0
    assert 0 < K.ctmi(22, 8, 32, 39) < K.ctmi(27, 8, 32, 39) < 1


def test_config_has_metadata():
    specs = K.parameter_specs()
    assert len(specs) > 30
    for name, s in specs.items():
        assert s.units, name
        assert s.reference, name
        assert s.min <= s.value <= s.max, name


def test_cold_slows_everything():
    cold = simulate(TemperatureSchedule.constant(10.0), t_end_h=12.0, dt_h=0.1)
    warm = simulate(TemperatureSchedule.constant(24.0), t_end_h=12.0, dt_h=0.1)
    assert cold.co2_cumulative.iloc[-1] < 0.5 * warm.co2_cumulative.iloc[-1]
    assert cold.ph.iloc[-1] > warm.ph.iloc[-1]


def test_param_override_and_unknown():
    base = simulate(TemperatureSchedule.constant(24.0), t_end_h=8.0, dt_h=0.1)
    fast = simulate(
        TemperatureSchedule.constant(24.0), params={"beta_lactic": 0.15}, t_end_h=8.0, dt_h=0.1
    )
    assert fast.lactic_acid.iloc[-1] > base.lactic_acid.iloc[-1]
    with pytest.raises(KeyError):
        simulate(TemperatureSchedule.constant(24.0), params={"not_a_param": 1.0}, t_end_h=1.0)
    with pytest.raises(KeyError):
        simulate(TemperatureSchedule.constant(24.0), initial={"bogus": 1.0}, t_end_h=1.0)


def test_initial_override():
    df = simulate(
        TemperatureSchedule.constant(24.0), initial={"lactic_acid": 3.0}, t_end_h=1.0, dt_h=0.1
    )
    assert df.lactic_acid.iloc[0] == pytest.approx(3.0)


def test_uncertainty_envelopes_deterministic():
    sch = TemperatureSchedule.constant(27.0)
    r1 = simulate_with_uncertainty(sch, n_samples=12, seed=3, t_end_h=12.0)
    r2 = simulate_with_uncertainty(sch, n_samples=12, seed=3, t_end_h=12.0)
    r3 = simulate_with_uncertainty(sch, n_samples=12, seed=4, t_end_h=12.0)
    assert r1["provenance"] == "simulated"
    for v in ("ph", "expansion_ratio", "lactic_acid", "co2_cumulative"):
        o = r1["outputs"][v]
        assert set(o) == {"p05", "median", "p95"}
        assert (o["p05"] <= o["median"] + 1e-12).all() and (o["median"] <= o["p95"] + 1e-12).all()
        np.testing.assert_array_equal(o["median"], r2["outputs"][v]["median"])
    assert not np.array_equal(r1["outputs"]["ph"]["median"], r3["outputs"]["ph"]["median"])
    assert r1["n_failed"] == 0
    # sampled parameters respect configured ranges
    specs = K.parameter_specs()
    for n, col in r1["param_samples"].items():
        assert col.min() >= specs[n].min - 1e-12 and col.max() <= specs[n].max + 1e-12, n


def test_scenarios_load():
    a, b = load_scenario("A"), load_scenario("b")
    assert a.schedule.temperature_at(5) == 22.0 and b.schedule.temperature_at(5) == 27.0
    assert a.window_h == (12.0, 16.0)
    assert 12.0 <= a.horizon_h <= 24.0
