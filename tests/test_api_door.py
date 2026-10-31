"""Door -> hat rule: open -> bounded wiggle; interlock rejection -> one deferred wiggle on close; cooldown."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import FakeClock, auth, make_app


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(tmp_path, clock):
    return TestClient(make_app(tmp_path, clock=clock))


class Seq:
    n = 0


def tel(client, door, chamber="A", legacy=False, opens=None):
    Seq.n += 1
    body = {"schema_version": "1.0", "chamber_id": chamber, "device_id": f"fermentai-esp32-{chamber.lower()}",
            "boot_id": "b1", "sequence_number": Seq.n, "chamber_temperature_c": 22.0, "door_open": door,
            "source": "measured", "quality_flags": ["legacy_status_log"] if legacy else []}
    if opens is not None:
        body["diagnostics"] = {"door_opens": opens}
    r = client.post("/api/v1/telemetry", headers=auth(), json=body)
    assert r.status_code == 200, r.text
    return r


def door_cmds(client, chamber="A"):
    rows = client.get(f"/api/v1/commands?chamber_id={chamber}&command_type=motor", headers=auth()).json()
    return [r for r in rows if r["source"] == "door_event"]


def door_events(client):
    from fermentai.api.devices import ControlService  # noqa: F401
    svc = client.app.state.service
    return [e["data"] if "data" in e else e for e in svc.db.events_since(0, 500, ["door"])]


def reject(client, cmd, msg="ERR door open"):
    r = client.post("/api/v1/control-ack", headers=auth(), json={
        "schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "A",
        "status": "rejected", "detail": msg})
    assert r.status_code == 200, r.text


def test_open_issues_bounded_wiggle_and_emits_door_event(client):
    tel(client, False, opens=0)
    assert door_cmds(client) == []
    tel(client, True, opens=1)
    cmds = door_cmds(client)
    assert len(cmds) == 1 and cmds[0]["payload"]["duration_s"] == 3.0 and cmds[0]["payload"]["pattern"] == "wiggle"
    tel(client, True, opens=1)                       # no transition: nothing new
    assert len(door_cmds(client)) == 1
    evs = door_events(client)
    assert [(e["payload"]["open"] if "payload" in e else e["open"]) for e in evs] == [True]


def test_rejection_with_door_open_defers_one_wiggle_to_close(client, clock):
    tel(client, False)
    tel(client, True)
    (c,) = door_cmds(client)
    reject(client, c)
    assert len(door_cmds(client)) == 1               # nothing re-issued while open
    clock.advance(1)                                  # rejected command did not use the cooldown
    tel(client, False)
    cmds = door_cmds(client)
    assert len(cmds) == 2
    tel(client, False)
    assert len(door_cmds(client)) == 2               # only one deferred wiggle


def test_other_rejection_is_not_deferred(client):
    tel(client, False)
    tel(client, True)
    (c,) = door_cmds(client)
    reject(client, c, "ERR motor not armed (commissioning)")
    tel(client, False)
    assert len(door_cmds(client)) == 1


def test_legacy_device_fires_on_open(client):
    # firmware no longer refuses the motor with the door open: legacy devices fire on open too
    tel(client, False, legacy=True)
    tel(client, True, legacy=True, opens=1)
    assert len(door_cmds(client)) == 1


def test_cooldown(client, clock):
    tel(client, False)
    tel(client, True)
    assert len(door_cmds(client)) == 1
    clock.advance(3)
    tel(client, False)
    tel(client, True)                                 # within 10 s of the last wiggle
    assert len(door_cmds(client)) == 1
    clock.advance(10)
    tel(client, False)
    tel(client, True)
    assert len(door_cmds(client)) == 2


def test_disabled_and_per_chamber(tmp_path, clock):
    c = TestClient(make_app(tmp_path, clock=clock, hw_overrides={"door_motor": {"enabled": False}}))
    tel(c, False)
    tel(c, True)
    assert door_cmds(c) == []
    assert len(door_events(c)) == 1                   # SSE door event still emitted
