"""Crumb image analysis: pore-area fraction and pore-size distribution of loaf cross-sections.

``analyze_crumb_image`` works on real photographs (provenance ``imported_human``) and on
pore masks derived from generated models (pass ``provenance="simulated"``), so measured
bread and generated geometry are summarised with identical code.

Method (photos): grayscale -> light Gaussian blur -> Otsu threshold inside the region of
interest (pores are the darker class by default: cavities are shadowed) -> morphological
opening/closing with a 1 px disk -> removal of objects smaller than ``min_pore_px`` ->
8-connected labelling -> region properties.

Caveats: lighting, crumb colour and focus all affect thresholding; the result is a 2D section
measurement (pore *section* sizes, not 3D bubble sizes). Without ``scale_mm_per_px`` all sizes
are reported in pixels only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage import filters, measure, morphology


def _load_gray(src) -> np.ndarray:
    if isinstance(src, (str, Path)):
        from PIL import Image

        img = np.asarray(Image.open(src).convert("L"), dtype=np.float64) / 255.0
        return img
    arr = np.asarray(src)
    if arr.ndim == 3:
        arr = arr[..., :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    arr = arr.astype(np.float64)
    if arr.max() > 1.0:
        arr = arr / 255.0
    return arr


def _stats(x: np.ndarray) -> dict:
    if len(x) == 0:
        return {"n": 0}
    return {
        "n": len(x),
        "mean": float(np.mean(x)),
        "sd": float(np.std(x, ddof=1)) if len(x) > 1 else 0.0,
        "median": float(np.median(x)),
        "p10": float(np.percentile(x, 10)),
        "p90": float(np.percentile(x, 90)),
        "max": float(np.max(x)),
    }


def segment_pores(
    gray: np.ndarray,
    roi: np.ndarray | None = None,
    pores_are_dark: bool = True,
    min_pore_px: int = 4,
    blur_sigma: float = 1.0,
) -> tuple[np.ndarray, float]:
    """Return (pore_mask, otsu_threshold) for a grayscale image in [0, 1]."""
    roi = np.ones(gray.shape, bool) if roi is None else roi.astype(bool)
    g = ndimage.gaussian_filter(gray, blur_sigma) if blur_sigma > 0 else gray
    thr = float(filters.threshold_otsu(g[roi]))
    pores = (g < thr) if pores_are_dark else (g > thr)
    pores &= roi
    fp = morphology.disk(1)
    pores = ndimage.binary_opening(pores, fp)
    pores = ndimage.binary_closing(pores, fp) & roi
    pores = morphology.remove_small_objects(pores, max_size=max(min_pore_px - 1, 0))
    return pores, thr


def analyze_crumb_image(
    path_or_array,
    scale_mm_per_px: float | None = None,
    roi: np.ndarray | None = None,
    pores_are_dark: bool = True,
    min_pore_px: int = 4,
    provenance: str | None = None,
    source: str | None = None,
) -> dict:
    """Measure pore-area fraction and pore-size distribution.

    ``path_or_array`` may be an image path, a grayscale/RGB array, or a *boolean pore mask*
    (True = pore), in which case segmentation is skipped.
    """
    if provenance is None:
        provenance = "imported_human"
    if source is None:
        source = str(path_or_array) if isinstance(path_or_array, (str, Path)) else "array"
    arr = None if isinstance(path_or_array, (str, Path)) else np.asarray(path_or_array)
    roi_m = None if roi is None else np.asarray(roi, bool)
    if arr is not None and arr.dtype == bool:
        pores = arr & (roi_m if roi_m is not None else True)
        thr, method = None, "precomputed boolean pore mask"
    else:
        gray = _load_gray(path_or_array)
        pores, thr = segment_pores(gray, roi_m, pores_are_dark, min_pore_px)
        method = "gaussian blur + Otsu + 1px open/close + small-object removal"
    roi_m = np.ones(pores.shape, bool) if roi_m is None else roi_m
    lab = measure.label(pores, connectivity=2)
    props = measure.regionprops(lab)
    areas_px = np.array([p.area for p in props], dtype=float)
    eqd_px = np.array([p.equivalent_diameter_area for p in props], dtype=float)
    ecc = np.array([p.eccentricity for p in props], dtype=float)
    out = {
        "provenance": provenance,
        "source": source,
        "method": method,
        "otsu_threshold": thr,
        "image_shape": list(pores.shape),
        "roi_px": int(roi_m.sum()),
        "pore_area_fraction": float(pores.sum() / max(roi_m.sum(), 1)),
        "pore_count": len(props),
        "eq_diameter_px": _stats(eqd_px),
        "eccentricity": _stats(ecc),
        "scale_mm_per_px": scale_mm_per_px,
    }
    if scale_mm_per_px:
        eqd_mm = eqd_px * scale_mm_per_px
        roi_cm2 = roi_m.sum() * scale_mm_per_px**2 / 100.0
        out["eq_diameter_mm"] = _stats(eqd_mm)
        out["pore_area_mm2"] = _stats(areas_px * scale_mm_per_px**2)
        out["pore_density_per_cm2"] = float(len(props) / roi_cm2) if roi_cm2 else None
        out["eq_diameters_mm"] = [round(float(d), 4) for d in eqd_mm]
    return out


def expected_section_diameter_mm(params, n: int = 200_000, seed: int = 0) -> float:
    """Expected mean 2D section diameter for the params' 3D pore-size distribution.

    Stereology for spheres cut by a random plane: intersection probability ~ D, and the mean
    section diameter of a sphere is (pi/4) D, so E[d] = (pi/4) E[D^2] / E[D]. Uses the clipped
    lognormal of the generator. Approximate: ignores eccentricity and pore coalescence.
    """
    from .crumb import _lognormal_params

    rng = np.random.default_rng(seed)
    mu, s = _lognormal_params(params.pore_diam_mean_mm, params.pore_diam_sd_mm)
    D = np.clip(rng.lognormal(mu, s, n), params.pore_diam_min_mm, params.pore_diam_max_mm)
    return float(np.pi / 4 * np.mean(D**2) / np.mean(D))


def compare_to_params(measured: dict, params) -> dict:
    """Compare a measurement (photo or generated) against generator parameters."""
    res = {
        "params_name": getattr(params, "name", None),
        "measured_provenance": measured.get("provenance"),
        "params_provenance": "simulated",
        "pore_area_fraction": {
            "measured": measured["pore_area_fraction"],
            "param_target": params.porosity_target,
            "difference": measured["pore_area_fraction"] - params.porosity_target,
        },
        "notes": [
            "2D section statistics; generator sizes are 3D equivalent-sphere diameters.",
            "Expected section diameter uses sphere stereology and ignores pore coalescence.",
        ],
    }
    if "eq_diameter_mm" in measured and measured["eq_diameter_mm"].get("n", 0):
        exp_d = expected_section_diameter_mm(params)
        m = measured["eq_diameter_mm"]["mean"]
        res["mean_section_diameter_mm"] = {
            "measured": m,
            "expected_from_params": exp_d,
            "ratio": m / exp_d,
        }
        res["suggested_params"] = {
            "porosity_target": round(measured["pore_area_fraction"], 3),
            "pore_diam_mean_mm": round(params.pore_diam_mean_mm * m / exp_d, 3),
        }
    else:
        res["mean_section_diameter_mm"] = None
        res["notes"].append("No mm scale given: pore sizes not compared.")
    return res


def synthetic_crumb_image(
    shape: tuple[int, int] = (400, 400),
    target_fraction: float = 0.3,
    r_range: tuple[float, float] = (4.0, 14.0),
    seed: int = 0,
    noise: float = 0.04,
) -> tuple[np.ndarray, np.ndarray]:
    """Tiny synthetic 'photo': bright crumb with dark circular pores + noise.

    Returns (uint8 image, true pore mask). Provenance of such images: "simulated".
    """
    rng = np.random.default_rng(seed)
    mask = np.zeros(shape, bool)
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    while mask.mean() < target_fraction:
        r = rng.uniform(*r_range)
        cy, cx = rng.uniform(0, shape[0]), rng.uniform(0, shape[1])
        mask |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    img = np.where(mask, 0.25, 0.85) + rng.normal(0, noise, shape)
    img = ndimage.gaussian_filter(img, 0.7)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8), mask
