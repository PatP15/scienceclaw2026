"""Chef-hat motor commands: bounded, cancellable, cannot touch setpoints (acceptance test #10)."""

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import FakeClock, auth, make_app


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(tmp_path, clock):
    return make_app(tmp_path, clock=clock)


@pytest.fixture
def client(app):
    return TestClient(app)


def motor_of(client, cid="A"):
    return client.get(f"/api/v1/chambers/{cid}/desired-control", headers=auth()).json()["motor"]


def test_motor_bounded(client):
    assert client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 60}).status_code == 422
    assert client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 0}).status_code == 422
    assert client.post("/api/v1/chambers/A/motor", headers=auth(), json={"pattern": "laser"}).status_code == 422
    r = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 10})
    assert r.status_code == 200 and r.json()["duration_s"] == 10


@pytest.mark.parametrize("field", ["temperature_setpoint_c", "setpoint_c", "peltier_mode", "peltier_mode_hint"])
def test_motor_cannot_carry_setpoint_fields(client, field):
    before = client.get("/api/v1/chambers/A/state").json()["desired_setpoint_c"]
    r = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 3, field: 30})
    assert r.status_code == 422
    assert client.get("/api/v1/chambers/A/state").json()["desired_setpoint_c"] == before


def test_motor_does_not_change_setpoint_or_control_command(client):
    first = client.get("/api/v1/chambers/B/desired-control", headers=auth()).json()
    client.post("/api/v1/chambers/B/motor", headers=auth(), json={"duration_s": 4})
    after = client.get("/api/v1/chambers/B/desired-control", headers=auth()).json()
    assert after["command_id"] == first["command_id"]  # control command untouched
    assert after["temperature_setpoint_c"] == first["temperature_setpoint_c"]
    assert after["motor"]["action"] == "run" and after["motor"]["duration_s"] == 4


def test_motor_ack_lifecycle(client):
    cmd = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 5}).json()
    m = motor_of(client)
    assert m["command_id"] == cmd["command_id"] and m["run"] is True
    ack = {"schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "A"}
    client.post("/api/v1/control-ack", headers=auth(), json={**ack, "status": "accepted"})
    assert motor_of(client)["action"] == "none"  # not re-run
    client.post("/api/v1/control-ack", headers=auth(), json={**ack, "status": "completed"})
    log = client.get("/api/v1/commands?command_type=motor").json()[0]
    assert log["status"] == "completed" and [a["status"] for a in log["acks"]] == ["accepted", "completed"]


def test_motor_cancel(client, clock):
    cmd = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 8}).json()
    r = client.delete(f"/api/v1/commands/{cmd['command_id']}", headers=auth())
    assert r.status_code == 200 and r.json()["cancelled"] is True
    m = motor_of(client)
    assert m["action"] == "stop" and m["run"] is False and m["command_id"] == cmd["command_id"]
    client.post("/api/v1/control-ack", headers=auth(), json={
        "schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "A", "status": "cancelled"})
    assert motor_of(client)["action"] == "none"
    assert client.delete(f"/api/v1/commands/{cmd['command_id']}").status_code == 401
    assert client.delete("/api/v1/commands/unknown", headers=auth()).status_code == 404


def test_cannot_cancel_control_command(client):
    cid = client.get("/api/v1/chambers/A/desired-control", headers=auth()).json()["command_id"]
    assert client.delete(f"/api/v1/commands/{cid}", headers=auth()).status_code == 409


def test_motor_expires_unpicked(client, clock):
    cmd = client.post("/api/v1/chambers/B/motor", headers=auth(), json={"duration_s": 2}).json()
    clock.advance(61)
    assert motor_of(client, "B")["action"] == "none"
    log = {c["command_id"]: c["status"] for c in client.get("/api/v1/commands?command_type=motor").json()}
    assert log[cmd["command_id"]] == "expired_unacked"
