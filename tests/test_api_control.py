"""Setpoints, safety bounds, desired-control command lifecycle (acceptance test #6)."""

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import FakeClock, auth, make_app, telemetry


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(tmp_path, clock):
    return make_app(tmp_path, clock=clock)


@pytest.fixture
def client(app):
    return TestClient(app)


def svc(app):
    return app.state.service


def dc(client, cid):
    r = client.get(f"/api/v1/chambers/{cid}/desired-control", headers=auth())
    assert r.status_code == 200, r.text
    return r.json()


def test_initial_independent_setpoints(client):
    a, b = dc(client, "A"), dc(client, "B")
    assert a["temperature_setpoint_c"] == 22.0 and b["temperature_setpoint_c"] == 27.0
    assert a["command_id"] != b["command_id"]
    assert a["schema_version"] == "1.0" and a["command_type"] == "control"


def test_setpoints_change_independently(app, client, clock):
    r = client.post("/api/v1/chambers/A/setpoint", headers=auth(),
                    json={"setpoint_c": 24.0, "source": "agent_A", "rationale": "test"})
    assert r.status_code == 200 and r.json()["applied_c"] == 24.0
    assert dc(client, "A")["temperature_setpoint_c"] == 24.0
    assert dc(client, "B")["temperature_setpoint_c"] == 27.0


def test_clamp_and_step_limit(app, clock):
    s = svc(app)
    r = s.set_desired_setpoint("A", 100.0, "llm", "absurd request")
    assert r["clamped"] and r["step_limited"] and not r["rejected"]
    assert r["applied_c"] == 25.0  # 22 + max step 3, even though target clamped to 35
    clock.advance(10)
    r = s.set_desired_setpoint("A", -50, "llm", "")
    assert r["clamped"] and r["applied_c"] == 22.0
    for _ in range(20):  # walk down: never below the lower limit
        clock.advance(10)
        r = s.set_desired_setpoint("A", -50, "llm", "")
    assert r["applied_c"] == 4.0
    # every stored/issued value respects the bounds
    rows = s.db.query("SELECT applied_c FROM setpoint_changes")
    assert all(4.0 <= x["applied_c"] <= 35.0 for x in rows)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "hot", None, True])
def test_non_numeric_rejected(app, bad):
    r = svc(app).set_desired_setpoint("B", bad, "llm", "")
    assert r["rejected"] and not r["changed"] and r["applied_c"] == 27.0


def test_rate_limit(app, clock):
    s = svc(app)
    assert s.set_desired_setpoint("A", 23.0, "agent_A", "")["changed"]
    r = s.set_desired_setpoint("A", 24.0, "agent_A", "")
    assert r["rejected"] and "rate limit" in r["reason"]
    clock.advance(6)
    assert s.set_desired_setpoint("A", 24.0, "agent_A", "")["applied_c"] == 24.0


def test_module_level_function_uses_same_service(app):
    from fermentai.api.devices import set_desired_setpoint
    r = set_desired_setpoint("B", 26.0, "agent_B", "module path")
    assert r["applied_c"] == 26.0
    assert svc(app).current_control("B")["setpoint_c"] == 26.0


def test_command_reuse_reissue_and_expiry(client, clock, app):
    first = dc(client, "A")
    assert first["ttl_s"] == 90
    clock.advance(10)
    again = dc(client, "A")
    assert again["command_id"] == first["command_id"] and again["ttl_s"] == 80
    clock.advance(40)  # 40 s remaining < reissue threshold 45 -> new id, higher seq
    third = dc(client, "A")
    assert third["command_id"] != first["command_id"]
    assert third["sequence_number"] == first["sequence_number"] + 1
    # setpoint change -> new command immediately
    svc(app).set_desired_setpoint("A", 23.0, "agent_A", "")
    fourth = dc(client, "A")
    assert fourth["command_id"] != third["command_id"] and fourth["temperature_setpoint_c"] == 23.0
    # unacked commands are logged as superseded / expired_unacked
    clock.advance(200)
    log = {c["command_id"]: c["status"] for c in client.get("/api/v1/commands?chamber_id=A").json()}
    assert log[first["command_id"]] == "superseded"
    assert log[fourth["command_id"]] == "expired_unacked"


def test_ack_flow(client, clock):
    cmd = dc(client, "B")
    ack = {"schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "B",
           "status": "accepted", "sequence_number": cmd["sequence_number"], "applied_setpoint_c": 27.0}
    r = client.post("/api/v1/control-ack", headers=auth(), json=ack)
    assert r.status_code == 200 and r.json()["command_status"] == "accepted" and not r.json()["late"]
    entry = client.get("/api/v1/commands?chamber_id=B").json()[0]
    assert entry["acked"] and entry["acks"][0]["status"] == "accepted"
    # wrong chamber / unknown id / bad version
    assert client.post("/api/v1/control-ack", headers=auth(), json={**ack, "chamber_id": "A"}).status_code == 409
    assert client.post("/api/v1/control-ack", headers=auth(), json={**ack, "command_id": "nope"}).status_code == 404
    assert client.post("/api/v1/control-ack", headers=auth(), json={**ack, "schema_version": "0.9"}).status_code == 422


def test_late_ack_flagged(client, clock):
    cmd = dc(client, "A")
    clock.advance(120)
    r = client.post("/api/v1/control-ack", headers=auth(), json={
        "schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "A", "status": "accepted"})
    assert r.json()["late"] is True


def test_peltier_hint(client, clock):
    assert dc(client, "A")["peltier_mode_hint"] == "unknown"
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", chamber_temperature_c=18.0))
    assert dc(client, "A")["peltier_mode_hint"] == "heat"
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", chamber_temperature_c=25.0))
    assert dc(client, "A")["peltier_mode_hint"] == "cool"
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", chamber_temperature_c=25.0, door_open=True))
    assert dc(client, "A")["peltier_mode_hint"] == "idle"
    clock.advance(600)
    assert dc(client, "A")["peltier_mode_hint"] == "unknown"  # stale telemetry


def test_config_tightening_is_enforced_at_issue_time(tmp_path):
    clock = FakeClock()
    app = make_app(tmp_path, clock=clock, hw_overrides={"temperature": {"max_setpoint_c": 25.0}})
    c = TestClient(app)
    # chamber B initial 27 is above the tightened max -> clamped everywhere
    assert dc(c, "B")["temperature_setpoint_c"] == 25.0


def test_rejected_setpoint_is_not_reissued_every_poll(client, clock):
    cmd = dc(client, "A")
    client.post("/api/v1/control-ack", headers=auth(), json={
        "command_id": cmd["command_id"], "chamber_id": "A", "status": "rejected", "detail": "peltier disarmed"})
    clock.advance(5)
    again = dc(client, "A")
    assert again["command_id"] == cmd["command_id"] and again["commands"] == []   # no flood
    clock.advance(41)                                                              # < 45 s left -> retry
    retry = dc(client, "A")
    assert retry["command_id"] != cmd["command_id"] and retry["commands"][0]["action"] == "set_setpoint"
