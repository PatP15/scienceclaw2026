"""Acceptance test #10 (software-only): simulated device acks control + motor commands."""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from fermentai.api.testing import TEST_TOKEN, FakeClock, auth, make_app

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "simulate_device.py"


def load_sim():
    spec = importlib.util.spec_from_file_location("simulate_device", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_simulated_device_end_to_end(tmp_path):
    sim = load_sim()
    clock = FakeClock()
    client = TestClient(make_app(tmp_path, clock=clock))
    mono = Mono()
    devs = {c: sim.SimulatedDevice(c, client, TEST_TOKEN, telemetry_interval_s=10, clock=mono)
            for c in ("A", "B")}

    def run(seconds, dt=1.0):
        for _ in range(int(seconds / dt)):
            mono.t += dt
            clock.advance(dt)
            for d in devs.values():
                d.step()

    run(12)
    assert devs["A"].setpoint_c == 22.0 and devs["B"].setpoint_c == 27.0
    ctl = client.get("/api/v1/commands?command_type=control").json()
    assert ctl and all(c["acked"] for c in ctl)
    tel = client.get("/api/v1/chambers/B/state").json()["latest_telemetry"]
    assert tel["source"] == "simulated" and "simulated_device" in tel["quality_flags"]

    # motor via taste feedback path -> both devices run and complete it
    r = client.post("/api/v1/taste-feedback", headers=auth(), json={"text": "A mild, B sour"})
    assert r.status_code == 200
    run(15)
    motors = client.get("/api/v1/commands?command_type=motor").json()
    assert len(motors) == 2
    assert all(m["status"] == "completed" for m in motors), motors
    assert all([a["status"] for a in m["acks"]] == ["accepted", "completed"] for m in motors)

    # cancel mid-run -> device stops and acks cancelled
    cmd = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 10}).json()
    run(6)
    assert devs["A"].motor_until is not None
    client.delete(f"/api/v1/commands/{cmd['command_id']}", headers=auth())
    run(6)
    assert devs["A"].motor_until is None
    log = {m["command_id"]: m for m in client.get("/api/v1/commands?command_type=motor").json()}
    assert log[cmd["command_id"]]["status"] == "cancelled"
    assert "cancelled" in [a["status"] for a in log[cmd["command_id"]]["acks"]]

    # setpoint change propagates
    client.post("/api/v1/chambers/B/setpoint", headers=auth(), json={"setpoint_c": 25.0})
    run(6)
    assert devs["B"].setpoint_c == 25.0
    assert devs["A"].stats["errors"] == 0 and devs["B"].stats["errors"] == 0
