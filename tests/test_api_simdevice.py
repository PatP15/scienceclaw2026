"""Acceptance test #10 (software-only): the simulated reference device acks control + motor commands,
over contract v1.1 (commands list, new ack shape) and v1.0 (flat fields, legacy ack shape)."""

import importlib.util
from pathlib import Path

import pytest
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


@pytest.mark.parametrize("protocol", ["1.1", "1.0"])
def test_simulated_device_end_to_end(tmp_path, protocol):
    sim = load_sim()
    clock = FakeClock()
    client = TestClient(make_app(tmp_path, clock=clock))
    mono = Mono()
    devs = {c: sim.SimulatedDevice(c, client, TEST_TOKEN, telemetry_interval_s=10, clock=mono,
                                   protocol=protocol)
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
    if protocol == "1.1":
        assert tel["timestamp_utc"] is None and tel["timestamp_quality"] == "unsynced"
        assert tel["boot_id"] and tel["sequence_number"] >= 1 and tel["time_basis"] == "server_receipt"

    # motor via taste feedback path -> both devices run and complete it
    r = client.post("/api/v1/taste-feedback", headers=auth(), json={"text": "A mild, B sour"})
    assert r.status_code == 200
    run(15)
    motors = client.get("/api/v1/commands?command_type=motor").json()
    assert len(motors) == 2
    assert all(m["status"] == "completed" and m["ran"] for m in motors), motors
    assert all([a["status"] for a in m["acks"]] == ["accepted", "completed"] for m in motors)
    assert client.get("/api/v1/chambers/A/state").json()["motor_ran"] is True

    # cancel mid-run -> device stops
    cmd = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 10}).json()
    run(6)
    assert devs["A"].motor_until is not None
    client.delete(f"/api/v1/commands/{cmd['command_id']}", headers=auth())
    run(6)
    assert devs["A"].motor_until is None
    log = {m["command_id"]: m for m in client.get("/api/v1/commands?command_type=motor").json()}
    assert log[cmd["command_id"]]["status"] == "cancelled" and log[cmd["command_id"]]["ran"] is False
    if protocol == "1.0":
        assert "cancelled" in [a["status"] for a in log[cmd["command_id"]]["acks"]]
    else:
        stop = client.get("/api/v1/commands?action=stop_motor").json()[0]
        assert stop["status"] == "completed" and stop["payload"]["target_command_id"] == cmd["command_id"]
    # stop directive cleared once the device confirmed
    assert client.get("/api/v1/chambers/A/desired-control", headers=auth()).json()["motor"]["action"] == "none"

    # setpoint change propagates
    client.post("/api/v1/chambers/B/setpoint", headers=auth(), json={"setpoint_c": 25.0})
    run(6)
    assert devs["B"].setpoint_c == 25.0
    assert devs["A"].stats["errors"] == 0 and devs["B"].stats["errors"] == 0


def test_capture_image_command_v11(tmp_path):
    sim = load_sim()
    clock = FakeClock()
    client = TestClient(make_app(tmp_path, clock=clock))
    mono = Mono()
    dev = sim.SimulatedDevice("A", client, TEST_TOKEN, clock=mono)
    cmd = client.post("/api/v1/chambers/A/capture-image", headers=auth()).json()
    assert cmd["action"] == "capture_image"
    for _ in range(6):
        mono.t += 1
        clock.advance(1)
        dev.step()
    row = client.get("/api/v1/commands?action=capture_image").json()[0]
    assert row["status"] == "completed"
    img = client.get("/api/v1/chambers/A/state").json()["latest_image"]
    assert img and img["source"] == "simulated"


def test_telemetry_retry_is_deduplicated(tmp_path):
    sim = load_sim()
    client = TestClient(make_app(tmp_path, clock=FakeClock()))
    dev = sim.SimulatedDevice("B", client, TEST_TOKEN, clock=Mono())
    body = dev.telemetry(0.0)
    first = client.post("/api/v1/telemetry", headers=dev.headers, json=body).json()
    again = client.post("/api/v1/telemetry", headers=dev.headers, json=body).json()
    assert first["duplicate"] is False and again["duplicate"] is True
    assert again["telemetry_id"] == first["telemetry_id"]
    assert len(client.get("/api/v1/chambers/B/telemetry").json()) == 1
