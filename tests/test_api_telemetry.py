"""Telemetry ingest, auth, images, chamber state (acceptance test #5)."""

import io

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import FakeClock, auth, make_app, telemetry


@pytest.fixture
def client(tmp_path):
    return TestClient(make_app(tmp_path, clock=FakeClock()))


def test_ingest_both_chambers(client):
    for cid, temp in (("A", 22.1), ("B", 26.8)):
        r = client.post("/api/v1/telemetry", headers=auth(), json=telemetry(cid, chamber_temperature_c=temp))
        assert r.status_code == 200, r.text
        assert r.json()["chamber_id"] == cid
    a = client.get("/api/v1/chambers/A/state").json()
    b = client.get("/api/v1/chambers/B/state").json()
    assert a["latest_telemetry"]["chamber_temperature_c"] == 22.1
    assert b["latest_telemetry"]["chamber_temperature_c"] == 26.8
    assert a["provenance"] == b["provenance"] == "simulated"
    assert len(client.get("/api/v1/chambers/A/telemetry").json()) == 1


def test_plan_fixture_and_nullable_fields(client):
    plan_fixture = {
        "schema_version": "1.0", "chamber_id": "A", "experiment_id": "trial_001",
        "timestamp_utc": "2026-10-31T12:00:00Z", "chamber_temperature_c": 24.5,
        "starter_temperature_c": 23.9, "relative_humidity_percent": 72.0,
        "gas_resistance_ohms": 105432.0, "ph": None, "ph_method": None, "starter_height_mm": 28.0,
        "door_open": False, "peltier_mode": "idle", "temperature_setpoint_c": 24.0,
        "motor_running": False, "source": "simulated", "quality_flags": [],
    }
    assert client.post("/api/v1/telemetry", headers=auth(), json=plan_fixture).status_code == 200
    minimal = {"schema_version": "1.0", "chamber_id": "B", "source": "real"}  # all sensors missing
    r = client.post("/api/v1/telemetry", headers=auth(), json=minimal)
    assert r.status_code == 200
    assert "timestamp_unavailable" in r.json()["quality_flags"]  # left null (v1.1)
    assert client.get("/api/v1/chambers/B/state").json()["latest_telemetry"]["timestamp_utc"] is None


@pytest.mark.parametrize("bad, fragment", [
    ({"schema_version": "2.0"}, "unsupported schema_version"),
    ({"source": "unknown_source"}, "source"),
    ({"peltier_mode": "turbo"}, "peltier_mode"),
    ({"co2_ppm": 400}, "co2_ppm"),
])
def test_rejects_invalid(client, bad, fragment):
    r = client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", **bad))
    assert r.status_code == 422
    assert fragment in r.text


def test_nan_rejected(client):
    body = '{"schema_version":"1.0","chamber_id":"A","source":"real","chamber_temperature_c":NaN}'
    r = client.post("/api/v1/telemetry", headers={**auth(), "content-type": "application/json"},
                    content=body)
    assert r.status_code == 422


def test_unknown_chamber(client):
    r = client.post("/api/v1/telemetry", headers=auth(), json=telemetry("Z"))
    assert r.status_code == 404
    assert client.get("/api/v1/chambers/Z/state").status_code == 404


def test_auth_rejects_missing_and_bad_token(client):
    assert client.post("/api/v1/telemetry", json=telemetry("A")).status_code == 401
    r = client.post("/api/v1/telemetry", headers=auth("wrong"), json=telemetry("A"))
    assert r.status_code == 403
    assert client.get("/api/v1/chambers/A/desired-control").status_code == 401
    assert client.post("/api/v1/chambers/A/motor", json={}).status_code == 401
    assert client.post("/api/v1/taste-feedback", json={"text": "x"}).status_code == 401
    # read-only endpoints are open on the LAN
    assert client.get("/api/v1/chambers/A/state").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def _jpeg() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 180, 140)).save(buf, "JPEG")
    return buf.getvalue()


def test_image_upload_multipart_and_raw(client, tmp_path):
    r = client.post("/api/v1/images", headers=auth(),
                    files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
                    data={"chamber_id": "A", "timestamp_utc": "2026-10-31T12:00:00Z",
                          "source": "simulated"})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert list((tmp_path / "data" / "images" / "A").glob("*.jpg"))
    got = client.get(url)
    assert got.status_code == 200 and got.content.startswith(b"\xff\xd8")
    r = client.post("/api/v1/images?chamber_id=B&source=real&timestamp_utc=2026-10-31T12:05:00Z",
                    headers={**auth(), "content-type": "image/jpeg"}, content=_jpeg())
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/chambers/B/state").json()["latest_image"]["url"].startswith("/api/v1/images/")
    # not a JPEG / missing provenance
    bad = client.post("/api/v1/images", headers=auth(), files={"file": ("x.jpg", b"PNG...", "image/jpeg")},
                      data={"chamber_id": "A", "source": "real"})
    assert bad.status_code == 422
    nosrc = client.post("/api/v1/images", headers=auth(), files={"file": ("x.jpg", _jpeg(), "image/jpeg")},
                        data={"chamber_id": "A"})
    assert nosrc.status_code == 422


def test_placeholder_root_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "FermentAI" in r.text
