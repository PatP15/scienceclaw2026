"""Manifest provenance tests (pure functions + the committed LIVE manifest in data/stl/print/)."""

import json
from pathlib import Path

import pytest

from fermentai.formlabs.manifest import (
    SOURCE_LIVE,
    SOURCE_MOCK,
    build_manifest,
    check_manifest,
    write_manifest,
)
from fermentai.formlabs.prepare import (
    CHAIN_SUMMARY,
    PROFILES,
    REPO_ROOT,
    load_chamber_inputs,
    sha256_file,
)

LIVE_MANIFEST = REPO_ROOT / "data" / "stl" / "print" / "print_manifest.json"


def _stub_build():
    return {"build_name": "t", "chambers": ["A", "B"], "orientation_policy": "open_face_up",
            "scene_settings": {"machine_type": "FORM-4-0", "material_code": "FLGPGR05",
                               "layer_thickness_mm": "0.1", "print_setting": "DEFAULT"},
            "estimates": {"print_time_s": 1, "material_volume_ml": 1.0}, "outputs": {},
            "metadata": {"MaterialName": "Grey V5"}}


def _manifest(backend, **kw):
    chambers = load_chamber_inputs(CHAIN_SUMMARY)
    return build_manifest(backend=backend, profile=PROFILES["form4-grey-v5"], chambers=chambers,
                          build=_stub_build(), per_chamber=None, server={}, api={},
                          transcript_path=None, server_log_path=None, **kw)


def test_inputs_carry_stl_hashes_from_chain_summary():
    chain = json.loads(CHAIN_SUMMARY.read_text())
    m = _manifest("live")
    for inp in m["inputs"]:
        assert inp["stl_sha256_chain_summary"] == chain["chambers"][inp["chamber"]]["stl_sha256"]
        assert inp["stl_sha256"] == sha256_file(REPO_ROOT / inp["stl"])
        assert inp["sha256_matches_chain_summary"] is True
        assert inp["mesh_validation_passed"] is True


def test_live_and_mock_labels():
    live, mock = _manifest("live"), _manifest("mock")
    assert live["source"] == SOURCE_LIVE and live["provenance"] == "estimated"
    assert mock["source"] == SOURCE_MOCK and mock["provenance"] == "replay"
    assert _manifest("mock", mock_reason="x")["source"] == "mock — x"
    for m in (live, mock):
        assert m["job_submitted"] is False
        assert m["print_submission"]["submitted"] is False
        assert "food" in m["food_contact"]
        assert check_manifest(m) == []


def test_check_manifest_rejects_inconsistent(tmp_path):
    m = _manifest("live")
    m["job_submitted"] = True
    assert "job_submitted must be False" in check_manifest(m)
    with pytest.raises(ValueError):
        write_manifest(tmp_path / "m.json", m)
    m = _manifest("mock")
    m["provenance"] = "estimated"
    assert check_manifest(m)


@pytest.mark.skipif(not LIVE_MANIFEST.is_file(), reason="no live run committed")
def test_committed_live_manifest_is_real_and_consistent():
    """The committed manifest came from a LIVE PreFormServer run; its files must match it."""
    m = json.loads(LIVE_MANIFEST.read_text())
    assert check_manifest(m) == []
    assert m["source"] == SOURCE_LIVE
    assert m["preform_server"]["version"].startswith("3.64")
    assert "Formlabs" in (m["preform_server"].get("code_signature") or "")
    assert m["job_submitted"] is False
    assert m["build"]["form_roundtrip"]["ok"] is True
    paths = {e["path"] for e in m["api"]["endpoints_used"]}
    assert "/v1/scene/print/" not in paths
    for rec in m["build"]["outputs"].values():
        p = REPO_ROOT / rec["path"]
        assert p.is_file() and sha256_file(p) == rec["sha256"]
        assert rec["kind"] == "written by PreFormServer"
    assert (REPO_ROOT / m["outputs"]["transcript"]).is_file()
    form = Path(REPO_ROOT / m["build"]["outputs"]["form"]["path"])
    assert form.stat().st_size > 100_000  # a real PreForm scene, not a placeholder
