"""Generic parts path: job files, STL inspection, and the multi-build job through the MOCK backend
(replay of the recorded live 'hackathon' session — no PreFormServer binary is used)."""

import json

import pytest

from fermentai.formlabs.parts import inspect_stl, load_job, parse_part_arg
from fermentai.formlabs.prepare import REPO_ROOT, PartSpec, expand_parts

JOB = REPO_ROOT / "data" / "stl" / "print" / "jobs" / "hackathon_six.yaml"
LABELS = {"real", "measured", "simulated", "replay", "estimated", "imported_human"}


def _label(v):
    return v.split(":", 1)[0].strip() if isinstance(v, str) else None


def test_job_file_parses_six_copies_in_priority_order():
    job = load_job(JOB)
    assert job["profile"] == "form4-grey-v5"
    names = [i.key for i in expand_parts(job["parts"])]
    assert names == ["A", "B", "hat_1", "hat_2", "clawd_1", "clawd_2"]
    a, b, hat, clawd = job["parts"]
    assert (a.label, b.label) == ("mild, 22 °C", "sour, 27 °C")
    assert a.orientation == b.orientation == "open_face_up" and a.repair == "error"
    assert a.sha256_expected and a.sha256_matches is True  # pulled from chain_summary.json
    assert hat.orientation == clawd.orientation == "preform_auto"
    assert hat.repair == "error_then_repair" and hat.provenance == "imported_human"
    assert hat.sha256_actual and clawd.sha256_actual


def test_part_arg_and_validation(tmp_path):
    f = tmp_path / "thing.stl"
    f.write_bytes(b"solid x\nendsolid x\n")
    p = parse_part_arg(f"{f}:3:open_face_up")
    assert (p.name, p.count, p.orientation, p.model_name) == ("thing", 3, "open_face_up", "thing")
    assert [i.key for i in expand_parts([p])] == ["thing_1", "thing_2", "thing_3"]
    with pytest.raises(ValueError):
        PartSpec(name="x", stl=f, orientation="sideways")
    with pytest.raises(ValueError):
        PartSpec(name="x", stl=f, count=0)
    with pytest.raises(ValueError):
        PartSpec(name="x", stl=f, repair="maybe")


def test_inspect_stl_units_and_degenerate_triangles(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    import numpy as np

    box = trimesh.creation.box(extents=(40, 20, 10))
    ok = tmp_path / "box.stl"
    box.export(ok)
    rep = inspect_stl(ok)
    assert rep["units_check"]["plausible"] and rep["as_supplied"]["watertight"]
    assert rep["extents_mm"] == [40.0, 20.0, 10.0]
    assert rep["defects"] == []

    tiny = tmp_path / "tiny.stl"
    box.copy().apply_scale(0.04).export(tiny)  # 1.6 mm: inches/cm-sized model read as mm?
    rep = inspect_stl(tiny)
    assert rep["units_check"]["plausible"] is False
    assert rep["units_check"]["if_inches_mm"] == pytest.approx(1.6 * 25.4, rel=1e-3)

    # add a zero-area sliver like the user's files have: non-manifold as supplied, clean without
    v = np.vstack([box.vertices, box.vertices[[0, 1]]])
    f = np.vstack([box.faces, [[0, 1, len(v) - 1]]])
    sliver = trimesh.Trimesh(v, f, process=False)
    sliver.vertices[-1] = (sliver.vertices[0] + sliver.vertices[1]) / 2
    bad = tmp_path / "sliver.stl"
    sliver.export(bad)
    rep = inspect_stl(bad)
    assert rep["as_supplied"]["degenerate_zero_area_triangles"] >= 1
    assert rep["after_dropping_zero_area_triangles"]["watertight"] is True


def test_committed_parts_report_has_provenance_and_mm_verdicts():
    rep = json.loads((REPO_ROOT / "data/stl/external/parts_report.json").read_text())
    assert _label(rep["provenance"]) == "imported_human"
    for name in ("hat", "clawd"):
        p = rep["parts"][name]
        assert p["units_check"]["plausible"] is True
        assert p["after_dropping_zero_area_triangles"]["watertight"] is True


@pytest.fixture(scope="module")
def mock_job(tmp_path_factory):
    pytest.importorskip("formlabs_sdk")
    from fermentai.formlabs.cli import main

    out = tmp_path_factory.mktemp("formlabs_job_mock")
    assert main(["prepare", "--job", str(JOB), "--backend", "mock", "--out", str(out)]) == 0
    return out


def test_mock_job_splits_into_two_builds(mock_job):
    summary = json.loads((mock_job / "hackathon_jobs.json").read_text())
    assert summary["provenance"] == "replay" and summary["source"].startswith("mock")
    assert summary["job_submitted"] is False
    builds = summary["builds"]
    placed = [k for b in builds for k in b["placed"]]
    assert sorted(placed) == sorted(["A", "B", "hat_1", "hat_2", "clawd_1", "clawd_2"])
    assert len(builds) == 2 and builds[0]["placed"][:2] == ["A", "B"]
    for b in builds:
        m = json.loads(
            (mock_job / f"hackathon_job{b['build']}_form4-grey-v5_manifest.json").read_text())
        assert m["provenance"] == "replay" and m["job_submitted"] is False
        for inp in m["inputs"]:
            assert inp["scaled"] is False


def test_mock_job_layout_attempts_and_orientation(mock_job):
    m1 = json.loads((mock_job / "hackathon_job1_form4-grey-v5_manifest.json").read_text())
    attempts = m1["layout"]["attempts"]
    assert attempts[0]["fit"] is False and attempts[-1]["fit"] is True
    pol = m1["orientation"]["per_model_policy"]
    assert pol["A"] == pol["B"] == "open_face_up"
    assert all(v["open_face_up"] for k, v in m1["orientation"]["final"].items() if k in "AB")
    m2 = json.loads((mock_job / "hackathon_job2_form4-grey-v5_manifest.json").read_text())
    assert set(m2["orientation"]["per_model_policy"].values()) == {"preform_auto"}


def test_mock_job_print_sheet_and_provenance_of_every_json(mock_job):
    sheet = (mock_job / "PRINT_SHEET.md").read_text()
    assert "Nothing has been sent to a printer" in sheet
    assert "hackathon_job1_form4-grey-v5.form" in sheet
    assert "hackathon_job2_form4-grey-v5.form" in sheet
    assert "not food-safe" in sheet and "IPA" in sheet
    for f in mock_job.glob("*.json"):
        assert _label(json.loads(f.read_text()).get("provenance")) in LABELS, f.name
