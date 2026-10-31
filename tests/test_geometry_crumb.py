"""Crumb generation + validation tests (P3 / acceptance test 4)."""

import dataclasses

import numpy as np
import pytest
import trimesh

from fermentai.geometry.crumb import MILD, SOUR, CrumbParams, generate_crumb
from fermentai.geometry.render import mesh_summary
from fermentai.geometry.validation import count_self_intersections, validate_mesh

SMALL = CrumbParams(name="small", width_mm=30, depth_mm=30, thickness_mm=8, base_mm=3,
                    pore_diam_mean_mm=1.5, pore_diam_sd_mm=0.6, porosity_target=0.3,
                    voxel_mm=0.35, seed=7)


@pytest.fixture(scope="module")
def models():
    out = {}
    for p in (MILD, SOUR):
        mesh = generate_crumb(p)
        out[p.name] = (p, mesh, mesh_summary(mesh, p))
    return out


def test_determinism_with_seed():
    a = generate_crumb(SMALL)
    b = generate_crumb(SMALL)
    assert np.array_equal(a.vertices, b.vertices)
    assert np.array_equal(a.faces, b.faces)
    c = generate_crumb(dataclasses.replace(SMALL, seed=8))
    assert len(c.faces) != len(a.faces) or not np.array_equal(c.vertices, a.vertices)


@pytest.mark.parametrize("name", ["mild", "sour"])
def test_validation_passes(models, name):
    p, mesh, _ = models[name]
    rep = validate_mesh(mesh, p.min_wall_mm, name=name, ray_samples=300)
    failed = {k: v.value for k, v in rep.checks.items() if v.passed is False}
    assert rep.passed, failed
    for key in ("watertight", "winding_consistent", "positive_volume", "self_intersections",
                "single_component", "min_thickness", "enclosed_cavities", "dimensions"):
        assert rep.checks[key].passed is True


@pytest.mark.parametrize("name", ["mild", "sour"])
def test_size_and_dims(models, name):
    _, mesh, _s = models[name]
    assert np.allclose(mesh.extents, [80, 80, 12], atol=1.0)
    assert len(mesh.faces) * 50 < 25e6  # binary STL size budget
    assert mesh.metadata["provenance"] == "simulated"


def test_models_differ(models):
    m, s = models["mild"][2], models["sour"][2]
    assert s["cut_face_pore_area_fraction"] > m["cut_face_pore_area_fraction"] + 0.08
    assert s["cut_face_eq_diameter_mm"]["mean"] > 1.3 * m["cut_face_eq_diameter_mm"]["mean"]
    assert s["porosity_3d_void_fraction"] > m["porosity_3d_void_fraction"]
    for name in ("mild", "sour"):
        p, _, x = models[name]
        assert abs(x["cut_face_pore_area_fraction"] - p.porosity_target) < 0.04


def test_self_intersection_detector_finds_overlap():
    a = trimesh.creation.box((2, 2, 2))
    b = trimesh.creation.box((2, 2, 2))
    b.apply_translation((0.7, 0.6, 0.5))
    both = trimesh.util.concatenate([a, b])
    assert count_self_intersections(both)["intersecting_pairs"] > 0
    assert count_self_intersections(a)["intersecting_pairs"] == 0


def test_validation_flags_thin_and_cavity():
    thin = trimesh.creation.box((20, 20, 0.3))
    rep = validate_mesh(thin, 0.9, expected_dims_mm=None, ray_samples=50)
    assert rep.checks["min_thickness"].passed is False

    outer = trimesh.creation.box((20, 20, 10))
    inner = trimesh.creation.box((6, 6, 4))
    inner.invert()
    hollow = trimesh.util.concatenate([outer, inner])
    rep = validate_mesh(hollow, 0.9, expected_dims_mm=None, ray_samples=50)
    assert rep.checks["enclosed_cavities"].passed is False
    assert rep.checks["single_component"].passed is False
