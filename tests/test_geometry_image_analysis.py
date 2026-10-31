"""Image analysis on a tiny synthetic crumb image with known pore fraction."""

import numpy as np
from PIL import Image

from fermentai.geometry.crumb import MILD
from fermentai.geometry.image_analysis import (
    analyze_crumb_image,
    compare_to_params,
    synthetic_crumb_image,
)


def test_recovers_known_fraction(tmp_path):
    img, mask = synthetic_crumb_image(target_fraction=0.3, seed=1)
    path = tmp_path / "crumb.png"
    Image.fromarray(img).save(path)
    res = analyze_crumb_image(path, scale_mm_per_px=0.1)
    assert res["provenance"] == "imported_human"
    assert abs(res["pore_area_fraction"] - mask.mean()) < 0.02
    assert res["pore_count"] > 5
    assert res["eq_diameter_mm"]["mean"] > 0


def test_array_and_mask_inputs():
    img, mask = synthetic_crumb_image(target_fraction=0.2, seed=2)
    r1 = analyze_crumb_image(img, provenance="simulated")
    assert r1["provenance"] == "simulated"
    assert abs(r1["pore_area_fraction"] - mask.mean()) < 0.02
    r2 = analyze_crumb_image(mask, scale_mm_per_px=0.2, provenance="simulated")
    assert np.isclose(r2["pore_area_fraction"], mask.mean())


def test_compare_to_params():
    img, _ = synthetic_crumb_image(target_fraction=0.3, seed=3)
    res = analyze_crumb_image(img, scale_mm_per_px=0.1)
    cmp_ = compare_to_params(res, MILD)
    assert cmp_["params_provenance"] == "simulated"
    assert np.isclose(cmp_["pore_area_fraction"]["difference"],
                      res["pore_area_fraction"] - MILD.porosity_target)
    assert cmp_["mean_section_diameter_mm"]["expected_from_params"] > 0
    assert "suggested_params" in cmp_
