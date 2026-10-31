"""Read-only data endpoints for the dashboard."""

import json

from fastapi.testclient import TestClient

from fermentai.api.testing import make_app


def test_data_endpoints_empty(tmp_path):
    c = TestClient(make_app(tmp_path))
    assert c.get("/api/v1/predictions").json()["files"] == []
    mol = c.get("/api/v1/molecular/summary").json()
    assert mol["available"] is False
    assert c.get("/api/v1/stl").json()["files"] == []
    assert c.get("/api/v1/findings").json()["available"] is False


def test_data_endpoints_with_files(tmp_path):
    data = tmp_path / "data"
    (data / "predictions").mkdir(parents=True)
    (data / "predictions" / "chamber_A.csv").write_text("time_h,ph\n0,6.0\n")
    (data / "alchemi" / "analysis").mkdir(parents=True)
    (data / "alchemi" / "analysis" / "summary.json").write_text(json.dumps({"energy_eV": -1.0}))
    (data / "stl").mkdir()
    (data / "stl" / "crumb_mild.stl").write_text("solid x\nendsolid x\n")
    (data / "findings.jsonl").write_text('{"agent":"A","observation":"x"}\n{"agent":"B"}\n')
    (tmp_path / "secret.txt").write_text("nope")
    c = TestClient(make_app(tmp_path))
    files = c.get("/api/v1/predictions").json()["files"]
    assert files[0]["name"] == "chamber_A.csv"
    assert c.get(files[0]["url"]).text.startswith("time_h")
    mol = c.get("/api/v1/molecular/summary").json()
    assert mol["available"] and mol["summaries"]["summary.json"]["energy_eV"] == -1.0
    stl = c.get("/api/v1/stl").json()["files"][0]
    assert c.get(stl["url"]).status_code == 200
    f = c.get("/api/v1/findings").json()
    assert f["available"] and len(f["findings"]) == 2
    assert c.get("/api/v1/predictions/../../secret.txt").status_code == 404
    assert c.get("/api/v1/predictions/%2e%2e/%2e%2e/secret.txt").status_code == 404


def test_static_dashboard_mounted(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>dash</html>")
    c = TestClient(make_app(tmp_path, static_dir=static))
    assert "dash" in c.get("/").text
    assert c.get("/api/v1/health").json()["dashboard_static"] is True
