"""Presentation hero renders (fermentai.geometry.hero) are produced from the STLs."""

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fermentai.geometry.hero import oblique_view, render_heroes, top_view

ROOT = Path(__file__).resolve().parents[1]
STL = ROOT / "data" / "stl"
NEEDED = ["crumb_chamber_A.stl", "crumb_chamber_B.stl", "chain_summary.json",
          "crumb_mild.stl", "crumb_sour.stl", "crumb_summary.json"]


@pytest.mark.skipif(not all((STL / f).exists() for f in NEEDED),
                    reason="run scripts/generate_stls.py first")
def test_hero_files_produced(tmp_path):
    for f in NEEDED:  # copy so the test never touches data/stl
        shutil.copy(STL / f, tmp_path / f)
    paths = render_heroes(tmp_path, tmp_path, pitch=0.2, supersample=1)  # fast settings
    names = {p.name for p in paths}
    assert names == {"hero_chamber_A.png", "hero_chamber_B.png", "hero_chamber_AB.png",
                     "hero_mild_sour.png"}
    for p in paths:
        im = Image.open(p)
        assert im.size == (3200, 1800)
        a = np.asarray(im.convert("RGB"), float)
        assert a.std() > 10  # not blank


def test_views_of_synthetic_heightfield():
    n = 120
    h = np.full((n, n), 10.0)
    yy, xx = np.mgrid[:n, :n]
    h[(xx - 60) ** 2 + (yy - 60) ** 2 < 15**2] = 6.0  # one pore
    h[:5, :] = np.nan  # outside the footprint
    ob = oblique_view(h, 0.1, 10.0, supersample=2)
    tv = top_view(h, 0.1, 10.0)
    assert ob.shape[2] == 4 and ob[..., 3].max() == 1.0 and np.isfinite(ob).all()
    assert tv.shape == (n, n, 4) and tv[..., 3].sum() == np.isfinite(h).sum()
