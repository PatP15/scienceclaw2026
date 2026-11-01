"""Contract revision 1.1: telemetry clock/dedup/provenance, device auth + isolation, commands list,
v1.1 acks, motor `ran` semantics, size limits, new endpoints, DB migration."""

import json
import sqlite3
import sys
import types

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from fermentai.api.contract import PLAN_MASTER_TELEMETRY
from fermentai.api.db import Database
from fermentai.api.schemas import Command, DesiredControl, SimulationResult
from fermentai.api.testing import FakeClock, auth, make_app, telemetry

TOK_A, TOK_B = "dev-token-a", "dev-token-b"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(tmp_path, clock):
    return make_app(tmp_path, clock=clock, device_tokens={"A": TOK_A, "B": TOK_B})


@pytest.fixture
def client(app):
    return TestClient(app)


def dev(token, device_id=None):
    h = {"Authorization": f"Bearer {token}"}
    if device_id:
        h["X-Device-Id"] = device_id
    return h


# ----------------------------------------------------------------------------- telemetry
def test_plan_master_telemetry_fixture_ingests(client):
    r = client.post("/api/v1/telemetry", headers=dev(TOK_A, "fermentai-esp32-a"), json=PLAN_MASTER_TELEMETRY)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False and "timestamp_unavailable" in r.json()["quality_flags"]
    tel = client.get("/api/v1/chambers/A/state").json()["latest_telemetry"]
    assert tel["timestamp_utc"] is None                      # stays null
    assert tel["received_at_utc"] == "2026-10-31T12:00:00Z"   # server receipt always stored
    assert tel["boot_id"] == "boot_a001" and tel["sequence_number"] == 120
    assert tel["device_uptime_ms"] == 120000 and tel["timestamp_quality"] == "unsynced"
    assert tel["peltier_mode"] == "heat"                     # "heating" normalised for the column
    assert tel["payload"]["peltier_mode"] == "heating"       # raw kept
    assert tel["time_basis"] == "server_receipt"


def test_dedup_on_device_boot_sequence(client):
    body = {**PLAN_MASTER_TELEMETRY}
    first = client.post("/api/v1/telemetry", headers=dev(TOK_A), json=body).json()
    dup = client.post("/api/v1/telemetry", headers=dev(TOK_A), json=body).json()
    assert dup["duplicate"] is True and dup["telemetry_id"] == first["telemetry_id"]
    nxt = client.post("/api/v1/telemetry", headers=dev(TOK_A), json={**body, "sequence_number": 121}).json()
    reboot = client.post("/api/v1/telemetry", headers=dev(TOK_A),
                         json={**body, "boot_id": "boot_a002", "sequence_number": 1}).json()
    assert not nxt["duplicate"] and not reboot["duplicate"]
    assert len(client.get("/api/v1/chambers/A/telemetry").json()) == 3
    assert any(e["data"].get("event") == "rebooted" for e in client.get("/api/v1/events?types=device").json())


def test_measured_alias_and_field_provenance_readings(client):
    body = {**PLAN_MASTER_TELEMETRY, "source": "real", "ph": 4.6, "ph_method": "manual",
            "field_provenance": {"ph": "manual"},
            "diagnostics": {"sensor_health": {"bme680": "ok", "ph": "uncalibrated"}, "free_heap_bytes": 1,
                            "reset_reason": "poweron"}}
    assert client.post("/api/v1/telemetry", headers=dev(TOK_A), json=body).status_code == 200
    st = client.get("/api/v1/chambers/A/state").json()
    assert st["provenance"] == "measured"
    assert st["latest_telemetry"]["payload"]["source_as_sent"] == "real"
    rd = {r["field"]: r for r in client.get("/api/v1/chambers/A/readings").json()}
    assert rd["ph"]["provenance"] == "manual" and rd["ph"]["quality"] == "uncalibrated"
    assert rd["chamber_temperature_c"]["provenance"] == "measured" and rd["chamber_temperature_c"]["unit"] == "degC"
    assert rd["chamber_temperature_c"]["quality"] == "ok" and rd["pressure_pa"]["unit"] == "Pa"
    assert rd["external_heatsink_temperature_c"]["value"] == 37.1
    assert "starter_height_mm" not in rd                      # null -> no reading row
    assert [r["field"] for r in client.get("/api/v1/chambers/A/readings?field=ph").json()] == ["ph"]


def test_peltier_output_fraction_bounded(client):
    r = client.post("/api/v1/telemetry", headers=dev(TOK_A), json={**PLAN_MASTER_TELEMETRY,
                                                                    "peltier_output_fraction": 1.2})
    assert r.status_code == 422


# ----------------------------------------------------------------------------- auth / isolation
def test_device_token_bound_to_chamber(client):
    assert client.post("/api/v1/telemetry", headers=dev(TOK_A), json=telemetry("A")).status_code == 200
    r = client.post("/api/v1/telemetry", headers=dev(TOK_A), json=telemetry("B"))
    assert r.status_code == 403 and "chamber A" in r.text
    assert client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_A)).status_code == 403
    assert client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_B)).status_code == 200
    # device tokens cannot use operator endpoints
    assert client.post("/api/v1/chambers/A/motor", headers=dev(TOK_A), json={}).status_code == 403
    assert client.post("/api/v1/chambers/A/setpoint", headers=dev(TOK_A), json={"setpoint_c": 30}).status_code == 403
    assert client.post("/api/v1/taste-feedback", headers=dev(TOK_A), json={"text": "x"}).status_code == 403
    # operator token may still act as any device (Mac-local simulators)
    assert client.post("/api/v1/telemetry", headers=auth(), json=telemetry("B")).status_code == 200


def test_device_id_binding(client):
    # registered device for A cannot write B, even with the operator token
    r = client.post("/api/v1/telemetry", headers=auth(), json={**telemetry("B"), "device_id": "fermentai-esp32-a"})
    assert r.status_code == 403
    r = client.post("/api/v1/telemetry", headers=dev(TOK_B, "fermentai-esp32-a"), json=telemetry("B"))
    assert r.status_code == 403
    # header/body mismatch
    r = client.post("/api/v1/telemetry", headers=dev(TOK_A, "fermentai-esp32-a"),
                    json={**telemetry("A"), "device_id": "other"})
    assert r.status_code == 403
    # unregistered id auto-binds to its first chamber, then is locked to it
    assert client.post("/api/v1/telemetry", headers=auth(), json={**telemetry("A"), "device_id": "mystery-1"}).status_code == 200
    assert client.post("/api/v1/telemetry", headers=auth(), json={**telemetry("B"), "device_id": "mystery-1"}).status_code == 403
    devs = {d["device_id"]: d for d in client.get("/api/v1/devices").json()}
    assert devs["mystery-1"]["chamber_id"] == "A" and devs["mystery-1"]["registered"] == 0
    assert devs["fermentai-esp32-b"]["chamber_id"] == "B" and devs["fermentai-esp32-b"]["registered"] == 1


def test_ack_for_other_chamber_rejected(client):
    cmd = client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_B)).json()
    r = client.post("/api/v1/control-ack", headers=dev(TOK_A), json={
        "command_id": cmd["command_id"], "chamber_id": "B", "status": "accepted"})
    assert r.status_code == 403


def test_chamber_state_isolated(client):
    client.post("/api/v1/telemetry", headers=dev(TOK_A), json=telemetry("A", chamber_temperature_c=20.0))
    client.post("/api/v1/telemetry", headers=dev(TOK_B), json=telemetry("B", chamber_temperature_c=29.0))
    assert client.get("/api/v1/chambers/A/state").json()["latest_telemetry"]["chamber_temperature_c"] == 20.0
    assert client.get("/api/v1/chambers/B/state").json()["latest_telemetry"]["chamber_temperature_c"] == 29.0


# ----------------------------------------------------------------------------- commands
def test_commands_list_shape_and_order(client, app, clock):
    env = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    DesiredControl.model_validate(env)
    assert [c["action"] for c in env["commands"]] == ["set_setpoint"]
    sp = env["commands"][0]
    assert sp["command_id"] == env["command_id"] and sp["parameters"] == {"setpoint_c": 22.0, "control_mode": "auto"}
    motor = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 3000, "pattern": "nod"}).json()
    env = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    assert [c["action"] for c in env["commands"]] == ["set_setpoint", "animate_motor"]
    seqs = [c["sequence_number"] for c in env["commands"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 2      # one counter across actions
    m = env["commands"][1]
    assert m["command_id"] == motor["command_id"] and m["parameters"] == {"duration_ms": 3000, "pattern": "nod"}
    for c in env["commands"]:
        TypeAdapter(Command).validate_python(c)
    # acked commands leave the list
    client.post("/api/v1/control-ack", headers=dev(TOK_A), json={
        "command_id": sp["command_id"], "device_id": "fermentai-esp32-a", "chamber_id": "A",
        "status": "accepted", "device_uptime_ms": 5000, "result": {"applied_setpoint_c": 22.0}})
    env = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    assert [c["action"] for c in env["commands"]] == ["animate_motor"]
    # setpoint change -> new set_setpoint command (clamped)
    app.state.service.set_desired_setpoint("A", 99, "llm", "")
    env = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    new_sp = [c for c in env["commands"] if c["action"] == "set_setpoint"][0]
    assert new_sp["parameters"]["setpoint_c"] == 25.0 and new_sp["sequence_number"] > m["sequence_number"]
    # expired commands are not listed
    clock.advance(120)
    env = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    assert "animate_motor" not in [c["action"] for c in env["commands"]]


def test_control_mode_idle(client):
    r = client.post("/api/v1/chambers/B/setpoint", headers=auth(),
                    json={"setpoint_c": 27.0, "control_mode": "idle", "source": "operator"})
    assert r.status_code == 200 and r.json()["control_mode"] == "idle"
    env = client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_B)).json()
    assert env["control_mode"] == "idle" and env["peltier_mode_hint"] == "idle"
    assert env["commands"][0]["parameters"]["control_mode"] == "idle"


def test_motor_duration_ms_bounds(client):
    assert client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 10001}).status_code == 422
    assert client.post("/api/v1/chambers/A/motor", headers=auth(),
                       json={"duration_ms": 3000, "duration_s": 3}).status_code == 422
    assert client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 10000}).status_code == 200


def test_motor_ran_only_after_completed_ack(client):
    cmd = client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 3000}).json()
    assert cmd["ran"] is False
    log = lambda: {c["command_id"]: c for c in client.get("/api/v1/commands?action=animate_motor").json()}
    assert log()[cmd["command_id"]]["ran"] is False
    base = {"command_id": cmd["command_id"], "device_id": "fermentai-esp32-a", "chamber_id": "A"}
    client.post("/api/v1/control-ack", headers=dev(TOK_A), json={**base, "status": "accepted"})
    assert log()[cmd["command_id"]]["ran"] is False
    assert client.get("/api/v1/chambers/A/state").json()["motor_ran"] is False
    r = client.post("/api/v1/control-ack", headers=dev(TOK_A),
                    json={**base, "status": "completed", "device_uptime_ms": 9000, "result": {"ran_ms": 3000}})
    assert r.json()["action"] == "animate_motor"
    assert log()[cmd["command_id"]]["ran"] is True
    assert client.get("/api/v1/chambers/A/state").json()["motor_ran"] is True
    ack_ev = [e for e in client.get("/api/v1/events?types=ack").json() if e["data"]["status"] == "completed"][-1]
    assert ack_ev["data"]["motor_ran"] is True and ack_ev["data"]["result"] == {"ran_ms": 3000}


def test_cancel_creates_stop_motor_command(client):
    cmd = client.post("/api/v1/chambers/B/motor", headers=auth(), json={"duration_ms": 8000}).json()
    res = client.delete(f"/api/v1/commands/{cmd['command_id']}", headers=auth()).json()
    env = client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_B)).json()
    stop = [c for c in env["commands"] if c["action"] == "stop_motor"]
    assert stop and stop[0]["command_id"] == res["stop_command_id"]
    assert stop[0]["parameters"]["target_command_id"] == cmd["command_id"]
    assert env["motor"]["action"] == "stop"                       # v1.0 directive still present
    client.post("/api/v1/control-ack", headers=dev(TOK_B), json={
        "command_id": stop[0]["command_id"], "chamber_id": "B", "status": "completed"})
    env = client.get("/api/v1/chambers/B/desired-control", headers=dev(TOK_B)).json()
    assert env["motor"]["action"] == "none" and "stop_motor" not in [c["action"] for c in env["commands"]]


def test_legacy_v10_ack_still_accepted(client):
    cmd = client.get("/api/v1/chambers/A/desired-control", headers=dev(TOK_A)).json()
    r = client.post("/api/v1/control-ack", headers=dev(TOK_A), json={
        "schema_version": "1.0", "command_id": cmd["command_id"], "chamber_id": "A", "status": "accepted",
        "sequence_number": cmd["sequence_number"], "timestamp_utc": "2026-10-31T12:00:01Z",
        "applied_setpoint_c": 22.0, "message": None})
    assert r.status_code == 200 and r.json()["command_status"] == "accepted"


# ----------------------------------------------------------------------------- size limits
def test_request_size_limits(client):
    big = {**telemetry("A"), "quality_flags": ["x" * 60] * 1200}  # ~80 KB
    r = client.post("/api/v1/telemetry", headers=dev(TOK_A), json=big)
    assert r.status_code == 413
    jpeg = b"\xff\xd8" + b"\0" * 2_100_000
    r = client.post("/api/v1/images?chamber_id=A&source=measured", headers={**dev(TOK_A), "content-type": "image/jpeg"},
                    content=jpeg)
    assert r.status_code == 413
    # chunked body without Content-Length is counted as it streams
    def gen():
        for _ in range(100):
            yield b"x" * 1000
    r = client.post("/api/v1/telemetry", headers={**dev(TOK_A), "content-type": "application/json"}, content=gen())
    assert r.status_code == 413


# ----------------------------------------------------------------------------- endpoints
def test_agent_state_stub_and_module(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "fermentai.agents.state", None)
    st = client.get("/api/v1/agents/scientist_A/state").json()
    assert st["available"] is False and st["stub"] is True and st["chamber_id"] == "A"
    mod = types.ModuleType("fermentai.agents.state")
    mod.get_agent_state = lambda agent_id, **kw: {"agent_id": agent_id, "hypotheses": ["h1"], "mode": "offline"}
    monkeypatch.setitem(sys.modules, "fermentai.agents.state", mod)
    st = client.get("/api/v1/agents/scientist_B/state").json()
    assert st["available"] is True and st["hypotheses"] == ["h1"]


def test_demos_endpoints(tmp_path):
    data = tmp_path / "data"
    (data / "demos").mkdir(parents=True)
    (data / "demos" / "demo_01.json").write_text(json.dumps({"title": "Sour but airy", "provenance": "replay"}))
    (data / "demos" / "shot.png").write_bytes(b"\x89PNG")
    c = TestClient(make_app(tmp_path))
    files = c.get("/api/v1/demos").json()["files"]
    assert [f["name"] for f in files] == ["demo_01.json"] and files[0]["title"] == "Sour but airy"
    assert c.get(files[0]["url"]).json()["provenance"] == "replay"
    assert c.get("/api/v1/demos/shot.png").status_code == 415


def test_simulation_get_matches_contract(client):
    try:
        import fermentai.fermentation.simulation  # noqa: F401
    except ImportError:
        pytest.skip("simulator not available")
    r = client.post("/api/v1/simulations/run", headers=auth(),
                    json={"chamber_id": "A", "schedule": [{"t_h": 0, "setpoint_c": 22}], "duration_h": 2})
    assert r.status_code == 200, r.text
    SimulationResult.model_validate(r.json())
    got = client.get(f"/api/v1/simulations/{r.json()['simulation_id']}").json()
    SimulationResult.model_validate(got)
    assert got["provenance"] == "simulated" and got["schedule"]["breakpoints"] == [[0.0, 22.0]]
    assert client.get("/api/v1/simulations/99999").status_code == 404


# ----------------------------------------------------------------------------- DB migration
V10_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id TEXT NOT NULL, experiment_id TEXT,
  timestamp_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, source TEXT NOT NULL, chamber_temperature_c REAL,
  starter_temperature_c REAL, relative_humidity_percent REAL, gas_resistance_ohms REAL, ph REAL, ph_method TEXT,
  starter_height_mm REAL, door_open INTEGER, peltier_mode TEXT, temperature_setpoint_c REAL, motor_running INTEGER,
  quality_flags_json TEXT NOT NULL DEFAULT '[]', payload_json TEXT NOT NULL);
CREATE INDEX ix_telemetry_chamber ON telemetry(chamber_id, id);
CREATE TABLE chamber_control (chamber_id TEXT PRIMARY KEY, setpoint_c REAL NOT NULL, source TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '', updated_at_utc TEXT NOT NULL, last_changed_at_utc TEXT, experiment_id TEXT,
  version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sequences (chamber_id TEXT NOT NULL, command_type TEXT NOT NULL, value INTEGER NOT NULL,
  PRIMARY KEY (chamber_id, command_type));
CREATE TABLE commands (command_id TEXT PRIMARY KEY, chamber_id TEXT NOT NULL, command_type TEXT NOT NULL,
  sequence_number INTEGER NOT NULL, issued_at_utc TEXT NOT NULL, expires_at_utc TEXT NOT NULL, status TEXT NOT NULL,
  source TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
  delivered_count INTEGER NOT NULL DEFAULT 0, first_delivered_at_utc TEXT, acked INTEGER NOT NULL DEFAULT 0,
  status_updated_at_utc TEXT NOT NULL);
CREATE TABLE agent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at_utc TEXT NOT NULL, feedback_id INTEGER,
  sender TEXT, recipient TEXT, sender_key TEXT, recipient_key TEXT, kind TEXT, content_json TEXT NOT NULL);
INSERT INTO meta VALUES ('server_epoch', 'oldepoch0001');
INSERT INTO telemetry(chamber_id, timestamp_utc, received_at_utc, source, chamber_temperature_c, payload_json)
  VALUES ('A', '2026-10-30T10:00:00Z', '2026-10-30T10:00:01Z', 'simulated', 21.5, '{}');
INSERT INTO chamber_control VALUES ('A', 23.0, 'agent', '', '2026-10-30T10:00:00Z', NULL, NULL, 3);
INSERT INTO sequences VALUES ('A', 'control', 7), ('A', 'motor', 9);
INSERT INTO commands VALUES ('old-motor', 'A', 'motor', 9, '2026-10-30T10:00:00Z', '2026-10-30T10:01:00Z',
  'completed', 'x', '', '{"action":"run","duration_s":5,"pattern":"wiggle"}', 1, NULL, 1, '2026-10-30T10:00:00Z');
"""


def test_migration_from_v10_db_is_idempotent(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(V10_SCHEMA)
    conn.close()
    db = Database(path)
    assert "telemetry.timestamp_utc nullable" in db.migrations_applied
    assert "chamber_control.control_mode" in db.migrations_applied
    assert Database(path).migrations_applied == []           # second run: nothing to do
    row = db.query_one("SELECT * FROM telemetry")
    assert row["chamber_temperature_c"] == 21.5 and row["boot_id"] is None
    assert db.meta("server_epoch") == "oldepoch0001"
    for view in ("chambers", "camera_images", "experiment_definitions", "simulation_runs", "hardware_commands",
                 "command_acknowledgments", "scientist_messages", "sensory_feedback"):
        db.query(f"SELECT * FROM {view} LIMIT 1")
    for table in ("devices", "sensor_readings", "temperature_schedules", "scientist_hypotheses",
                  "model_calibrations", "molecular_results"):
        db.query(f"SELECT * FROM {table} LIMIT 1")
    assert db.query_one("SELECT action FROM commands WHERE command_id='old-motor'")["action"] == "animate_motor"
    # the migrated DB works end to end; sequence numbers continue above the v1.0 counters
    clock = FakeClock()
    from fermentai.api.config import load_hardware_config
    from fermentai.api.devices import ControlService
    svc = ControlService(db, load_hardware_config(), clock)
    env = svc.desired_control("A")
    assert env["temperature_setpoint_c"] == 23.0 and env["sequence_number"] == 10
    assert svc.issue_motor_command("A", 2.0)["sequence_number"] == 11
    from fermentai.api.schemas import TelemetryIn
    svc.ingest_telemetry(TelemetryIn.model_validate(PLAN_MASTER_TELEMETRY))
    assert db.query_one("SELECT timestamp_utc FROM telemetry ORDER BY id DESC LIMIT 1")["timestamp_utc"] is None
