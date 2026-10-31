"""Tests for fermentai.molecular.alchemi_runner cache readers / report writer (no Brev, no GPU).

Uses a SYNTHETIC tmp directory; never reads or writes data/alchemi/.
"""

from __future__ import annotations

import json

from fermentai.molecular import alchemi_runner as ar


def _setup(tmp_path, monkeypatch):
    d = tmp_path / "alchemi"
    (d / "results" / "json").mkdir(parents=True)
    (d / "md").mkdir()
    (d / "selftest").mkdir()
    (d / "environment").mkdir()
    for i, (status, conv) in enumerate([("converged", True), ("not_converged", False), ("failed", False)]):
        (d / "results" / "json" / f"s{i}.json").write_text(json.dumps(
            {"id": f"s{i}", "status": status, "converged": conv, "wall_seconds": 2.0,
             "provenance": "TEST FIXTURE"}))
    (d / "md" / "m0.json").write_text(json.dumps({"id": "m0", "status": "completed", "wall_seconds": 60.0}))
    (d / "selftest" / "selftest.json").write_text(json.dumps({
        "all_finite": True, "versions": {"gpu": "TEST-GPU", "torch": "x"}, "d3_params_used": {"a1": 1},
        "rows": [{"id": "w", "delta_energy_ev_alchemi_minus_upstream": 0.0005, "max_abs_force_diff_ev_per_angstrom": 1e-4}]}))
    (d / "environment" / "nvidia-smi_p0.txt").write_text("| NVIDIA-SMI 570.1  Driver Version: 570.195.03  CUDA Version: 12.8 |\n")
    monkeypatch.setattr(ar, "ALCHEMI_DIR", d)
    return d


def test_cached_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    st = ar.cached_status()
    assert st["n_optimisations"] == 3
    assert st["n_converged"] == 1
    assert st["n_failed"] == 1
    assert st["n_md_runs"] == 1 and st["n_md_completed"] == 1
    assert st["selftest_all_finite"] is True
    assert "results" in st["layout"]


def test_environment_report_cost(tmp_path, monkeypatch):
    d = _setup(tmp_path, monkeypatch)
    out = ar.write_environment_report(0.60, "2026-10-31T03:00:00Z", "2026-10-31T05:30:00Z")
    text = out.read_text()
    assert out == d / "environment" / "report.md"
    assert "2.50 h" in text and "$1.50" in text
    assert "570.195.03" in text and "12.8" in text
    assert "TEST-GPU" in text


def test_environment_report_sessions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = ar.write_environment_report(0.60, "2026-10-31T03:00:00Z", "2026-10-31T04:00:00Z",
                                      extra_sessions=[("campaign 2", "2026-10-31T05:00:00Z", "2026-10-31T07:00:00Z")])
    text = out.read_text()
    assert "campaign 2" in text and "2.00 h" in text
    assert "Total across sessions: 3.00 h ≈ $1.80" in text
