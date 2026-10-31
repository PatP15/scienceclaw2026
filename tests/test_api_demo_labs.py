"""Demo network labs (C-F, kind=demo): SIMULATED labs at fictional restaurants."""

from fastapi.testclient import TestClient

from fermentai.api.config import load_hardware_config
from fermentai.api.testing import auth, make_app, telemetry

DEMO = {"C": "Crumb & Copper", "D": "Levain Lane", "E": "Hoshi Bakehouse", "F": "Casa Fermento"}


def test_config_parses_demo_labs():
    hw = load_hardware_config()
    assert list(hw.chambers) == ["A", "B", *DEMO]
    for cid, label in DEMO.items():
        c = hw.chambers[cid]
        assert c.label == label and c.kind == "demo" and c.location
        assert c.device_id == f"fermentai-sim-{cid.lower()}"
    assert hw.chambers["A"].kind == hw.chambers["B"].kind == "lab"
    assert hw.chambers["A"].device_id == "fermentai-esp32-a"
    assert [hw.chambers[c].initial_setpoint_c for c in DEMO] == [21.0, 24.0, 26.0, 28.0]
    assert len(hw.device_bindings()) == len({d for c in hw.chambers.values() for d in c.all_device_ids()})


def test_chambers_expose_label_location_kind(tmp_path):
    c = TestClient(make_app(tmp_path))
    body = c.get("/api/v1/chambers").json()
    assert list(body) == ["A", "B", "C", "D", "E", "F"]
    assert body["A"]["kind"] == "lab" and body["B"]["kind"] == "lab"
    for cid, label in DEMO.items():
        assert body[cid]["label"] == label and body[cid]["kind"] == "demo" and body[cid]["location"]
        st = c.get(f"/api/v1/chambers/{cid}/state").json()
        assert (st["label"], st["kind"], st["location"]) == (label, "demo", body[cid]["location"])


def test_demo_lab_routes_do_not_crash(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.post("/api/v1/telemetry", headers=auth(),
               json=telemetry("C", source="simulated", device_id="fermentai-sim-c"))
    assert r.status_code == 200, r.text
    st = c.get("/api/v1/chambers/C/state").json()
    assert st["provenance"] == "simulated" and st["desired_setpoint_c"] == 21.0
    r = c.post("/api/v1/chambers/D/setpoint", headers=auth(), json={"setpoint_c": 25.0, "source": "operator"})
    assert r.status_code == 200, r.text
    assert c.post("/api/v1/chambers/E/motor", headers=auth(), json={"pattern": "wiggle"}).status_code == 200
    assert c.get("/api/v1/chambers/F/telemetry").status_code == 200
    assert c.get("/api/v1/chambers/C/desired-control",
                 headers={**auth(), "X-Device-Id": "fermentai-sim-c"}).status_code == 200


def test_chat_for_demo_station_is_400_not_500(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.post("/api/v1/chat", headers=auth(), json={"text": "hi", "sous_chef": "C"})
    assert r.status_code == 400, r.text
    assert c.post("/api/v1/chat", headers=auth(), json={"text": "hi", "sous_chef": "Z"}).status_code == 400
