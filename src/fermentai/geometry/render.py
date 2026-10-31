"""Headless renders and cut-face statistics for crumb meshes (matplotlib Agg; no GL/window).

Images are produced from a heightmap rasterised from the *mesh* (mesh_raster.heightmap), so
what is rendered is what will be printed (undercuts beneath pore rims are not visible from
above, as in a photo of a cut loaf).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib.colors import LightSource, LinearSegmentedColormap, to_rgb
from scipy import ndimage

from .crumb import DISCLAIMER, CrumbParams
from .image_analysis import analyze_crumb_image
from .mesh_raster import Grid2D, heightmap

SERIES = {"mild": "#2a78d6", "sour": "#eb6834"}  # categorical slots 1, 2
INK, INK2, MUTED, GRID = "#1f1e1c", "#55534c", "#8a887f", "#e4e2dc"
CRUST = to_rgb("#a4652f")
CRUMB_CMAP = LinearSegmentedColormap.from_list(
    "crumb", ["#3b2614", "#7a5532", "#c19a68", "#e9d6ae", "#f8f0de"]
)
HIST_BINS = np.arange(0.0, 10.5, 0.5)
DEPTH_FULL_MM = 4.0  # depth below the cut face that maps to the darkest crumb colour


def depth_colors(depth_mm: np.ndarray) -> np.ndarray:
    """Shared depth->colour mapping (sqrt so shallow pores already read as holes)."""
    t = np.sqrt(np.clip(depth_mm / DEPTH_FULL_MM, 0, 1))
    return CRUMB_CMAP(1.0 - t)


def model_heightmap(mesh: trimesh.Trimesh, pitch: float = 0.2) -> tuple[np.ndarray, Grid2D]:
    lo = np.floor(mesh.bounds[0, :2])
    hi = np.ceil(mesh.bounds[1, :2])
    n = np.round((hi - lo) / pitch).astype(int)
    grid = Grid2D(lo.astype(float), pitch, int(n[0]), int(n[1]))
    return heightmap(mesh, grid), grid


def cut_face_masks(hm: np.ndarray, pitch: float, top_z: float, crust_mm: float,
                   depth_tol_mm: float = 0.2) -> dict:
    foot = np.isfinite(hm)
    if crust_mm > 0:
        crumb = ndimage.distance_transform_edt(np.pad(foot, 1))[1:-1, 1:-1] * pitch > crust_mm
    else:
        crumb = foot
    pores = foot & (np.nan_to_num(hm, nan=top_z) < top_z - depth_tol_mm)
    return {"footprint": foot, "crumb": crumb, "pores": pores & crumb}


def mesh_summary(mesh: trimesh.Trimesh, params: CrumbParams, pitch: float = 0.2) -> dict:
    """Geometric summary statistics measured on the mesh (provenance: simulated)."""
    hm, _grid = model_heightmap(mesh, pitch)
    top = float(mesh.bounds[1, 2])
    masks = cut_face_masks(hm, pitch, top, params.crust_mm)
    cut = analyze_crumb_image(masks["pores"], scale_mm_per_px=pitch, roi=masks["crumb"],
                              provenance="simulated", source=f"crumb_{params.name}.stl heightmap")
    foot_area = float(masks["footprint"].sum() * pitch**2)
    vol = float(mesh.volume)
    depth = top - np.nan_to_num(hm, nan=top)
    return {
        "name": params.name,
        "provenance": "simulated",
        "volume_mm3": vol,
        "surface_area_mm2": float(mesh.area),
        "bounding_box_mm": [[float(v) for v in r] for r in mesh.bounds],
        "extents_mm": [float(v) for v in mesh.extents],
        "triangle_count": len(mesh.faces),
        "vertex_count": len(mesh.vertices),
        "footprint_area_mm2": foot_area,
        "porosity_3d_void_fraction": 1.0 - vol / (foot_area * params.thickness_mm),
        "cut_face_pore_area_fraction": cut["pore_area_fraction"],
        "porosity_target": params.porosity_target,
        "cut_face_pore_count": cut["pore_count"],
        "cut_face_pore_density_per_cm2": cut.get("pore_density_per_cm2"),
        "cut_face_eq_diameter_mm": cut.get("eq_diameter_mm"),
        "cut_face_eccentricity": cut["eccentricity"],
        "mean_pore_depth_mm": float(depth[masks["pores"]].mean()) if masks["pores"].any() else 0,
        "max_pore_depth_mm": float(depth[masks["pores"]].max()) if masks["pores"].any() else 0,
        "_eq_diameters_mm": cut.get("eq_diameters_mm", []),
        "_heightmap": hm,
        "_masks": masks,
        "_pitch": pitch,
        "_mesh": mesh,
    }


# ----------------------------------------------------------------------------- drawing


def _shaded_rgba(hm: np.ndarray, pitch: float, masks: dict, top: float) -> np.ndarray:
    base = np.nanmin(hm)
    z = np.where(np.isfinite(hm), hm, base)
    ls = LightSource(azdeg=315, altdeg=40)
    colors = depth_colors(top - z)
    shade = ls.hillshade(z, vert_exag=1.0, dx=pitch, dy=pitch)
    rgb = colors[..., :3] * (0.45 + 0.55 * shade[..., None])
    rim = masks["footprint"] & ~masks["crumb"]
    rgb[rim] = 0.55 * rgb[rim] + 0.45 * np.array(CRUST) * (0.6 + 0.4 * shade[rim, None])
    rgba = np.concatenate([np.clip(rgb, 0, 1), masks["footprint"][..., None].astype(float)], -1)
    return rgba


def draw_top(ax, s: dict, top: float, label_scale: bool = True) -> None:
    hm, masks, p = s["_heightmap"], s["_masks"], s["_pitch"]
    rgba = _shaded_rgba(hm, p, masks, top)
    ext = [0, hm.shape[0] * p, 0, hm.shape[1] * p]
    ax.imshow(np.transpose(rgba, (1, 0, 2)), origin="lower", extent=ext, interpolation="bilinear")
    ax.set_axis_off()
    ax.set_aspect("equal")
    if label_scale:
        x0, y0 = ext[1] - 12, -2.5
        ax.plot([x0, x0 + 10], [y0, y0], color=INK, lw=2, solid_capstyle="butt")
        ax.text(x0 - 1.0, y0, "10 mm", ha="right", va="center", fontsize=8, color=INK2)
        ax.set_ylim(-4.5, ext[3])


def _view_dir(elev: float, azim: float) -> np.ndarray:
    e, a = np.radians(elev), np.radians(azim)
    return np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])  # toward eye


def draw_oblique(ax, s: dict, top: float, z_exag: float = 1.5, elev: float = 34,
                 azim: float = -58) -> None:
    """Render the actual mesh triangles (back-face culled, Lambert + ambient shading)."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh: trimesh.Trimesh = s["_mesh"]
    tri = np.asarray(mesh.triangles).copy()
    n = mesh.face_normals.copy()
    tri[..., 2] *= z_exag
    n[:, 2] /= z_exag
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    eye = _view_dir(elev, azim)
    vis = n @ eye > -0.05
    tri, n = tri[vis], n[vis]
    cz = tri[..., 2].mean(1) / z_exag
    col = depth_colors(top - cz)[:, :3]
    # Crust: outer side walls and the bottom take the crust colour.
    side = np.abs(n[:, 2]) < 0.35
    p = s["_pitch"]
    masks = s["_masks"]
    rim2d = masks["footprint"] & ~ndimage.binary_erosion(masks["footprint"], iterations=3)
    cx = np.clip((tri[..., 0].mean(1) / p).astype(int), 0, rim2d.shape[0] - 1)
    cy = np.clip((tri[..., 1].mean(1) / p).astype(int), 0, rim2d.shape[1] - 1)
    outer = (side & (rim2d[cx, cy] | ~masks["footprint"][cx, cy])) | (n[:, 2] < -0.9)
    col[outer] = np.array(CRUST)
    light = np.array([-0.45, 0.35, 0.82])
    light /= np.linalg.norm(light)
    lam = np.clip(n @ light, 0, 1)
    shade = np.where(outer, 0.62 + 0.38 * lam, 0.42 + 0.58 * lam)
    rgb = np.clip(col * shade[:, None], 0, 1)
    pc = Poly3DCollection(tri, facecolors=rgb, edgecolors="none", linewidths=0,
                          antialiased=False)
    ax.add_collection3d(pc)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(hi - lo))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.set_proj_type("persp", focal_length=0.6)
    ax.margins(0)


def draw_hist(ax, s: dict, others: list[dict] | None = None) -> None:
    name = s["name"]
    d = np.asarray(s["_eq_diameters_mm"])
    w = np.full(len(d), 100.0 / max(len(d), 1))
    ax.hist(d, bins=HIST_BINS, weights=w, color=SERIES.get(name, "#2a78d6"),
            edgecolor="white", linewidth=1.0)
    for o in others or []:
        od = np.asarray(o["_eq_diameters_mm"])
        ax.hist(od, bins=HIST_BINS, weights=np.full(len(od), 100.0 / max(len(od), 1)),
                histtype="step", color=MUTED, linewidth=1.2, linestyle="--",
                label=f"{o['name']} (for comparison)")
    if others:
        ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper right")
    ax.set_xlim(HIST_BINS[0], HIST_BINS[-1])
    ax.set_xlabel("pore section diameter on cut face (mm)", fontsize=8.5, color=INK2)
    ax.set_ylabel("% of pores", fontsize=8.5, color=INK2)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def params_text(params: CrumbParams, s: dict) -> str:
    eq = s.get("cut_face_eq_diameter_mm") or {}
    return (
        f"MODEL PARAMETERS (seed {params.seed})\n"
        f"cut-face porosity target  {params.porosity_target:.2f}\n"
        f"pore diameter (lognormal) {params.pore_diam_mean_mm:.1f} ± {params.pore_diam_sd_mm:.1f} mm\n"
        f"eccentricity              {params.eccentricity:.2f}\n"
        f"clustering                {params.clustering:.2f}\n"
        f"min wall                  {params.min_wall_mm:.1f} mm\n"
        f"\nMEASURED ON MESH\n"
        f"cut-face pore fraction    {s['cut_face_pore_area_fraction']:.3f}\n"
        f"pores on cut face         {s['cut_face_pore_count']}\n"
        f"section diameter          {eq.get('mean', float('nan')):.2f} mm mean, "
        f"{eq.get('p90', float('nan')):.2f} p90\n"
        f"3D void fraction          {s['porosity_3d_void_fraction']:.3f}\n"
        f"triangles                 {s['triangle_count']:,}"
    )


def _row(fig, gs_row, params, s, others, top, title_suffix=""):
    ax_t = fig.add_subplot(gs_row[0])
    ax_o = fig.add_subplot(gs_row[1], projection="3d")
    sub = gs_row[2].subgridspec(2, 1, height_ratios=[1.0, 1.15], hspace=0.35)
    ax_h = fig.add_subplot(sub[0])
    ax_p = fig.add_subplot(sub[1])
    draw_top(ax_t, s, top)
    draw_oblique(ax_o, s, top)
    draw_hist(ax_h, s, others)
    ax_p.set_axis_off()
    ax_p.text(0, 1, params_text(params, s), va="top", ha="left", family="monospace",
              fontsize=7.6, color=INK2, transform=ax_p.transAxes)
    color = SERIES.get(params.name, INK)
    ax_t.set_title(f"     {params.name.upper()}  ·  top-down (cut face)", loc="left",
                   fontsize=11, color=INK, fontweight="bold", pad=6)
    ax_t.add_patch(plt.Rectangle((0.0, 1.022), 0.028, 0.028, transform=ax_t.transAxes,
                                 color=color, clip_on=False))
    ax_o.set_title(f"oblique view (z × 1.5){title_suffix}", fontsize=9.5, color=INK2, pad=0)
    ax_h.set_title("pore-size distribution", loc="left", fontsize=9.5, color=INK2)
    return ax_t


def render_comparison(items: list[tuple[CrumbParams, dict]], path, top: float = 12.0) -> None:
    """Side-by-side figure: one row per model (top-down | oblique | histogram + params)."""
    n = len(items)
    fig = plt.figure(figsize=(16, 5.3 * n + 1.2), facecolor="white")
    gs = fig.add_gridspec(n, 3, width_ratios=[1.0, 1.25, 0.85], wspace=0.08, hspace=0.18,
                          left=0.03, right=0.98, top=1 - 0.75 / (5.3 * n + 1.2),
                          bottom=0.55 / (5.3 * n + 1.2))
    for i, (params, s) in enumerate(items):
        others = [o for _, o in items if o is not s]
        _row(fig, [gs[i, 0], gs[i, 1], gs[i, 2]], params, s, others, top)
    fig.suptitle("FermentAI crumb cross-section models: mild vs sour (80 × 80 × 12 mm, "
                 "Formlabs resin)", x=0.03, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.text(0.03, 0.012, f"Provenance: simulated (seeded stochastic pore model).  {DISCLAIMER}",
             fontsize=8, color=MUTED, ha="left")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)


def render_single(params: CrumbParams, s: dict, path, top: float = 12.0) -> None:
    fig = plt.figure(figsize=(16, 6.2), facecolor="white")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.25, 0.85], wspace=0.08,
                          left=0.03, right=0.98, top=0.86, bottom=0.08)
    _row(fig, [gs[0, 0], gs[0, 1], gs[0, 2]], params, s, None, top)
    fig.suptitle(f"FermentAI crumb model: {params.name}", x=0.03, ha="left", fontsize=14,
                 color=INK, fontweight="bold")
    fig.text(0.03, 0.015, f"Provenance: simulated.  {DISCLAIMER}", fontsize=8, color=MUTED)
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
