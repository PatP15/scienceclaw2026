"""Stochastic, seeded, parameterized open-faced bread-crumb cross-section models.

Pipeline (voxel/implicit approach, chosen for robustness over CSG):

1. A node grid (``voxel_mm`` spacing) covers the slab footprint. The footprint is either a
   rectangle or a loaf-shaped outline (flat-ish bottom, domed top) inside the 80 x 80 mm box.
2. Ellipsoidal pores are placed one by one until the pore-area fraction on the *cut face*
   (the top plane) reaches ``porosity_target``. Pore sizes are lognormal (equivalent-sphere
   diameter, mm), elongated by ``eccentricity`` with random 3D orientation, and positioned by
   a Thomas/Matern-like cluster process: with probability ``clustering`` a pore is a child of
   a randomly chosen cluster parent, otherwise it is uniform in the crumb region.
   Every pore is centred near the top plane so that it is *cut open* by it, exactly like a
   bubble sliced by a bread knife. Pores never go below a solid floor (``base_mm``) and
   never enter the crust rim (``crust_mm``).
3. Minimum wall thickness is enforced by a morphological *opening* of the solid with a ball of
   radius ``min_wall_mm / 2`` (exact, via Euclidean distance transforms): every remaining solid
   point lies inside a fully solid ball of diameter ``min_wall_mm``. Walls thinner than that
   are removed, so neighbouring pores coalesce (which is also what happens in real crumb).
4. Disconnected solid fragments are dropped (largest 6-connected component kept) and any void
   not connected to the outside (6-connectivity, conservative) is filled, so no sealed cavity
   can trap uncured resin.
5. The field is lightly Gaussian-smoothed and meshed with marching cubes (skimage), then
   cleaned with trimesh and decimated with ``manifold3d.Manifold.simplify`` which preserves
   manifoldness (the surface moves by less than ``simplify_tolerance_mm``).

IMPORTANT: the "mild" vs "sour" presets are ILLUSTRATIVE HYPOTHESES about possible crumb
morphology differences. Sourness does not determine crumb structure, and these geometries are
NOT ALCHEMI (or any other) predictions. Provenance label of every generated model: "simulated".
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial.transform import Rotation
from skimage import measure

PROVENANCE = "simulated"
DISCLAIMER = (
    "Illustrative hypothesis only: sourdough sourness does not determine crumb structure. "
    "These geometries are parameterized stochastic models, not ALCHEMI predictions."
)


@dataclass(frozen=True)
class CrumbParams:
    """Parameters of the stochastic crumb model. All lengths in millimetres."""

    name: str = "crumb"
    # Target pore-area fraction on the cut face (inside the crumb, excluding crust). This is the
    # same quantity measured from loaf photographs by image_analysis.analyze_crumb_image.
    porosity_target: float = 0.30
    # Lognormal distribution of the pores' equivalent-sphere diameter (arithmetic mean / sd).
    pore_diam_mean_mm: float = 1.5
    pore_diam_sd_mm: float = 0.5
    pore_diam_min_mm: float = 0.6
    pore_diam_max_mm: float = 12.0
    # 0 = spherical. Aspect ratio of a prolate pore = 1 + eccentricity * (1 + jitter * N(0,1)).
    eccentricity: float = 0.1
    eccentricity_jitter: float = 0.3
    # Fraction of pores placed in clusters (0 = Poisson/uniform, 1 = fully clustered).
    clustering: float = 0.0
    cluster_radius_mm: float = 4.0  # sd of Gaussian child offsets around a parent
    cluster_spacing_mm: float = 14.0  # mean spacing between cluster parents
    min_wall_mm: float = 0.9
    width_mm: float = 80.0
    depth_mm: float = 80.0  # y extent
    thickness_mm: float = 12.0  # z extent
    base_mm: float = 4.0  # solid floor below all pores
    crust_mm: float = 2.5  # solid rim; 0 disables
    outline: Literal["loaf", "rect"] = "loaf"
    voxel_mm: float = 0.3
    smooth_sigma_vox: float = 0.6
    simplify_tolerance_mm: float = 0.03
    seed: int = 0
    description: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


MILD = CrumbParams(
    name="mild",
    porosity_target=0.28,
    pore_diam_mean_mm=1.4,
    pore_diam_sd_mm=0.45,
    pore_diam_max_mm=4.0,
    eccentricity=0.15,
    eccentricity_jitter=0.3,
    clustering=0.1,
    cluster_radius_mm=3.0,
    min_wall_mm=0.9,
    seed=20261031,
    description="Finer, more uniform, lower porosity (illustrative hypothesis).",
)

SOUR = CrumbParams(
    name="sour",
    porosity_target=0.42,
    pore_diam_mean_mm=2.8,
    pore_diam_sd_mm=1.8,
    pore_diam_max_mm=11.0,
    eccentricity=0.6,
    eccentricity_jitter=0.5,
    clustering=0.6,
    cluster_radius_mm=4.5,
    cluster_spacing_mm=16.0,
    min_wall_mm=0.9,
    seed=20261032,
    description="Larger, more irregular/eccentric, more clustered pores (illustrative hypothesis).",
)

PRESETS: dict[str, CrumbParams] = {"mild": MILD, "sour": SOUR}


@dataclass
class Pore:
    center: np.ndarray  # (3,) mm
    axes: np.ndarray  # (3,) semi-axes mm
    rotation: np.ndarray  # (3,3), columns are the ellipsoid axes
    diameter_mm: float  # equivalent-sphere diameter


@dataclass
class CrumbField:
    params: CrumbParams
    solid: np.ndarray  # bool (nx, ny, nz), node (i,j,k) at ((i+.5)p, (j+.5)p, (k+.5)p)
    footprint: np.ndarray  # bool (nx, ny): inside outline
    crumb_region: np.ndarray  # bool (nx, ny): inside outline and outside crust rim
    pores: list[Pore] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def pitch(self) -> float:
        return self.params.voxel_mm


# ----------------------------------------------------------------------------- outline


def footprint_mask(params: CrumbParams) -> np.ndarray:
    """2D boolean mask (nx, ny) of the slab outline sampled at node centres."""
    p = params.voxel_mm
    nx = round(params.width_mm / p)
    ny = round(params.depth_mm / p)
    x = (np.arange(nx) + 0.5) * p
    y = (np.arange(ny) + 0.5) * p
    X, Y = np.meshgrid(x, y, indexing="ij")
    if params.outline == "rect":
        return np.ones((nx, ny), dtype=bool)
    # Loaf: superellipse with a squarer bottom and a domed top (y is "up" in the slice).
    a = params.width_mm / 2.0
    cy = params.depth_mm * 0.42  # the widest part sits below the middle
    u = np.abs(X - a) / a
    below = Y < cy
    v = np.where(below, (cy - Y) / cy, (Y - cy) / (params.depth_mm - cy))
    n = np.where(below, 5.0, 2.4)
    return (u**n + np.abs(v) ** n) <= 1.0


# ----------------------------------------------------------------------------- pores


def _lognormal_params(mean: float, sd: float) -> tuple[float, float]:
    sigma2 = np.log(1.0 + (sd / mean) ** 2)
    return np.log(mean) - 0.5 * sigma2, np.sqrt(sigma2)


def _sample_pore(params: CrumbParams, rng: np.random.Generator, center_xy: np.ndarray) -> Pore:
    mu, sigma = _lognormal_params(params.pore_diam_mean_mm, params.pore_diam_sd_mm)
    d = float(np.clip(rng.lognormal(mu, sigma), params.pore_diam_min_mm, params.pore_diam_max_mm))
    r = d / 2.0
    aspect = 1.0 + params.eccentricity * max(0.0, 1.0 + params.eccentricity_jitter * rng.normal())
    axes = np.array([r * aspect ** (2 / 3), r * aspect ** (-1 / 3), r * aspect ** (-1 / 3)])
    rot = Rotation.random(random_state=rng).as_matrix()
    # Vertical half-extent of the rotated ellipsoid.
    h = float(np.sqrt(np.sum((rot[2, :] * axes) ** 2)))
    # Centre between 0.15 h above and 0.9 h below the cut plane: always sliced open, mostly
    # through or below the equator (deep bowls with slight undercuts, like a cut bubble).
    zc = params.thickness_mm - rng.uniform(-0.15, 0.9) * h
    return Pore(np.array([center_xy[0], center_xy[1], zc]), axes, rot, d)


def _carve(solid: np.ndarray, pore: Pore, params: CrumbParams, allowed: np.ndarray) -> None:
    p = params.voxel_mm
    nx, ny, nz = solid.shape
    ext = pore.axes.max()
    lo = np.floor((pore.center - ext) / p - 0.5).astype(int)
    hi = np.ceil((pore.center + ext) / p - 0.5).astype(int) + 1
    kmin = int(np.ceil(params.base_mm / p - 0.5))
    lo = np.maximum(lo, [0, 0, kmin])
    hi = np.minimum(hi, [nx, ny, nz])
    if np.any(hi <= lo):
        return
    xs = (np.arange(lo[0], hi[0]) + 0.5) * p - pore.center[0]
    ys = (np.arange(lo[1], hi[1]) + 0.5) * p - pore.center[1]
    zs = (np.arange(lo[2], hi[2]) + 0.5) * p - pore.center[2]
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    pts = np.stack([X, Y, Z], axis=-1) @ pore.rotation  # coordinates in ellipsoid frame
    inside = np.sum((pts / pore.axes) ** 2, axis=-1) <= 1.0
    inside &= allowed[lo[0] : hi[0], lo[1] : hi[1], None]
    solid[lo[0] : hi[0], lo[1] : hi[1], lo[2] : hi[2]] &= ~inside


# ----------------------------------------------------------------------------- morphology


def _open_solid(solid: np.ndarray, radius_vox: float) -> np.ndarray:
    """Exact morphological opening with a ball (distance-transform based). Pads with void."""
    padded = np.pad(solid, 1, constant_values=False)
    eroded = ndimage.distance_transform_edt(padded) > radius_vox
    opened = ndimage.distance_transform_edt(~eroded) <= radius_vox
    return opened[1:-1, 1:-1, 1:-1] & solid


def _largest_component(mask: np.ndarray) -> np.ndarray:
    lab, n = ndimage.label(mask)  # 6-connectivity
    if n <= 1:
        return mask
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def _fill_enclosed_voids(solid: np.ndarray) -> tuple[np.ndarray, int]:
    padded = np.pad(~solid, 1, constant_values=True)
    lab, n = ndimage.label(padded)  # 6-connectivity: diagonal-only links don't count as vents
    outside = lab[0, 0, 0]
    enclosed = (lab != outside) & padded
    n_enclosed = n - 1
    return solid | enclosed[1:-1, 1:-1, 1:-1], n_enclosed


# ----------------------------------------------------------------------------- generation


def generate_crumb_field(params: CrumbParams, calibration_iters: int = 4) -> CrumbField:
    """Build the voxel solid field for ``params`` (deterministic given ``params.seed``).

    The wall-thickness opening merges thin-walled neighbours and therefore increases the
    cut-face pore fraction beyond what was placed. A short deterministic feedback loop lowers
    the internal placement target until the *final* cut-face fraction is close to
    ``porosity_target`` (each iteration restarts from the same seed).
    """
    internal = params.porosity_target
    best: CrumbField | None = None
    for it in range(calibration_iters):
        cf = _generate_once(params, internal)
        cf.stats["calibration_iteration"] = it
        cf.stats["internal_placement_target"] = float(internal)
        err = cf.stats["cut_face_pore_fraction_voxel"] - params.porosity_target
        if best is None or abs(err) < abs(
            best.stats["cut_face_pore_fraction_voxel"] - params.porosity_target
        ):
            best = cf
        if abs(err) < 0.01:
            break
        internal = float(np.clip(internal - err, 0.02, 0.95))
    assert best is not None
    return best


def _generate_once(params: CrumbParams, placement_target: float) -> CrumbField:
    rng = np.random.default_rng(params.seed)
    p = params.voxel_mm
    foot = footprint_mask(params)
    nx, ny = foot.shape
    nz = round(params.thickness_mm / p)
    if params.crust_mm > 0:
        crumb = ndimage.distance_transform_edt(np.pad(foot, 1))[1:-1, 1:-1] * p > params.crust_mm
    else:
        crumb = foot.copy()
    solid = np.repeat(foot[:, :, None], nz, axis=2)

    # Cluster parents (Poisson number, uniform within the crumb region).
    crumb_idx = np.argwhere(crumb)
    area = crumb.sum() * p * p
    n_par = max(1, rng.poisson(area / params.cluster_spacing_mm**2))
    parents = (crumb_idx[rng.integers(len(crumb_idx), size=n_par)] + 0.5) * p

    pores: list[Pore] = []
    n_crumb_top = crumb.sum()
    frac = 0.0
    max_pores = 200_000
    while frac < placement_target and len(pores) < max_pores:
        for _ in range(16):
            if rng.random() < params.clustering:
                xy = parents[rng.integers(n_par)] + rng.normal(0, params.cluster_radius_mm, 2)
            else:
                xy = (crumb_idx[rng.integers(len(crumb_idx))] + rng.random(2)) * p
            pore = _sample_pore(params, rng, xy)
            _carve(solid, pore, params, crumb)
            pores.append(pore)
        frac = 1.0 - (solid[:, :, -1] & crumb).sum() / n_crumb_top
    frac_placed = float(frac)

    solid = _open_solid(solid, params.min_wall_mm / 2.0 / p)
    solid = _largest_component(solid)
    solid, n_filled = _fill_enclosed_voids(solid)

    top_frac = 1.0 - (solid[:, :, -1] & crumb).sum() / n_crumb_top
    stats = {
        "pores_placed": len(pores),
        "cut_face_pore_fraction_after_placement": frac_placed,
        "cut_face_pore_fraction_voxel": float(top_frac),
        "enclosed_voids_filled": int(n_filled),
        "void_fraction_3d_voxel_in_footprint": float(
            1.0 - solid.sum() / (foot.sum() * nz)
        ),
        "grid_shape": [int(nx), int(ny), int(nz)],
    }
    return CrumbField(params, solid, foot, crumb, pores, stats)


def field_to_mesh(cf: CrumbField) -> trimesh.Trimesh:
    p = cf.pitch
    pad = 3
    vol = np.pad(cf.solid.astype(np.float32), pad, constant_values=0.0)
    if cf.params.smooth_sigma_vox > 0:
        vol = ndimage.gaussian_filter(vol, cf.params.smooth_sigma_vox)
    verts, faces, _, _ = measure.marching_cubes(vol, level=0.5, spacing=(p, p, p))
    verts = verts - (pad - 0.5) * p  # node j <-> (j - pad + 0.5) * p
    mesh = trimesh.Trimesh(verts, faces, process=True)
    mesh = _clean(mesh)
    if cf.params.simplify_tolerance_mm > 0:
        mesh = _simplify(mesh, cf.params.simplify_tolerance_mm)
    return mesh


def _clean(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        mesh = max(parts, key=lambda m: abs(m.volume) if m.is_watertight else len(m.faces))
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_normals(mesh)
    if mesh.volume < 0:
        mesh.invert()
    # trimesh.repair.fix_normals can flip the winding without invalidating the cached
    # face_normals (seen with trimesh 5.2): rebuild so normals (and STL facet normals) match it.
    return trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)


def _simplify(mesh: trimesh.Trimesh, tol: float) -> trimesh.Trimesh:
    import manifold3d

    m = manifold3d.Manifold(
        manifold3d.Mesh(
            vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint32),
        )
    )
    if m.status() != manifold3d.Error.NoError:
        return mesh  # keep the un-simplified (already valid) mesh
    out = m.simplify(tol).to_mesh()
    simp = trimesh.Trimesh(
        np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts), process=True
    )
    simp = _clean(simp)
    return simp if simp.is_watertight and simp.is_winding_consistent else mesh


def generate_crumb(params: CrumbParams, return_field: bool = False):
    """Generate the crumb mesh (mm units). Optionally also return the voxel CrumbField."""
    cf = generate_crumb_field(params)
    mesh = field_to_mesh(cf)
    mesh.metadata.update(
        {
            "name": params.name,
            "provenance": PROVENANCE,
            "params": params.to_dict(),
            "generator_stats": cf.stats,
            "disclaimer": DISCLAIMER,
        }
    )
    return (mesh, cf) if return_field else mesh
