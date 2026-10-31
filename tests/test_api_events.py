"""SSE event stream + JSON event log."""

import json

from fastapi.testclient import TestClient

from fermentai.api.testing import auth, make_app, telemetry


def _frames(text):
    return [f for f in text.split("\n\n") if f.startswith("id:")]


def test_sse_yields_telemetry_event(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A"))
    with client.stream("GET", "/api/v1/events/stream?since=0&max_events=1") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    frame = _frames(body)[0]
    assert "event: telemetry" in frame
    data = json.loads(frame.split("data: ", 1)[1])
    assert data["type"] == "telemetry" and data["chamber_id"] == "A"
    assert data["data"]["source"] == "simulated"


def test_sse_type_filter_plain_format_and_timeout(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A"))
    client.post("/api/v1/chambers/B/motor", headers=auth(), json={"duration_s": 2})
    with client.stream("GET", "/api/v1/events/stream?since=0&types=command&format=plain&max_events=1") as r:
        frame = _frames("".join(r.iter_text()))[0]
    assert "event:" not in frame and '"type":"command"' in frame
    with client.stream("GET", "/api/v1/events/stream?timeout_s=0.2") as r:  # no new events -> closes
        assert _frames("".join(r.iter_text())) == []


def test_events_json_log(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("B"))
    ev = client.get("/api/v1/events?since=0").json()
    assert ev and ev[0]["type"] == "telemetry"
    assert "feedback_result" in client.get("/api/v1/events/types").json()
