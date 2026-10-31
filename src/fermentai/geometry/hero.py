"""Presentation ("hero") renders of crumb STLs: a realistic-looking bread slice, headless.

Rendering is pure numpy/scipy + matplotlib (Agg) — no GL, no windows:

1. The STL is rasterised to a top-surface heightmap (``mesh_raster.heightmap``; 0.05 mm pitch),
   i.e. exactly what will be printed, as seen from above (pore undercuts are not visible).
2. Oblique 3/4 view: the heightmap is rotated by the azimuth and ray-cast orthographically at the
   elevation angle. For each screen column, the projected top ``T = h cos(e)/p + v sin(e)`` is
   accumulated front-to-back with a running maximum; each pixel's first hit is found with one
   vectorised ``searchsorted``. Pixels below the hit cell's top are its viewer-facing wall
   (outer crust wall or the far wall of a pore).
3. Shading: Lambert (directional light) + ambient, an ambient-occlusion-like term from the local
   height deficit (pores are darker than the cut face around them), depth-dependent crumb tone,
   a crust-coloured rim (``crust_mm``), and faint seeded grain noise for a crumb-like texture.

Every image is labelled SIMULATED: these are renders of model geometry, not photographs.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.patches import FancyBboxPatch
from scipy import ndimage

from .render import model_heightmap

ROOT = Path(__file__).resolve().parents[3]
STL_DIR = ROOT / "data" / "stl"

BG = "#f8f5ef"  # very light warm background (drops onto a light slide)
INK, INK2, MUTED = "#1f1e1c", "#4b4944", "#8a877f"
CHAMBER_COLORS = {"A": "#2a78d6", "B": "#eb6834", "mild": "#2a78d6", "sour": "#eb6834"}
BADGE_EDGE, BADGE_FILL, BADGE_INK = "#b7791f", "#fdf3df", "#7a4d0b"

# cut face (cream) -> shadowed pore interior (warm brown)
CRUMB = LinearSegmentedColormap.from_list(
    "crumb_hero", ["#f3e7cc", "#e6d2a8", "#c9a877", "#8e6a43", "#4f3720"])
CRUST_IN, CRUST_OUT = np.array(to_rgb("#a8622b")), np.array(to_rgb("#5e3013"))
LIGHT = np.array([-0.55, -0.35, 0.76])  # from upper-left-front (rotated view frame)
DEPTH_FULL_MM = 4.0


# ----------------------------------------------------------------------------- shading


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _grain(shape: tuple[int, int], seed: int, sigma: float = 0.8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = ndimage.gaussian_filter(rng.normal(size=shape), sigma)
    return g / (g.std() + 1e-12)


def shade_cells(h: np.ndarray, mask: np.ndarray, pitch: float, top_z: float,
                crust_mm: float = 2.5, light: np.ndarray = LIGHT, seed: int = 0) -> dict:
    """Per-cell colours of the top surface. ``h`` is NaN outside ``mask``."""
    hf = np.where(mask, h, np.nanmin(h) if np.isfinite(h).any() else 0.0)
    # normals from a ~0.2 mm smoothed surface: hides the 0.3 mm voxel terraces of the model
    hs = ndimage.gaussian_filter(hf, 0.2 / pitch)
    gx, gy = np.gradient(hs, pitch)
    n = np.stack([-gx, -gy, np.ones_like(hs)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lam = np.clip(n @ _unit(light), 0, 1)
    # ambient occlusion-like: how far below the local (2 mm) mean surface this cell sits
    local = ndimage.gaussian_filter(np.where(mask, hf, top_z), 2.0 / pitch)
    ao = 1.0 - 0.6 * np.clip((local - hf) / 2.5, 0, 1)
    depth = np.clip((top_z - hf) / DEPTH_FULL_MM, 0, 1) ** 0.55
    rgb = CRUMB(depth)[..., :3]
    shade = (0.50 + 0.55 * lam) * ao
    dist = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] * pitch
    rim = mask & (dist <= crust_mm)
    t = np.clip(dist / max(crust_mm, 1e-6), 0, 1)[..., None]
    crust = CRUST_OUT * (1 - t) ** 1.5 + CRUST_IN * (1 - (1 - t) ** 1.5)
    rgb = np.where(rim[..., None], crust, rgb)
    shade = np.where(rim, 0.62 + 0.45 * lam, shade)
    rgb = rgb * shade[..., None] * (1.0 + 0.03 * _grain(h.shape, seed, 0.1 / pitch)[..., None])
    return {"rgb": np.clip(rgb, 0, 1), "rim": rim, "dist": dist, "depth": depth, "ao": ao,
            "hf": hf}


def top_view(hm: np.ndarray, pitch: float, top_z: float, crust_mm: float = 2.5,
             seed: int = 0) -> np.ndarray:
    """Straight top-down RGBA image (row 0 = far/top edge of the slice)."""
    mask = np.isfinite(hm)
    c = shade_cells(hm, mask, pitch, top_z, crust_mm, light=np.array([-0.5, 0.45, 0.75]),
                    seed=seed)
    rgba = np.concatenate([c["rgb"], mask[..., None].astype(float)], -1)
    return np.flipud(np.transpose(rgba, (1, 0, 2)))


def _downsample(img: np.ndarray, k: int) -> np.ndarray:
    """Box-filter RGBA by ``k`` with premultiplied alpha (anti-aliasing)."""
    if k <= 1:
        return img
    H, W = (img.shape[0] // k) * k, (img.shape[1] // k) * k
    x = img[:H, :W].copy()
    x[..., :3] *= x[..., 3:4]
    x = x.reshape(H // k, k, W // k, k, 4).mean(axis=(1, 3))
    a = x[..., 3:4]
    x[..., :3] = np.where(a > 0, x[..., :3] / np.maximum(a, 1e-9), 0)
    return x


def oblique_view(hm: np.ndarray, pitch: float, top_z: float, azim_deg: float = 28.0,
                 elev_deg: float = 42.0, crust_mm: float = 2.5, seed: int = 0,
                 supersample: int = 2) -> np.ndarray:
    """Orthographic 3/4 view RGBA image of the heightfield (viewer in front, low y).

    Ray-cast at ``supersample`` x the heightmap resolution, then box-filtered back down."""
    mask0 = np.isfinite(hm)
    h0 = np.where(mask0, hm, 0.0)
    if supersample > 1:
        h0 = ndimage.zoom(h0, supersample, order=1)
        mask0 = ndimage.zoom(mask0.astype(float), supersample, order=1) > 0.5
        pitch = pitch / supersample
    h = ndimage.rotate(h0, azim_deg, axes=(0, 1), reshape=True, order=1, cval=0.0)
    m = ndimage.rotate(mask0.astype(float), azim_deg, axes=(0, 1), reshape=True, order=1,
                       cval=0.0) > 0.5
    h = np.where(m, h, np.nan)
    m = ndimage.binary_opening(m, iterations=1)
    h = np.where(m, h, np.nan)
    nu, nv = h.shape
    c = shade_cells(h, m, pitch, top_z, crust_mm, seed=seed)

    e = np.radians(elev_deg)
    se, ce = np.sin(e), np.cos(e)
    v = np.arange(nv)[None, :]
    T = np.where(m, h / pitch * ce + v * se, -1.0)
    M = np.maximum.accumulate(T, axis=1)
    off = float(M.max()) + 10.0
    ys = np.arange(0, int(np.ceil(M.max())) + 2, dtype=float)
    flat = (M + np.arange(nu)[:, None] * off).ravel()
    q = (ys[None, :] + np.arange(nu)[:, None] * off)  # (nu, nys)
    idx = np.searchsorted(flat, q.ravel(), side="left").reshape(q.shape)
    vh = idx - np.arange(nu)[:, None] * nv
    ok = (vh >= 0) & (vh < nv)
    vh = np.clip(vh, 0, nv - 1)
    uu = np.broadcast_to(np.arange(nu)[:, None], vh.shape)
    ok &= m[uu, vh] & (vh * se <= ys[None, :] + 0.5)  # below the slab's floor -> background
    gap = T[uu, vh] - ys[None, :]  # pixels below the hit cell's top (in px)
    wall = ok & (gap > se + 0.75)

    rgb = c["rgb"][uu, vh].copy()
    # viewer-facing walls: crust on the outer rim, shadowed crumb inside pores
    wz = np.clip(gap / max(ce, 1e-6) * pitch, 0, None)  # mm below that cell's top
    # only the outermost cells' walls are crust; pore walls next to the crust band are crumb
    rim = c["dist"][uu, vh] <= 0.6
    lam_wall = max(0.0, float(_unit(LIGHT) @ np.array([0.0, -1.0, 0.0])))
    hcell = c["hf"][uu, vh]
    zabs = np.clip(hcell - wz, 0, top_z)
    crust_wall = CRUST_IN * (0.55 + 0.35 * np.clip(zabs / top_z, 0, 1))[..., None] * (
        0.7 + 0.5 * lam_wall)
    inner = CRUMB(np.clip((top_z - zabs) / DEPTH_FULL_MM, 0, 1) ** 0.55)[..., :3] * (
        0.45 + 0.35 * lam_wall) * c["ao"][uu, vh][..., None]
    rgb = np.where((wall & rim)[..., None], crust_wall, rgb)
    rgb = np.where((wall & ~rim)[..., None], inner, rgb)
    alpha = ok.astype(float)
    img = np.concatenate([np.clip(rgb, 0, 1), alpha[..., None]], -1)  # (nu, nys, 4)
    img = np.flipud(np.transpose(img, (1, 0, 2)))  # rows: screen y (top first), cols: u
    rows = np.nonzero(img[..., 3].any(1))[0]
    cols = np.nonzero(img[..., 3].any(0))[0]
    return _downsample(img[rows[0]: rows[-1] + 1, cols[0]: cols[-1] + 1], supersample)


def contact_shadow(img: np.ndarray, pad_px: int, blur_px: float, drop_px: int,
                   strength: float = 0.35) -> np.ndarray:
    """RGBA with a soft shadow under the object (for opaque light backgrounds)."""
    H, W = img.shape[:2]
    a = np.zeros((H + 2 * pad_px, W + 2 * pad_px))
    a[pad_px: pad_px + H, pad_px: pad_px + W] = img[..., 3]
    sh = ndimage.gaussian_filter(np.roll(a, drop_px, axis=0), blur_px) * strength
    out = np.zeros((*a.shape, 4))
    out[..., :3] = 0.25
    out[..., 3] = sh
    obj = np.zeros_like(out)
    obj[pad_px: pad_px + H, pad_px: pad_px + W] = img
    oa = obj[..., 3:4]
    rgb = obj[..., :3] * oa + out[..., :3] * out[..., 3:4] * (1 - oa)
    al = oa + out[..., 3:4] * (1 - oa)
    rgb = np.where(al > 0, rgb / np.maximum(al, 1e-9), 0)
    return np.concatenate([rgb, al], -1)


# ----------------------------------------------------------------------------- data


def _stats_from_json(name: str, stl_dir: Path) -> dict | None:
    """Measured cut-face numbers (porosity, pore count, median section diameter)."""
    if name.startswith("chamber_"):
        p = stl_dir / "chain_summary.json"
        if p.exists():
            ch = json.loads(p.read_text())["chambers"].get(name.split("_", 1)[1])
            if ch:
                mm = ch["measured_on_mesh"]
                return {"porosity": mm["cut_face_pore_area_fraction"],
                        "pores": mm["cut_face_pore_count"],
                        "median_mm": (mm["cut_face_eq_diameter_mm"] or {}).get("median"),
                        "temperature_c": ch.get("temperature_c")}
    else:
        p = stl_dir / "crumb_summary.json"
        if p.exists():
            for mdl in json.loads(p.read_text())["models"]:
                if mdl["name"] == name:
                    return {"porosity": mdl["cut_face_pore_area_fraction"],
                            "pores": mdl["cut_face_pore_count"],
                            "median_mm": (mdl["cut_face_eq_diameter_mm"] or {}).get("median")}
    return None


def _stats_from_mesh(mesh: trimesh.Trimesh, name: str) -> dict:
    from .crumb import CrumbParams
    from .render import mesh_summary

    s = mesh_summary(mesh, CrumbParams(name=name))
    return {"porosity": s["cut_face_pore_area_fraction"], "pores": s["cut_face_pore_count"],
            "median_mm": (s["cut_face_eq_diameter_mm"] or {}).get("median")}


def prepare(name: str, stl_dir: Path = STL_DIR, pitch: float = 0.05, seed: int = 0,
            supersample: int = 2) -> dict:
    """Load ``crumb_<name>.stl`` and build its oblique + top-down images and key numbers."""
    stl = Path(stl_dir) / f"crumb_{name}.stl"
    mesh = trimesh.load(stl, force="mesh")
    hm, _grid = model_heightmap(mesh, pitch)
    top = float(mesh.bounds[1, 2])
    stats = _stats_from_json(name, Path(stl_dir)) or _stats_from_mesh(mesh, name)
    return {"name": name, "stl": stl, "stats": stats,
            "oblique": oblique_view(hm, pitch, top, seed=seed, supersample=supersample),
            "top": top_view(hm, pitch, top, seed=seed), "pitch": pitch}


# ----------------------------------------------------------------------------- layout

CHAMBER_LABEL = {"chamber_A": ("A", "Chamber A · 22 °C · predicted"),
                 "chamber_B": ("B", "Chamber B · 27 °C · predicted"),
                 "mild": ("mild", "Mild preset · illustrative"),
                 "sour": ("sour", "Sour preset · illustrative")}
BADGE_CHAMBER = "SIMULATED — kinetics-derived crumb hypothesis, not a measurement"
BADGE_PRESET = "SIMULATED — illustrative preset, not a prediction or a measurement"


def _numbers(stats: dict) -> str:
    med = stats.get("median_mm")
    return (f"cut-face porosity {100 * stats['porosity']:.0f} %   ·   "
            f"{stats['pores']} pores on the cut face   ·   "
            f"median pore {med:.1f} mm" if med is not None else "")


def _badge(fig, x, y, text, size=13, ha="left"):
    t = fig.text(x, y, text, fontsize=size, color=BADGE_INK, fontweight="bold", ha=ha,
                 va="center",
                 bbox={"boxstyle": "round,pad=0.55,rounding_size=0.8", "fc": BADGE_FILL,
                       "ec": BADGE_EDGE, "lw": 1.6})
    return t


def _place(fig, img, rect, anchor="C"):
    ax = fig.add_axes(rect)
    ax.imshow(img, interpolation="lanczos")
    ax.set_anchor(anchor)
    ax.set_axis_off()
    return ax


def _scalebar(ax, pitch_px_mm: float, img: np.ndarray):
    """10 mm bar under a top-down image (image pixels are ``pitch_px_mm`` mm)."""
    L = 10.0 / pitch_px_mm
    H, W = img.shape[:2]
    y = H + 0.06 * H
    ax.plot([W - 1 - L, W - 1], [y, y], color=INK, lw=3, solid_capstyle="butt", clip_on=False)
    ax.text(W - 1 - L - 0.02 * W, y, "10 mm", ha="right", va="center", fontsize=12,
            color=INK2, clip_on=False)


def _label_block(fig, x, y, key, label, stats, title_size=34, num_size=16):
    color = CHAMBER_COLORS.get(key, INK)
    fig.patches.append(FancyBboxPatch((x, y - 0.006), 0.012, 0.032, boxstyle="square,pad=0",
                                      transform=fig.transFigure, fc=color, ec="none"))
    fig.text(x + 0.02, y + 0.01, label, fontsize=title_size, fontweight="bold", color=INK,
             va="center")
    fig.text(x + 0.0, y - 0.055, _numbers(stats), fontsize=num_size, color=INK2, va="center")


FOOT = ("Render of the printable 80 × 80 × 12 mm crumb model (Formlabs resin, display only). "
        "Numbers measured on the model's cut face.")


def hero_single(spec: dict, path: str | Path) -> None:
    key, label = CHAMBER_LABEL.get(spec["name"], (spec["name"], spec["name"]))
    fig = plt.figure(figsize=(16, 9), dpi=200, facecolor=BG)
    _label_block(fig, 0.045, 0.905, key, label, spec["stats"], title_size=38, num_size=18)
    badge = BADGE_CHAMBER if spec["name"].startswith("chamber_") else BADGE_PRESET
    _badge(fig, 0.0475, 0.765, badge, size=13, ha="left")
    img = contact_shadow(spec["oblique"], 60, 22, 26)
    _place(fig, img, [0.02, 0.075, 0.64, 0.64], anchor="C")
    ax_t = _place(fig, spec["top"], [0.70, 0.2, 0.26, 0.5], anchor="C")
    ax_t.set_title("top-down · cut face", fontsize=15, color=INK2, pad=12)
    _scalebar(ax_t, spec["pitch"], spec["top"])
    fig.text(0.045, 0.035, FOOT + f"  Source: {spec['stl'].name}.", fontsize=11, color=MUTED)
    fig.savefig(path, dpi=200, facecolor=BG)
    plt.close(fig)


def hero_pair(a: dict, b: dict, path: str | Path, header: str | None = None) -> None:
    fig = plt.figure(figsize=(16, 9), dpi=200, facecolor=BG)
    if header:
        fig.text(0.5, 0.955, header, fontsize=17, color=INK2, ha="center", va="center")
    for i, spec in enumerate((a, b)):
        x0 = 0.035 + 0.5 * i
        key, label = CHAMBER_LABEL.get(spec["name"], (spec["name"], spec["name"]))
        _label_block(fig, x0, 0.865, key, label, spec["stats"], title_size=27, num_size=13.5)
        img = contact_shadow(spec["oblique"], 50, 18, 20)
        _place(fig, img, [x0 - 0.025, 0.13, 0.405, 0.64], anchor="W")
        ax_t = _place(fig, spec["top"], [x0 + 0.37, 0.27, 0.075, 0.36], anchor="C")
        ax_t.set_title("top-down", fontsize=12, color=INK2, pad=6)
    is_ch = a["name"].startswith("chamber_")
    _badge(fig, 0.5, 0.075, BADGE_CHAMBER if is_ch else BADGE_PRESET, size=13, ha="center")
    fig.text(0.5, 0.025, FOOT, fontsize=10.5, color=MUTED, ha="center")
    fig.savefig(path, dpi=200, facecolor=BG)
    plt.close(fig)


def render_heroes(stl_dir: str | Path = STL_DIR, out_dir: str | Path | None = None,
                  include_presets: bool = True, pitch: float = 0.05,
                  supersample: int = 2) -> list[Path]:
    """Write hero_chamber_A.png, hero_chamber_B.png, hero_chamber_AB.png (+ hero_mild_sour.png),
    3200 x 1800 px each, from the STLs in ``stl_dir`` (numbers from chain_summary.json /
    crumb_summary.json, else measured on the mesh). ``pitch``/``supersample`` trade quality
    for speed (defaults ~75 s for all four)."""
    stl_dir = Path(stl_dir)
    out_dir = Path(out_dir) if out_dir is not None else stl_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    def prep(n):
        return prepare(n, stl_dir, pitch=pitch, supersample=supersample)

    A, B = prep("chamber_A"), prep("chamber_B")
    paths = [out_dir / "hero_chamber_A.png", out_dir / "hero_chamber_B.png",
             out_dir / "hero_chamber_AB.png"]
    hero_single(A, paths[0])
    hero_single(B, paths[1])
    hero_pair(A, B, paths[2], header="Expected sourdough crumb after 16 h — simulation-derived")
    if include_presets:
        mild, sour = prep("mild"), prep("sour")
        paths.append(out_dir / "hero_mild_sour.png")
        hero_pair(mild, sour, paths[-1], header="Baseline illustrative presets (not predictions)")
    return paths
