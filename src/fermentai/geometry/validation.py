"""Printability validation for crumb meshes (Formlabs resin SLA).

Every check records *how* it was evaluated and whether it is exact or approximate:

* watertight            exact   every edge shared by exactly two faces (trimesh).
* winding_consistent    exact   adjacent faces have consistent orientation (trimesh).
* positive_volume       exact   signed volume > 0 and trimesh ``is_volume``.
* manifold3d_valid      exact   manifold3d accepts the mesh as a 2-manifold (status NoError);
                                genus and volume agreement reported.
* self_intersections    near-exact (best effort): every pair of triangles with overlapping
                        bounding boxes (rtree) that shares no vertex is tested with exact-ish
                        segment/triangle tests (each edge of A vs B and of B vs A, eps 1e-9).
                        Pairs sharing a vertex and coplanar overlaps are NOT tested.
* single_component      exact   one face-connected component (no disconnected fragments).
* min_thickness         approximate. (a) Re-voxelise the mesh by ray parity (independent of the
                        generator) at ``pitch_mm``, morphologically open the solid with a ball
                        of radius ``min_thickness/2 - pitch`` and measure the solid volume that
                        does not fit such a ball (thin features). Pass if < ``thin_tol``.
                        (b) Informational: inward ray thickness at random surface samples
                        (underestimates near edges/corners, so not used for pass/fail).
* enclosed_cavities     exact topological argument + approximate voxel check. A single
                        watertight, consistently oriented connected surface bounds exactly one
                        solid with no internal cavity (Jordan-Brouwer), so an enclosed void would
                        need a second shell. Independently, empty voxels of the re-voxelisation
                        are labelled (6-connectivity) and any region not connected to the outside
                        is reported as a cavity.
* dimensions            exact   bounding box vs. expected slab size (tolerance 1 mm).
* voxel_volume_agreement approximate: parity-voxel volume vs mesh volume (within 3 %) — a
                        sanity check that the re-voxelisation used above is trustworthy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage

from .mesh_raster import voxelize_parity


@dataclass
class Check:
    passed: bool | None
    value: object
    method: str
    exact: bool
    note: str = ""


@dataclass
class ValidationReport:
    name: str
    min_thickness_mm: float
    checks: dict[str, Check] = field(default_factory=dict)
    provenance: str = "simulated"

    @property
    def passed(self) -> bool:
        return all(c.passed is not False for c in self.checks.values())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provenance": self.provenance,
            "passed": self.passed,
            "min_thickness_mm": self.min_thickness_mm,
            "checks": {k: asdict(v) for k, v in self.checks.items()},
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(_jsonable(self.to_dict()), indent=2))


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return o


# ----------------------------------------------------------------------------- self-intersection


def _seg_tri(p0, p1, a, b, c, eps=1e-9):
    """Vectorised Moller-Trumbore: does segment p0->p1 cross triangle abc (strict interior)?"""
    d = p1 - p0
    e1, e2 = b - a, c - a
    h = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(det) > 1e-15
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = p0 - a
    u = inv * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    v = inv * np.einsum("ij,ij->i", d, q)
    t = inv * np.einsum("ij,ij->i", e2, q)
    return ok & (u > eps) & (v > eps) & (u + v < 1 - eps) & (t > eps) & (t < 1 - eps)


def count_self_intersections(mesh: trimesh.Trimesh, max_pairs_chunk: int = 500_000) -> dict:
    import rtree

    tris = np.asarray(mesh.triangles, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    prop = rtree.index.Property()
    prop.dimension = 3
    idx = rtree.index.Index(
        ((i, (*lo[i], *hi[i]), None) for i in range(len(tris))), properties=prop
    )
    ids, counts = idx.intersection_v(lo, hi)
    a = np.repeat(np.arange(len(tris)), counts.astype(np.int64))
    b = ids.astype(np.int64)
    m = a < b
    a, b = a[m], b[m]
    fa, fb = faces[a], faces[b]
    share = (fa[:, :, None] == fb[:, None, :]).any(axis=(1, 2))
    a, b = a[~share], b[~share]
    n_hits = 0
    examples: list[list[int]] = []
    for s in range(0, len(a), max_pairs_chunk):
        ta, tb = tris[a[s : s + max_pairs_chunk]], tris[b[s : s + max_pairs_chunk]]
        hit = np.zeros(len(ta), dtype=bool)
        for X, Y in ((ta, tb), (tb, ta)):
            for i, j in ((0, 1), (1, 2), (2, 0)):
                hit |= _seg_tri(X[:, i], X[:, j], Y[:, 0], Y[:, 1], Y[:, 2])
        n_hits += int(hit.sum())
        if hit.any() and len(examples) < 5:
            k = np.nonzero(hit)[0][: 5 - len(examples)]
            examples += [[int(a[s + x]), int(b[s + x])] for x in k]
    return {"pairs_tested": len(a), "intersecting_pairs": n_hits, "examples": examples}


# ----------------------------------------------------------------------------- thickness / voids


def _ray_thickness(mesh: trimesh.Trimesh, n: int, seed: int) -> dict:
    pts, fidx = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nrm = mesh.face_normals[fidx]
    origins = pts - nrm * 1e-4
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    dist = np.full(n, np.nan)
    dist[ray_idx] = np.linalg.norm(locs - origins[ray_idx], axis=1)
    d = dist[np.isfinite(dist)]
    if len(d) == 0:
        return {"samples": n, "hits": 0}
    return {
        "samples": n,
        "hits": len(d),
        "p01_mm": float(np.percentile(d, 1)),
        "p05_mm": float(np.percentile(d, 5)),
        "median_mm": float(np.median(d)),
    }


def validate_mesh(
    mesh: trimesh.Trimesh,
    min_thickness_mm: float,
    name: str = "mesh",
    expected_dims_mm: tuple[float, float, float] | None = (80.0, 80.0, 12.0),
    pitch_mm: float = 0.15,
    thin_tol: float = 0.01,
    ray_samples: int = 2000,
    seed: int = 0,
) -> ValidationReport:
    rep = ValidationReport(name=name, min_thickness_mm=min_thickness_mm)
    C = rep.checks

    C["watertight"] = Check(bool(mesh.is_watertight), bool(mesh.is_watertight),
                            "every edge shared by exactly 2 faces (trimesh)", True)
    C["winding_consistent"] = Check(bool(mesh.is_winding_consistent),
                                    bool(mesh.is_winding_consistent),
                                    "consistent face orientation (trimesh)", True)
    vol = float(mesh.volume)
    C["positive_volume"] = Check(bool(mesh.is_volume and vol > 0), round(vol, 3),
                                 "signed volume (mm^3) > 0 and trimesh is_volume", True)

    try:
        import manifold3d

        man = manifold3d.Manifold(manifold3d.Mesh(
            vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint32)))
        status = str(man.status())
        ok = man.status() == manifold3d.Error.NoError
        mvol = float(man.volume()) if ok else None
        C["manifold3d_valid"] = Check(
            bool(ok and abs(mvol - vol) / vol < 1e-3),
            {"status": status, "genus": int(man.genus()) if ok else None, "volume": mvol},
            "manifold3d.Manifold accepts mesh as 2-manifold; volume agrees with trimesh", True)
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        C["manifold3d_valid"] = Check(None, repr(e), "manifold3d unavailable", True)

    si = count_self_intersections(mesh)
    C["self_intersections"] = Check(
        si["intersecting_pairs"] == 0, si,
        "rtree AABB candidate pairs (no shared vertex) + segment/triangle tests", False,
        "best effort: vertex-sharing pairs and coplanar overlaps are not tested")

    comps = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)))
    C["single_component"] = Check(len(comps) == 1, len(comps),
                                  "face-adjacency connected components", True)

    # Independent re-voxelisation of the mesh.
    occ, _ = voxelize_parity(mesh, pitch_mm)
    vvol = float(occ.sum() * pitch_mm**3)
    rel = abs(vvol - vol) / vol
    C["voxel_volume_agreement"] = Check(bool(rel < 0.03), {"voxel_volume": round(vvol, 2),
                                        "relative_error": round(rel, 5), "pitch_mm": pitch_mm},
                                        "parity re-voxelisation volume vs mesh volume", False)

    r_vox = max(min_thickness_mm / 2.0 / pitch_mm - 1.0, 0.5)
    eroded = ndimage.distance_transform_edt(occ) > r_vox
    opened = ndimage.distance_transform_edt(~eroded) <= r_vox
    thin = occ & ~opened
    thin_frac = float(thin.sum() / max(occ.sum(), 1))
    # Thickness of the thinnest region that *survives* in each thin cluster (diagnostic).
    lab, n_thin = ndimage.label(thin)
    big_thin = 0
    if n_thin:
        sizes = np.bincount(lab.ravel())[1:]
        big_thin = int((sizes * pitch_mm**3 > 1.0).sum())  # thin clusters larger than 1 mm^3
    C["min_thickness"] = Check(
        bool(thin_frac < thin_tol),
        {"thin_volume_fraction": round(thin_frac, 6), "thin_clusters_gt_1mm3": big_thin,
         "ball_radius_mm": round(r_vox * pitch_mm, 4), "pitch_mm": pitch_mm,
         "ray_thickness_informational": _ray_thickness(mesh, ray_samples, seed)},
        f"solid volume not covered by inscribed balls of diameter ~{min_thickness_mm} mm "
        f"(minus 1 voxel tolerance) on a {pitch_mm} mm re-voxelisation; pass if < {thin_tol:.0%}",
        False, "approximate (voxel resolution); ray thickness is informational only")

    lab, n = ndimage.label(~np.pad(occ, 1, constant_values=False))
    outside = lab[0, 0, 0]
    n_cav = n - 1
    cav_vol = float(((lab != outside) & (lab > 0)).sum() * pitch_mm**3)
    topo_ok = bool(mesh.is_watertight and mesh.is_winding_consistent and len(comps) == 1)
    C["enclosed_cavities"] = Check(
        bool(n_cav == 0 and topo_ok),
        {"voxel_enclosed_regions": int(n_cav), "voxel_enclosed_volume_mm3": cav_vol,
         "topological_single_shell": topo_ok},
        "single watertight shell => no internal cavity (exact); voxel void labelling (approx.)",
        False, "a cavity smaller than the voxel pitch could be missed by the voxel check, "
               "but not by the topological argument")

    if expected_dims_mm is not None:
        ext = mesh.extents
        ok = bool(np.all(np.abs(ext - np.array(expected_dims_mm)) <= 1.0))
        C["dimensions"] = Check(ok, [round(float(e), 3) for e in ext],
                                f"bounding box extents vs {expected_dims_mm} mm (+-1 mm)", True)
    return rep
