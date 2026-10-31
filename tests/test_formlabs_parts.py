"""Generic parts path: job files, STL inspection, and the per-resin jobs through the MOCK backend
(replay of the recorded live 'hackathon_by_resin' session — no PreFormServer binary is used)."""

import json

import pytest

from fermentai.formlabs.parts import inspect_stl, load_job, parse_part_arg
from fermentai.formlabs.prepare import REPO_ROOT, PartSpec, expand_parts

JOBS = REPO_ROOT / "data" / "stl" / "print" / "jobs"
CLEAR, BLACK, SPLIT = (JOBS / "clear_form4.yaml", JOBS / "black_form4.yaml",
                       JOBS / "clear_form4_split.yaml")
LABELS = {"real", "measured", "simulated", "replay", "estimated", "imported_human"}


def _label(v):
    return v.split(":", 1)[0].strip() if isinstance(v, str) else None


def test_resin_job_files():
    clear, black, split = load_job(CLEAR), load_job(BLACK), load_job(SPLIT)
    assert (clear["profile"], black["profile"]) == ("form4-clear-v5", "form4-black-v5")
    assert [i.key for i in expand_parts(clear["parts"])] == ["A", "B", "hat_1", "hat_2"]
    assert [i.key for i in expand_parts(black["parts"])] == ["clawd_1", "clawd_2"]
    a, b, hat = clear["parts"]
    assert (a.label, b.label) == ("mild, 22 °C", "sour, 27 °C")
    assert a.orientation == "open_face_up" and a.supports == "none" and a.repair == "error"
    assert a.sha256_expected and a.sha256_matches is True  # pulled from chain_summary.json
    assert hat.orientation == "upright" and hat.supports == "mini_raft"
    assert hat.repair == "error_then_repair" and hat.provenance == "imported_human"
    assert split["role"] == "alternative" and split["parts"][0].supports == "raft"
    assert clear["build_name_template"].format(name="clear_form4", k=1, profile="x") == \
        "clear_form4_build1"


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
    with pytest.raises(ValueError):
        PartSpec(name="x", stl=f, supports="glue")


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
    argv = ["prepare", "--job", str(CLEAR), "--job", str(BLACK), "--job", str(SPLIT),
            "--name", "hackathon_by_resin", "--backend", "mock", "--out", str(out)]
    assert main(argv) == 0
    return out


def _m(out, name):
    return json.loads((out / f"{name}_manifest.json").read_text())


def test_mock_clear_fits_one_plate_black_one_plate(mock_job):
    clear = json.loads((mock_job / "clear_form4_jobs.json").read_text())
    black = json.loads((mock_job / "black_form4_jobs.json").read_text())
    for doc in (clear, black):
        assert doc["provenance"] == "replay" and doc["source"].startswith("mock")
        assert doc["job_submitted"] is False and len(doc["builds"]) == 1
    assert sorted(clear["builds"][0]["placed"]) == ["A", "B", "hat_1", "hat_2"]
    assert clear["material"]["code"] == "FLGPCL05"
    assert sorted(black["builds"][0]["placed"]) == ["clawd_1", "clawd_2"]
    assert black["material"]["code"] == "FLGPBK05"
    m = _m(mock_job, "clear_form4_build1")
    assert m["material"]["code"] == "FLGPCL05" and m["printer"]["machine_type"] == "FORM-4-0"
    assert m["supports"]["per_model"]["A"].startswith("none")
    assert all(v["open_face_up"] for k, v in m["orientation"]["final"].items() if k in "AB")
    assert all(inp["scaled"] is False for inp in m["inputs"])
    assert _m(mock_job, "black_form4_build1")["material"]["code"] == "FLGPBK05"


def test_mock_clear_split_alternative_needs_two_builds(mock_job):
    split = json.loads((mock_job / "clear_form4_split_jobs.json").read_text())
    assert split["role"] == "alternative" and len(split["builds"]) == 2
    m1 = _m(mock_job, "clear_form4_split_build1")
    attempts = m1["layout"]["attempts"]
    assert attempts[0]["fit"] is False and attempts[-1]["fit"] is True
    assert m1["job"]["deferred_to_next_build"]


def test_mock_print_sheet_and_provenance_of_every_json(mock_job):
    sheet = (mock_job / "PRINT_SHEET.md").read_text()
    assert "Nothing has been sent to a printer" in sheet
    for f in ("clear_form4_build1.form", "black_form4_build1.form", "FLGPCL05", "FLGPBK05",
              "ALTERNATIVE", "directly on the build platform", "yellows"):
        assert f in sheet, f
    assert "not food-safe" in sheet and "IPA" in sheet
    for f in mock_job.glob("*.json"):
        assert _label(json.loads(f.read_text()).get("provenance")) in LABELS, f.name


def test_committed_fit_study_is_live_and_found_a_fit():
    doc = json.loads((REPO_ROOT / "data/stl/print/clear_fit_study/clear_fit_attempts.json")
                     .read_text())
    assert _label(doc["provenance"]) == "real"
    fits = {a["attempt"]: a["fit"] for a in doc["attempts"]}
    assert fits["1_default_full_rafts"] is False
    assert fits["4_slabs_on_platform_hats_upright"] is True
