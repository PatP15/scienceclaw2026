"""PLAN §16 #1–4 from the Mac alone: cached ALCHEMI evidence is complete, the fermentation
forecasts reproduce bit-for-bit-ish without a GPU, and both crumb STLs are valid and different."""

from __future__ import annotations

import json
import struct
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from .conftest import REPO_ROOT

pytestmark = pytest.mark.integration
DATA = REPO_ROOT / "data"


def test_alchemi_results_and_gpu_logs_are_on_the_mac():
    """#1–2: real GPU outputs + logs were synced back (the instance has been deleted)."""
    summ = json.loads((DATA / "alchemi/analysis/analysis_summary.json").read_text())  # NaN tolerated
    assert summ["provenance"].startswith("real:") and "A6000" in summ["provenance"]
    assert summ["n_calculations"] == summ["n_converged"] == 159 and summ["n_failed"] == 0
    results = list((DATA / "alchemi/results").rglob("*.json"))
    assert len(results) >= 159, len(results)
    one = json.loads(results[0].read_text())
    blob = json.dumps(one).lower()
    assert "energy" in blob and "force" in blob, sorted(one)[:20]
    logs = list((DATA / "alchemi/logs").rglob("*")) + list((DATA / "alchemi/md").rglob("*"))
    assert any(p.is_file() for p in logs)
    env = (DATA / "alchemi/environment/report.md").read_text()
    assert "RTX A6000" in env and "driver" in env.lower()


def test_forecasts_reproduce_on_the_mac_without_gpu():
    """#3: re-run the nominal A/B scenarios in a clean interpreter with no torch/CUDA imported,
    and compare with the committed data/predictions CSVs."""
    code = r"""
import json, sys
from fermentai.fermentation.simulation import load_scenarios, simulate
out = {}
for cid, sc in load_scenarios().items():
    df = simulate(sc.schedule, t_end_h=sc.horizon_h, dt_h=0.05)
    out[cid] = {c: df[c].tolist() for c in ("time_h", "ph", "expansion_ratio", "lactic_acid", "acetic_acid")}
assert "torch" not in sys.modules and "warp" not in sys.modules
print(json.dumps(out))
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                       timeout=300, check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    fresh = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(fresh) == {"A", "B"}
    for cid, cols in fresh.items():
        ref = pd.read_csv(DATA / f"predictions/chamber_{cid}_prediction.csv")
        assert len(ref) == len(cols["time_h"]), (cid, len(ref), len(cols["time_h"]))
        for c, vals in cols.items():
            np.testing.assert_allclose(np.asarray(vals), ref[c].to_numpy(), rtol=2e-4, atol=1e-6,
                                       err_msg=f"chamber {cid} column {c} differs from the CSV")
    # the two hypotheses differ in the expected direction (B: warmer -> more acidic)
    assert fresh["B"]["ph"][-1] < fresh["A"]["ph"][-1]


def _stl_triangles(path) -> int:
    with open(path, "rb") as f:
        head = f.read(2048)
    if head[:5] == b"solid" and b"facet" in head:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip().startswith(b"facet"))
    return struct.unpack("<I", head[80:84])[0]


def test_both_chamber_stls_valid_and_distinguishable():
    """#4: both validate (watertight, printable thickness ...) and are different specimens."""
    reps = {}
    for cid in "AB":
        stl = DATA / f"stl/crumb_chamber_{cid}.stl"
        n = _stl_triangles(stl)
        assert stl.stat().st_size == 84 + 50 * n, "binary STL size does not match its triangle count"
        v = json.loads((DATA / f"stl/crumb_chamber_{cid}_validation.json").read_text())
        assert v["passed"] is True, v
        assert all(c.get("passed", True) for c in v["checks"].values()), v["checks"]
        m = json.loads((DATA / f"stl/crumb_chamber_{cid}_mapping.json").read_text())
        reps[cid] = (n, m)
    assert reps["A"][0] != reps["B"][0]
    pa, pb = reps["A"][1]["outputs"], reps["B"][1]["outputs"]
    assert pa and pb and pa != pb, "chamber A and B crumb parameters are identical"
    assert (DATA / "stl/crumb_chamber_comparison.png").stat().st_size > 10_000
