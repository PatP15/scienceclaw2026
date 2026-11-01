import csv
import math

import numpy as np
import pytest

from fermentai.control import tuning as T


def synth(K=18.0, tau=500.0, theta=40.0, u_level=0.3, n=900, dt=3.0, noise=0.03, seed=1):
    t = np.arange(n) * dt
    u = np.full(n, u_level)
    y = T.simulate_fopdt(t, u, K, tau, theta, 22.0, 0.0)
    return t, y + np.random.default_rng(seed).normal(0, noise, n), u


def test_fit_recovers_known_parameters_open_loop():
    t, y, u = synth()
    fit = T.fit_fopdt(t, y, u)
    assert fit.K == pytest.approx(18.0, rel=0.05)
    assert fit.tau == pytest.approx(500.0, rel=0.1)
    assert fit.theta == pytest.approx(40.0, abs=15)


def test_fit_with_varying_output():
    t = np.arange(1000) * 3.0
    u = np.where(t < 300, 0.0, np.where(t < 1500, 0.3, 0.15))
    y = T.simulate_fopdt(t, u, 12.0, 400.0, 30.0, 22.0, 0.0) + np.random.default_rng(2).normal(0, 0.03, len(t))
    fit = T.fit_fopdt(t, y, u)
    assert fit.K == pytest.approx(12.0, rel=0.08) and fit.tau == pytest.approx(400.0, rel=0.15)


def test_simc_formulas():
    K, tau, th = 10.0, 300.0, 20.0
    pi = T.simc_pi(K, tau, th)
    assert pi.kp == pytest.approx(tau / (K * 2 * th))
    assert pi.tau_i == pytest.approx(min(tau, 4 * 2 * th))
    assert pi.ki == pytest.approx(pi.kp / pi.tau_i) and pi.kd == 0
    pid = T.simc_pid(K, tau, th)
    assert pid.kp == pytest.approx((tau + th / 2) / (K * (th + th / 2)))
    assert pid.tau_d == pytest.approx(tau * th / (2 * tau + th))
    assert pid.kd == pytest.approx(pid.kp * pid.tau_d)
    rec = T.recommend(T.FopdtFit(K, tau, th, 22, 0, 0, 10, "x"), cap=0.3)
    assert rec["pi_conservative"].kp < rec["pi"].kp
    assert rec["pid"].anti_windup["output_max"] == 0.3 and rec["pid"].anti_windup["integral_state_limit"] == pytest.approx(0.3 / rec["pid"].ki)


def test_closed_loop_respects_cap_and_settles():
    K, tau, th = 15.0, 600.0, 30.0
    g = T.simc_pid(K, tau, th, tau_c=3 * th)
    r = T.simulate_closed_loop(K, tau, th, g, setpoint=26.0, y0=22.0, dt=1.0, t_end=7200, cap=0.3)
    assert np.max(np.abs(r["u"])) <= 0.3 + 1e-12
    assert abs(r["y"][-1] - 26.0) < 0.2
    assert T.step_metrics(r["t"], r["y"], 26.0, 22.0)["overshoot_pct"] < 30


def test_write_gains_and_csv_roundtrip(tmp_path):
    t, y, u = synth(n=200)
    p = tmp_path / "step_A_x.csv"
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=T.CSV_FIELDS); w.writeheader()
        for a, b, c in zip(t, y, u):
            w.writerow({"t_s": a, "phase": "step", "chamber_temp_c": b, "output_fraction": c, "peltier_mode": "heat", "output_signed": c})
    d = T.read_step_csv(p)
    fit = T.fit_fopdt(*T.select_segment(d))
    doc = T.write_gains_json(tmp_path / "pid.json", "A", fit, T.recommend(fit), source_csv=str(p), cap=0.3)
    assert doc["recommended"]["pid"]["kp"] > 0 and (tmp_path / "pid.json").exists()


# ---------------------------------------------------------------- run / abort logic with a stub API
class StubApi:
    def __init__(self, plant_gain=0.3, armed=True, fault_at=None, temp_jump=None, max_step=3.0):
        self.now = 0.0; self.sp = 22.0; self.T = 22.0; self.seq = 0; self.armed = armed
        self.fault_at = fault_at; self.temp_jump = temp_jump; self.max_step = max_step; self.sets = []

    def tick(self):
        self.now += 1.0; self.seq += 1
        err = self.sp - self.T
        out = min(0.3, 0.25 * abs(err)) if self.armed else 0.0
        self.T += (0.05 * out * np.sign(err) * 20) if self.armed else 0.0
        if self.temp_jump and self.now > self.temp_jump[0]:
            self.T = self.temp_jump[1]
        self._out = out

    def state(self, chamber):
        flags = ["fault_overtemp"] if self.fault_at is not None and self.now > self.fault_at else []
        return {"desired_setpoint_c": self.sp, "telemetry_stale": False, "latest_telemetry": {
            "id": self.seq, "sequence_number": self.seq, "chamber_temperature_c": round(self.T, 2), "starter_temperature_c": self.T - 1,
            "temperature_setpoint_c": self.sp, "peltier_mode": ("heat" if self.sp > self.T else "cool") if self.armed else "disarmed",
            "peltier_output_fraction": getattr(self, "_out", 0.0), "quality_flags": flags, "source": "measured"}}

    def set_setpoint(self, chamber, c):
        applied = self.sp + max(-self.max_step, min(self.max_step, c - self.sp))
        self.sp = applied; self.sets.append(c)
        return {"applied_c": applied, "requested_c": c, "rejected": False, "reason": ""}


def run_test(api, tmp_path, **kw):
    cfg = T.StepConfig(chamber="A", t_from=22, t_to=26, duration_min=3, settle_min=2, settle_hold_s=10, arm_check_s=20, out_dir=str(tmp_path), **kw)
    clock = lambda: api.now  # noqa: E731
    def sleep(s):
        for _ in range(int(max(1, round(s)))):
            api.tick()
    return T.StepTest(api, cfg, clock=clock, sleep=sleep, log=lambda *_: None).run()


def test_run_completes_logs_and_restores(tmp_path):
    api = StubApi(); api.sp = 21.0
    res = run_test(api, tmp_path)
    assert res.status == "completed" and res.restored and api.sp == 21.0
    rows = list(csv.DictReader(open(res.csv_path)))
    assert len(rows) > 50 and {"settle", "step"} <= {r["phase"] for r in rows}
    assert 26 in api.sets and max(api.sets[:-1]) == 26   # +4 step was split by the 3 C API limit


def test_refuses_when_peltier_disarmed(tmp_path):
    res = run_test(StubApi(armed=False), tmp_path)
    assert res.status == "aborted" and "disarmed" in res.reason and res.restored


def test_aborts_on_fault_flag_and_restores(tmp_path):
    api = StubApi(fault_at=15)
    res = run_test(api, tmp_path)
    assert res.status == "aborted" and "fault" in res.reason and api.sp == 22.0 and res.restored


def test_aborts_when_temperature_leaves_window(tmp_path):
    api = StubApi(temp_jump=(10, 40.0))
    res = run_test(api, tmp_path)
    assert res.status == "aborted" and "left the allowed window" in res.reason


def test_ctrl_c_restores(tmp_path):
    api = StubApi()
    n = {"c": 0}
    def sleep(s):
        n["c"] += 1
        if n["c"] == 40:
            raise KeyboardInterrupt
        api.tick()
    cfg = T.StepConfig(chamber="A", t_from=22, t_to=26, out_dir=str(tmp_path), settle_hold_s=1)
    res = T.StepTest(api, cfg, clock=lambda: api.now, sleep=sleep, log=lambda *_: None).run()
    assert res.status == "aborted" and "Ctrl-C" in res.reason and res.restored


def test_plan_text_mentions_limits():
    txt = T.plan_text(T.StepConfig("A", 22, 26))
    assert "[16, 32]" in txt and "DRY RUN" in txt
