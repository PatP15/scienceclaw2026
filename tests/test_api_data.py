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


NAN_JSON = '{"energy_eV": NaN, "rows": [1.0, Infinity, -Infinity], "ok": 2.5}'  # as Python json.dump writes


def test_nan_in_data_files_is_served_as_null(tmp_path):
    data = tmp_path / "data"
    for sub in ("alchemi/analysis", "predictions", "stl", "benchmarks"):
        (data / sub).mkdir(parents=True)
    (data / "alchemi/analysis/analysis_summary.json").write_text(NAN_JSON)
    (data / "predictions/chamber_A_summary.json").write_text(NAN_JSON)
    (data / "stl/geometry.json").write_text(NAN_JSON)
    (data / "benchmarks/summary.json").write_text(NAN_JSON)
    (data / "benchmarks/benchmark_results.csv").write_text("policy,score\ncollab,nan\n")
    (data / "findings.jsonl").write_text('{"agent_id": "scientist_A", "value": NaN}\n')
    c = TestClient(make_app(tmp_path))
    mol = c.get("/api/v1/molecular/summary")
    assert mol.status_code == 200
    s = mol.json()["summaries"]["analysis_summary.json"]
    assert s == {"energy_eV": None, "rows": [1.0, None, None], "ok": 2.5}
    for url in ("/api/v1/molecular/files/analysis_summary.json", "/api/v1/predictions/chamber_A_summary.json",
                "/api/v1/stl/geometry.json", "/api/v1/benchmarks/summary.json"):
        r = c.get(url)
        assert r.status_code == 200 and "NaN" not in r.text and "Infinity" not in r.text, url
        assert json.loads(r.text)["energy_eV"] is None
    b = c.get("/api/v1/benchmarks").json()
    assert b["available"] and b["summary"]["energy_eV"] is None
    assert {f["name"] for f in b["files"]} == {"summary.json", "benchmark_results.csv"}
    assert c.get("/api/v1/benchmarks/benchmark_results.csv").text.startswith("policy")
    assert c.get("/api/v1/findings").json()["findings"][0]["value"] is None


def test_safe_default_response_never_500s_on_nan(tmp_path):
    from fermentai.api.jsonutil import SafeJSONResponse
    body = SafeJSONResponse({"x": float("nan"), "y": [float("inf")]}).body
    assert json.loads(body) == {"x": None, "y": [None]}


def test_molecular_environment(tmp_path):
    c = TestClient(make_app(tmp_path))
    assert c.get("/api/v1/molecular/environment").json()["available"] is False
    env = tmp_path / "data/alchemi/environment"
    env.mkdir(parents=True)
    (env / "report.md").write_text("# Env\n\n## Runtime and cost\n\n"
                                   "- Elapsed instance time 1.14 h × $0.60/h ≈ **$0.68** (estimate)\n")
    (env / "remote_environment.json").write_text('{"device": "NVIDIA RTX A6000", "load_s": NaN}')
    (env / "nvidia-smi.txt").write_text("GPU 0: A6000")
    c = TestClient(make_app(tmp_path))
    j = c.get("/api/v1/molecular/environment").json()
    assert j["available"] and j["report_md"].startswith("# Env")
    assert j["remote_environment"] == {"device": "NVIDIA RTX A6000", "load_s": None}
    assert j["cost"]["estimate_usd"] == 0.68 and j["cost"]["hours"] == 1.14 and j["cost"]["rate_usd_per_h"] == 0.6
    f = {x["name"]: x for x in j["files"]}
    assert c.get(f["nvidia-smi.txt"]["url"]).text == "GPU 0: A6000"
