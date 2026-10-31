"""Chamber state: latest_image must come from the live telemetry device; device registry marks
virtual ids inactive when the chamber has a measured device."""

import io

from fastapi.testclient import TestClient

from fermentai.api.testing import auth, make_app, telemetry


def jpeg() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 180, 140)).save(buf, "JPEG")
    return buf.getvalue()


def upload(c, chamber, source, device_id=None):
    data = {"chamber_id": chamber, "source": source}
    if device_id:
        data["device_id"] = device_id
    r = c.post("/api/v1/images", headers=auth(), files={"file": ("x.jpg", jpeg(), "image/jpeg")}, data=data)
    assert r.status_code == 200, r.text
    return r.json()["image_id"]


def test_latest_image_only_from_live_device(tmp_path):
    c = TestClient(make_app(tmp_path))
    # no telemetry device yet -> newest image is shown
    first = upload(c, "A", "simulated", "simulated-esp32-a")
    st = c.get("/api/v1/chambers/A/state").json()
    assert st["latest_image"]["id"] == first and st["stale_image_from_other_device"] is None
    # real board reports measured telemetry
    c.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", source="measured", device_id="fermentai-esp32-a"))
    st = c.get("/api/v1/chambers/A/state").json()
    assert st["latest_image"] is None                                      # simulated image not attached
    assert st["stale_image_from_other_device"]["id"] == first
    assert "simulated-esp32-a" in st["stale_image_from_other_device"]["reason"]
    real = upload(c, "A", "measured", "fermentai-esp32-a")
    st = c.get("/api/v1/chambers/A/state").json()
    assert st["latest_image"]["id"] == real and st["latest_image"]["device_id"] == "fermentai-esp32-a"
    assert st["stale_image_from_other_device"] is None
    # a virtual device using the SAME id but simulated provenance is still not "the live device"
    sim_same_id = upload(c, "A", "simulated", "fermentai-esp32-a")
    st = c.get("/api/v1/chambers/A/state").json()
    assert st["latest_image"]["id"] == real and st["stale_image_from_other_device"]["id"] == sim_same_id
    assert c.get("/api/v1/images?chamber_id=A").json()[0]["device_id"] == "fermentai-esp32-a"


def test_device_registry_marks_virtual_ids_inactive(tmp_path):
    c = TestClient(make_app(tmp_path))
    c.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", source="simulated", device_id="simulated-esp32-a"))
    devs = {d["device_id"]: d for d in c.get("/api/v1/devices?chamber_id=A").json()}
    assert devs["simulated-esp32-a"]["active"] is True                      # only virtual so far
    assert devs["fermentai-esp32-a"]["active"] is False and devs["fermentai-esp32-a"]["inactive_reason"] == "never seen"
    c.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", source="measured", device_id="fermentai-esp32-a"))
    devs = {d["device_id"]: d for d in c.get("/api/v1/devices?chamber_id=A").json()}
    assert devs["fermentai-esp32-a"]["active"] is True
    assert devs["simulated-esp32-a"]["active"] is False and "measured device" in devs["simulated-esp32-a"]["inactive_reason"]
    assert {d["device_id"] for d in c.get("/api/v1/devices?chamber_id=A&active=true").json()} == {"fermentai-esp32-a"}
    st = c.get("/api/v1/chambers/A/state").json()
    assert [d["device_id"] for d in st["devices"]] == ["fermentai-esp32-a"]
    assert "simulated-esp32-a" in {d["device_id"] for d in st["inactive_devices"]}
    # chamber B untouched: nothing measured there, never-seen ids inactive, nothing deleted
    b = c.get("/api/v1/devices?chamber_id=B").json()
    assert b and all(d["active"] is False for d in b)
