"""MOCK tests: the full PreForm pipeline through the real formlabs-sdk against MockPreFormServer.

No PreFormServer binary is used here. Everything produced is provenance "replay".
Live tests are in tests/test_formlabs_live.py (opt-in).
"""

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("formlabs_sdk")

from fermentai.formlabs import MockPreFormServer, RecordingProxy
from fermentai.formlabs.cli import main
from fermentai.formlabs.manifest import check_manifest
from fermentai.formlabs.prepare import FIXTURE
from fermentai.formlabs.replay import MOCK_FORM_BYTES


@pytest.fixture(scope="module")
def mock_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("formlabs_mock")
    rc = main(["prepare", "--backend", "mock", "--out", str(out)])
    assert rc == 0
    return out, json.loads((out / "print_manifest.json").read_text())


def test_fixture_is_a_recorded_live_session():
    doc = json.loads(FIXTURE.read_text())
    assert doc["recorded_by"] == "fermentai.formlabs.replay.RecordingProxy"
    assert doc["server_version"].startswith("3.64.2")
    paths = {e["path"] for e in doc["entries"]}
    for p in ("/v1/scene/create/", "/v1/scene/model/import/", "/v1/scene/model/auto-support/",
              "/v1/scene/model/auto-layout/", "/v1/scene/calculate-print-time/",
              "/v1/scene/save/", "/v1/scene/render/"):
        assert p in paths
    assert "/v1/scene/print/" not in paths  # nothing was ever printed


def test_mock_pipeline_end_to_end(mock_run):
    _, m = mock_run
    assert m["source"].startswith("mock")
    assert m["provenance"] == "replay"
    assert m["job_submitted"] is False
    assert check_manifest(m) == []
    assert not any("mock had no recording" in n for n in m["notes"])
    assert m["printer"]["machine_type"] == "FORM-4-0"
    assert m["material"]["code"] == "FLGPGR05"
    assert m["build"]["estimates"]["print_time_s"] > 0
    assert m["build"]["estimates"]["material_volume_ml"] > 0
    assert set(m["per_chamber_builds"]) == {"A", "B"}
    assert all(v["open_face_up"] for v in m["orientation"]["final"].values())
    assert m["build"]["form_roundtrip"]["ok"] is True


def test_mock_outputs_are_marked_placeholders(mock_run):
    out, m = mock_run
    form = out / "crumb_chambers_AB_form4-grey-v5.form"
    assert form.read_bytes() == MOCK_FORM_BYTES  # placeholder bytes, not a real .form
    assert m["build"]["outputs"]["form"]["kind"] == "placeholder (mock)"
    png = out / "crumb_chambers_AB_form4-grey-v5_iso.png"
    assert png.read_bytes().startswith(b"\x89PNG")


def test_mock_estimates_equal_the_recording(mock_run):
    _, m = mock_run
    doc = json.loads(FIXTURE.read_text())
    times = [e["response"]["result"]["total_print_time_s"] for e in doc["entries"]
             if isinstance(e.get("response"), dict)
             and isinstance(e["response"].get("result"), dict)
             and "total_print_time_s" in e["response"]["result"]]
    assert m["build"]["estimates"]["print_time_s"] == times[0]


def test_mock_and_proxy_refuse_print_endpoint():
    doc = json.loads(FIXTURE.read_text())
    with MockPreFormServer(transcript=doc["entries"]) as mock:
        r = httpx.post(f"{mock.base_url}/v1/scene/print/", json={"scene_id": "x"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FERMENTAI_PRINT_BLOCKED"
        assert httpx.get(f"{mock.base_url}/v1/").json()["version"].startswith("3.64.2")
        seen_by_mock = sum(q["path"] == "/v1/scene/print/" for q in mock.requests)
        with RecordingProxy(upstream=mock.base_url) as proxy:
            r = httpx.post(f"{proxy.base_url}/v1/scene/print/", json={"scene_id": "x"})
            assert r.status_code == 403
            assert proxy.transcript()[-1]["path"] == "/v1/scene/print/"
        # the proxy blocked it itself: the upstream never received the print request
        assert sum(q["path"] == "/v1/scene/print/" for q in mock.requests) == seen_by_mock


def test_open_preform_live_without_server_raises(monkeypatch, tmp_path):
    from fermentai.formlabs import client

    monkeypatch.setattr(client, "locate_preform_server", lambda *_a, **_k: None)
    monkeypatch.setattr(client, "server_version", lambda *_a, **_k: None)
    with pytest.raises(client.PreFormUnavailable) as exc, client.open_preform("live", port=1):
        pass
    assert "downloads.formlabs.com/PreFormServer" in str(exc.value)


def test_auto_falls_back_to_mock_without_server(monkeypatch):
    from fermentai.formlabs import client

    monkeypatch.setattr(client, "locate_preform_server", lambda *_a, **_k: None)
    monkeypatch.setattr(client, "server_version", lambda *_a, **_k: None)
    with client.open_preform("auto", port=1, fixture=Path(FIXTURE)) as h:
        assert h.backend == "mock"
        assert any("not available" in n for n in h.notes)
