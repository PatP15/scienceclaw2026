"""Simulation-derived crumb specimens: kinetics summary -> CrumbParams -> STL (morphology_map)."""

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from fermentai.geometry.crumb import CrumbParams, generate_crumb
from fermentai.geometry.morphology_map import (
    FALLBACK_INPUTS,
    INPUT_FIELDS,
    PRINTABLE_MIN_WALL_MM,
    MorphologyInputs,
    generate_chain,
    kinetics_inputs,
    map_to_crumb_params,
)

ROOT = Path(__file__).resolve().parents[1]
PRED = ROOT / "data" / "predictions"
BASE = {**FALLBACK_INPUTS, "overproof_flag": False}


def _inp(**kw):
    return MorphologyInputs.from_summary({**BASE, **kw}, chamber_id="T")


def _p(**kw):
    return map_to_crumb_params(_inp(**kw))


# ----------------------------------------------------------------------------- inputs


@pytest.mark.parametrize("cid", ["A", "B"])
def test_from_json_uses_window_end(cid):
    path = PRED / f"chamber_{cid}_morphology.json"
    d = json.loads(path.read_text())["at_window_end"]
    x = MorphologyInputs.from_json(path)
    assert x.chamber_id == cid and x.t_end_h == 16.0 and x.fallback_fields == ()
    for k in INPUT_FIELDS:
        assert getattr(x, k) == pytest.approx(d[k])


def test_missing_fields_fall_back_safely():
    x = MorphologyInputs.from_summary({}, chamber_id="Z")
    assert set(x.fallback_fields) == set(INPUT_FIELDS)
    p, rep = map_to_crumb_params(x)
    assert all(math.isfinite(float(v)) for v in dataclasses.asdict(p).values()
               if isinstance(v, (int, float)))
    assert rep.fallback_fields and any("fallback" in n for n in rep.notes)

    bad = {**BASE, "final_ph": float("nan"), "gluten_weakening_index": None,
           "max_expansion_rate_per_h": "fast", "expansion_ratio_final": None,
           "expansion_ratio_peak": 2.0, "expansion_fall_from_peak_fraction": 0.25,
           "overproof_flag": None}
    x = MorphologyInputs.from_summary(bad, chamber_id="Z")
    assert {"final_ph", "gluten_weakening_index", "max_expansion_rate_per_h",
            "expansion_ratio_final", "overproof_flag"} <= set(x.fallback_fields)
    assert x.expansion_ratio_final == pytest.approx(2.0 - 0.25 * 1.0)  # derived from peak+fall
    assert x.overproof_flag is True  # derived: >= 5 % of the rise lost
    assert x.final_ph == FALLBACK_INPUTS["final_ph"]
    map_to_crumb_params(x)  # no exception


def test_missing_window_in_json(tmp_path):
    p = tmp_path / "chamber_Q_morphology.json"
    p.write_text(json.dumps({"chamber_id": "Q", "at_window_start": {}}))
    x = MorphologyInputs.from_json(p)
    assert x.chamber_id == "Q" and set(x.fallback_fields) == set(INPUT_FIELDS)


# ----------------------------------------------------------------------------- mapping


# (input field, low value, high value, {output: +1 increases / -1 decreases})
MONOTONE = [
    ("expansion_ratio_final", 1.4, 2.4, {"porosity_target": +1}),
    ("co2_retained_g_per_kg", 0.8, 2.2, {"porosity_target": +1}),
    ("max_expansion_rate_per_h", 0.12, 0.33,
     {"pore_diam_median_mm": +1, "eccentricity": +1, "eccentricity_jitter": +1}),
    ("gluten_weakening_index", 0.1, 0.9,
     {"pore_diam_median_mm": +1, "eccentricity": +1, "min_wall_mm": -1}),
    ("expansion_fall_from_peak_fraction", 0.02, 0.35,
     {"pore_diam_median_mm": +1, "clustering": +1, "pore_diam_cv": +1,
      "pore_diam_max_mm": +1, "cluster_radius_mm": +1}),
    ("overproof_flag", False, True, {"clustering": +1, "pore_diam_median_mm": +1}),
    ("final_ph", 4.8, 3.7, {"min_wall_mm": -1}),  # lower pH -> thinner walls
]


@pytest.mark.parametrize("fld,lo,hi,expect", MONOTONE)
def test_mapping_direction(fld, lo, hi, expect):
    _, r_lo = _p(**{fld: lo})
    _, r_hi = _p(**{fld: hi})
    for out, sign in expect.items():
        a, b = r_lo.outputs[out]["value"], r_hi.outputs[out]["value"]
        assert sign * (b - a) > 0, (fld, out, a, b)


@pytest.mark.parametrize("fld,lo,hi,expect", [m for m in MONOTONE if m[0] != "overproof_flag"])
def test_mapping_monotone_over_range(fld, lo, hi, expect):
    """Non-strict monotonicity over a sweep that extends past the clipping points."""
    span = hi - lo
    grid = np.linspace(lo - 2 * span, hi + 2 * span, 41)
    for out, sign in expect.items():
        vals = [_p(**{fld: float(v)})[1].outputs[out]["value"] for v in grid]
        assert np.all(sign * np.diff(vals) >= -1e-9), (fld, out)


def test_bounds_random_and_extreme_inputs():
    rng = np.random.default_rng(1)
    for _ in range(300):
        kw = {
            "expansion_ratio_final": rng.uniform(0.5, 6), "expansion_ratio_peak": rng.uniform(0.5, 6),
            "co2_retained_g_per_kg": rng.uniform(-1, 10), "max_expansion_rate_per_h": rng.uniform(-1, 3),
            "gluten_weakening_index": rng.uniform(-1, 2), "final_ph": rng.uniform(2, 7),
            "expansion_fall_from_peak_fraction": rng.uniform(-1, 2),
            "overproof_flag": bool(rng.integers(2)),
        }
        p, rep = _p(**kw)
        assert 0.20 <= p.porosity_target <= 0.50
        assert 0.9 <= rep.outputs["pore_diam_median_mm"]["value"] <= 3.2
        assert p.min_wall_mm >= PRINTABLE_MIN_WALL_MM
        assert p.min_wall_mm <= 1.5 + 1e-9
        assert 0 <= p.clustering <= 1 and 0.1 <= p.eccentricity <= 0.8 + 1e-9
        assert p.pore_diam_min_mm < p.pore_diam_mean_mm < p.pore_diam_max_mm <= 12.0
        assert p.pore_diam_sd_mm > 0


def test_config_override_and_wall_floor():
    _, rep = _p()
    p2, _ = map_to_crumb_params(_inp(), config={"porosity_max": 0.6, "wall_floor": 0.5})
    assert p2.min_wall_mm >= PRINTABLE_MIN_WALL_MM  # floor can never go below printable
    assert p2.porosity_target > rep.outputs["porosity_target"]["value"]
    with pytest.raises(KeyError):
        map_to_crumb_params(_inp(), config={"no_such_coeff": 1})


def test_determinism_and_report_provenance():
    x = MorphologyInputs.from_json(PRED / "chamber_A_morphology.json")
    p1, r1 = map_to_crumb_params(x, seed=5)
    p2, r2 = map_to_crumb_params(x, seed=5)
    assert p1 == p2 and r1.to_dict() == r2.to_dict() and p1.seed == 5
    for name, o in r1.outputs.items():
        assert set(o["provenance_chain"]) == {"alchemi", "simulated", "assumption"}
        if name != "seed":
            assert o["formula"] and o["provenance_chain"]["simulated"]
            assert o["provenance_chain"]["alchemi"] == []  # literature JSON: no ALCHEMI
    for k, v in r1.config.items():
        assert v["source"], k
    json.dumps(r1.to_dict())  # serialisable
    # geometry determinism on a small slab built from the mapped parameters
    small = dataclasses.replace(p1, width_mm=24, depth_mm=24, thickness_mm=8, base_mm=3,
                                voxel_mm=0.4, pore_diam_max_mm=6.0)
    a, b = generate_crumb(small), generate_crumb(small)
    assert np.array_equal(a.vertices, b.vertices) and np.array_equal(a.faces, b.faces)


def test_chambers_differ_in_mapped_params():
    pa, _ = map_to_crumb_params(MorphologyInputs.from_json(PRED / "chamber_A_morphology.json"))
    pb, _ = map_to_crumb_params(MorphologyInputs.from_json(PRED / "chamber_B_morphology.json"))
    assert pa.porosity_target - pb.porosity_target > 0.02  # A retains more gas at 16 h
    assert pb.pore_diam_mean_mm - pa.pore_diam_mean_mm > 0.5  # B: faster, weaker, collapsed
    assert pb.clustering > pa.clustering and pb.eccentricity > pa.eccentricity
    assert pb.min_wall_mm < pa.min_wall_mm


def test_kinetics_rerun_reproduces_json_and_alchemi_variant():
    k = kinetics_inputs("A", PRED / "chamber_A_morphology.json")
    assert k["error"] is None
    assert k["reproduces_json"]["ok"]
    alt = k["alchemi_informed"]
    if alt is not None:  # descriptor file present
        assert alt.kinetics_variant == "alchemi_informed"
        _, rep = map_to_crumb_params(alt)
        assert rep.outputs["porosity_target"]["provenance_chain"]["alchemi"]


def test_stl_facet_normals_match_winding_without_simplify():
    """Regression: crumb._clean used to leave stale (inverted) cached normals when the mesh was
    not rebuilt by the simplifier, which inverted STL facet normals and the oblique render."""
    p = CrumbParams(name="small", width_mm=24, depth_mm=24, thickness_mm=8, base_mm=3,
                    voxel_mm=0.4, seed=7, simplify_tolerance_mm=0)
    m = generate_crumb(p)
    c = np.cross(m.triangles[:, 1] - m.triangles[:, 0], m.triangles[:, 2] - m.triangles[:, 0])
    c /= np.linalg.norm(c, axis=1, keepdims=True)
    assert np.all(np.einsum("ij,ij->i", c, m.face_normals) > 0.99)
    assert m.volume > 0


# ----------------------------------------------------------------------------- specimens


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    out = tmp_path_factory.mktemp("stl")
    return out, generate_chain(("A", "B"), PRED, out, verbose=False)


def test_specimens_validate(chain):
    _, res = chain
    for sp in res["specimens"]:
        failed = {k: v.value for k, v in sp.validation.checks.items() if v.passed is False}
        assert sp.validation.passed, (sp.chamber_id, failed)
        for key in ("watertight", "winding_consistent", "positive_volume", "self_intersections",
                    "single_component", "min_thickness", "enclosed_cavities", "dimensions"):
            assert sp.validation.checks[key].passed is True
        assert len(sp.mesh.faces) * 50 < 25e6  # binary STL size budget


def test_specimen_files_and_chain_summary(chain):
    out, res = chain
    for cid in "AB":
        stl = out / f"crumb_chamber_{cid}.stl"
        assert stl.exists() and (out / f"crumb_chamber_{cid}_validation.json").exists()
        rep = json.loads((out / f"crumb_chamber_{cid}_mapping.json").read_text())
        assert rep["chamber_id"] == cid and rep["provenance"] == "simulated"
        m = trimesh.load(stl)
        assert m.is_watertight and np.allclose(m.extents, [80, 80, 12], atol=1.0)
    assert res["render"].exists() and res["render"].stat().st_size > 50_000
    cs = json.loads(res["chain_summary"].read_text())
    assert set(cs["chambers"]) == {"A", "B"}
    for cid, c in cs["chambers"].items():
        assert c["validation_passed"] is True and len(c["stl_sha256"]) == 64
        assert c["kinetics"]["provenance"] == "simulated"
        assert c["kinetics"]["literature_rerun_reproduces_json"]["ok"]
        assert c["kinetics"]["summary_used"]["final_ph"] > 3
        assert "attribution" in c["alchemi"]


def test_specimens_differ_on_mesh(chain):
    _, res = chain
    a, b = (sp.summary for sp in res["specimens"])
    assert a["cut_face_pore_area_fraction"] - b["cut_face_pore_area_fraction"] > 0.02
    assert b["cut_face_eq_diameter_mm"]["mean"] > 1.1 * a["cut_face_eq_diameter_mm"]["mean"]
    assert b["cut_face_pore_count"] < a["cut_face_pore_count"]
    for s, sp in zip((a, b), res["specimens"], strict=True):
        assert abs(s["cut_face_pore_area_fraction"] - sp.params.porosity_target) < 0.03
