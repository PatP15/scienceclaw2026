"""Temperature-schedule interpolation and time-varying simulation."""

import numpy as np
import pytest

from fermentai.fermentation.simulation import TemperatureSchedule, simulate


def test_piecewise_constant():
    s = TemperatureSchedule([(0, 27), (6, 22), (10, 24)])
    assert s.mode == "piecewise_constant"
    assert s.temperature_at(0) == 27
    assert s.temperature_at(5.999) == 27
    assert s.temperature_at(6) == 22
    assert s.temperature_at(9.5) == 22
    assert s.temperature_at(30) == 24
    assert s.temperature_at(-1) == 27
    np.testing.assert_allclose(s.temperature_at([0, 6, 12]), [27, 22, 24])


def test_piecewise_linear():
    s = TemperatureSchedule([(0, 20), (4, 28), (8, 24)], mode="piecewise_linear")
    assert s.temperature_at(2) == pytest.approx(24)
    assert s.temperature_at(6) == pytest.approx(26)
    assert s.temperature_at(100) == pytest.approx(24)
    assert s.temperature_at(-5) == pytest.approx(20)


def test_validation_and_roundtrip():
    with pytest.raises(ValueError):
        TemperatureSchedule([])
    with pytest.raises(ValueError):
        TemperatureSchedule([(0, 20), (0, 22)])
    with pytest.raises(ValueError):
        TemperatureSchedule([(0, 200)])
    with pytest.raises(ValueError):
        TemperatureSchedule([(0, 20)], mode="cubic")  # type: ignore[arg-type]
    s = TemperatureSchedule([(0, 27), (8, 22)], mode="piecewise_linear")
    s2 = TemperatureSchedule.from_dict(s.to_dict())
    assert s2 == s


def test_simulate_follows_schedule_and_accepts_dict():
    s = TemperatureSchedule([(0, 27), (6, 20)])
    df = simulate(s, t_end_h=12, dt_h=0.1)
    assert df.loc[df.time_h == 3.0, "temperature_c"].item() == 27
    assert df.loc[df.time_h == 9.0, "temperature_c"].item() == 20
    df2 = simulate(s.to_dict(), t_end_h=12, dt_h=0.1)  # type: ignore[arg-type]
    np.testing.assert_allclose(df.ph, df2.ph)


def test_cooling_step_slows_acidification():
    hot = simulate(TemperatureSchedule.constant(27), t_end_h=14, dt_h=0.1)
    step = simulate(TemperatureSchedule([(0, 27), (6, 20)]), t_end_h=14, dt_h=0.1)
    i6 = int(np.argmin(abs(hot.time_h - 6)))
    np.testing.assert_allclose(
        hot.ph[: i6 + 1], step.ph[: i6 + 1], atol=1e-6
    )  # identical before step
    assert step.ph.iloc[-1] > hot.ph.iloc[-1]
    rate_hot = hot.lactic_acid.iloc[-1] - hot.lactic_acid[i6]
    rate_step = step.lactic_acid.iloc[-1] - step.lactic_acid[i6]
    assert rate_step < rate_hot


def test_linear_ramp_between_constants():
    lo = simulate(TemperatureSchedule.constant(22), t_end_h=12, dt_h=0.1)
    hi = simulate(TemperatureSchedule.constant(27), t_end_h=12, dt_h=0.1)
    ramp = simulate(
        TemperatureSchedule([(0, 22), (12, 27)], mode="piecewise_linear"), t_end_h=12, dt_h=0.1
    )
    assert lo.lactic_acid.iloc[-1] < ramp.lactic_acid.iloc[-1] < hi.lactic_acid.iloc[-1]


def test_horizon_24h_and_deterministic():
    s = TemperatureSchedule.constant(24)
    d1 = simulate(s, t_end_h=24, dt_h=0.25)
    d2 = simulate(s, t_end_h=24, dt_h=0.25)
    assert d1.time_h.iloc[-1] == pytest.approx(24)
    np.testing.assert_array_equal(
        d1.select_dtypes("number").to_numpy(), d2.select_dtypes("number").to_numpy()
    )


def test_cooling_step_not_flagged_as_overproof():
    # gas contracts when cooled; that alone must not trigger the overproof signal
    df = simulate(TemperatureSchedule([(0, 27), (4, 18)]), t_end_h=8, dt_h=0.05)
    i = int(np.argmin(abs(df.time_h - 4.5)))
    assert df.overproof_risk[i] < 0.5
