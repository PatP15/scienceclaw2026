"""Mac-side orchestration of the remote NVIDIA ALCHEMI runs, and loaders for the cached results.

The GPU work itself lives in ``remote/run_alchemi.py`` (runs on the Brev box). This module:

* wraps the shell helpers (``scripts/setup_brev.sh``, ``scripts/run_remote_alchemi.sh``,
  ``scripts/collect_brev_results.sh``, ``scripts/stop_brev.sh``) so agents/CLI can call them;
* loads cached results from ``data/alchemi/`` *without* needing Brev or a GPU (the dashboard and
  agents only ever read the cache);
* writes the environment report ``data/alchemi/environment/report.md``.

CLI::

    uv run python -m fermentai.molecular.alchemi_runner status          # what is cached locally
    uv run python -m fermentai.molecular.alchemi_runner report --rate 0.60 --start ... --stop ...
    uv run python -m fermentai.molecular.alchemi_runner remote optimize # needs a running instance
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
ALCHEMI_DIR = REPO_ROOT / "data" / "alchemi"
SCRIPTS = REPO_ROOT / "scripts"

LAYOUT = {
    "inputs": "inputs/manifest.json + inputs/xyz/*.xyz (RDKit starting geometries, Mac-generated)",
    "official_example": "official_example/ (unmodified NVIDIA example 08 run on the GPU + log + CSV)",
    "selftest": "selftest/selftest.json (single points, finiteness + upstream-AIMNet2 cross-check)",
    "results": "results/json/<id>.json, results/xyz/<id>_{initial,optimized}.xyz, results/traj/<id>.csv",
    "md": "md/<id>__T<K>K.{json,csv} + _frames.xyz (optional NVT Langevin)",
    "analysis": "analysis/*.csv, analysis_summary.json, *.html (self-contained Plotly), *.png",
    "environment": "environment/ (nvidia-smi, pip freeze, remote_environment.json, report.md)",
    "logs": "logs/<mode>_<UTC stamp>.log (actual remote stdout/stderr), logs/failed/ (preserved failures)",
}


# ----------------------------------------------------------------------------- remote wrappers


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, check=check, text=True)


def setup_remote() -> None:
    _run([str(SCRIPTS / "setup_brev.sh")])


def run_remote(mode: str, *args: str) -> None:
    """Run ``remote/run_alchemi.py <mode>`` on the Brev box and pull results back."""
    _run([str(SCRIPTS / "run_remote_alchemi.sh"), mode, *args])


def collect() -> None:
    _run([str(SCRIPTS / "collect_brev_results.sh")])


def stop(delete_if_unstoppable: bool = False) -> None:
    _run([str(SCRIPTS / "stop_brev.sh"), *(["--delete-if-unstoppable"] if delete_if_unstoppable else [])])


# ----------------------------------------------------------------------------- cache readers


def load_json(rel: str) -> Any:
    p = ALCHEMI_DIR / rel
    return json.loads(p.read_text()) if p.exists() else None


def cached_status() -> dict[str, Any]:
    """Summary of what real ALCHEMI output is present locally (no network needed)."""
    res_dir = ALCHEMI_DIR / "results" / "json"
    recs = [json.loads(p.read_text()) for p in sorted(res_dir.glob("*.json"))] if res_dir.exists() else []
    md_dir = ALCHEMI_DIR / "md"
    md = [json.loads(p.read_text()) for p in sorted(md_dir.glob("*.json"))] if md_dir.exists() else []
    st = load_json("selftest/selftest.json")
    return {
        "n_optimisations": len(recs),
        "n_converged": sum(1 for r in recs if r.get("converged")),
        "n_failed": sum(1 for r in recs if str(r.get("status", "")).startswith("failed")),
        "n_md_runs": len(md),
        "n_md_completed": sum(1 for r in md if r.get("status") == "completed"),
        "selftest_all_finite": None if st is None else st.get("all_finite"),
        "official_example_log": (ALCHEMI_DIR / "official_example" / "08_aimnet2_ewald_pipeline.log").exists(),
        "analysis_summary": (ALCHEMI_DIR / "analysis" / "analysis_summary.json").exists(),
        "provenance": sorted({r.get("provenance") for r in recs if r.get("provenance")}),
        "layout": LAYOUT,
    }


# ----------------------------------------------------------------------------- environment report


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def write_environment_report(rate_per_hour: float, started_utc: str, stopped_utc: str | None,
                             instance_type: str = "hyperstack_A6000", notes: list[str] | None = None) -> Path:
    env = ALCHEMI_DIR / "environment"
    remote = load_json("environment/remote_environment.json") or {}
    st = load_json("selftest/selftest.json") or {}
    v = remote.get("versions") or st.get("versions") or {}
    ck = remote.get("checkpoint") or {}
    smi = (env / "nvidia-smi_p0.txt").read_text() if (env / "nvidia-smi_p0.txt").exists() else ""
    driver = next((ln.split("Driver Version:")[1].split()[0] for ln in smi.splitlines() if "Driver Version:" in ln), "?")
    cuda_drv = next((ln.split("CUDA Version:")[1].split()[0] for ln in smi.splitlines() if "CUDA Version:" in ln), "?")
    t0 = _parse_utc(started_utc)
    t1 = _parse_utc(stopped_utc) if stopped_utc else datetime.now(UTC)
    hours = (t1 - t0).total_seconds() / 3600
    status = cached_status()
    wall = 0.0
    for p in (ALCHEMI_DIR / "results" / "json").glob("*.json"):
        wall += json.loads(p.read_text()).get("wall_seconds") or 0.0
    md_wall = sum((json.loads(p.read_text()).get("wall_seconds") or 0.0) for p in (ALCHEMI_DIR / "md").glob("*.json"))
    lines = [
        "# ALCHEMI environment report",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} by `fermentai.molecular.alchemi_runner report`.",
        "Provenance of all numbers below: **real** (read from files produced on the Brev GPU instance).",
        "",
        "## Hardware / instance",
        f"- Brev instance: `fermentai-gpu`, type `{instance_type}`, listed price ${rate_per_hour:.2f}/hr (`brev search`)",
        f"- GPU: {v.get('gpu', '?')} (compute capability {v.get('gpu_capability', '?')}, {v.get('gpu_memory_gb', '?')} GB)",
        f"- NVIDIA driver {driver}, driver CUDA version {cuda_drv}",
        f"- OS/platform: {v.get('platform', '?')}",
        "",
        "## Software",
        f"- Python {v.get('python', '?')}",
        f"- PyTorch {v.get('torch', '?')} (CUDA {v.get('torch_cuda', '?')}, cuDNN {v.get('cudnn', '?')})",
        f"- NVIDIA ALCHEMI Toolkit `nvalchemi-toolkit` {v.get('nvalchemi-toolkit', '?')}; "
        f"`nvalchemi-toolkit-ops` {v.get('nvalchemi-toolkit-ops', '?')}",
        f"- NVIDIA Warp `warp-lang` {v.get('warp-lang', '?')} (the `+cu12` build; see note below)",
        f"- `aimnet` {v.get('aimnet', '?')}, ASE {v.get('ase', '?')}, PhysicsNeMo {v.get('nvidia-physicsnemo', '?')}",
        "- Full lockfile: `data/alchemi/environment/pip_freeze.txt`",
        "",
        "## Model",
        f"- AIMNet2 checkpoint: `{ck.get('name', 'aimnet2_wb97m_d3_3')}`"
        + (f", sha256 `{ck['sha256']}`" if ck.get("sha256") else "")
        + (f", {ck.get('n_parameters')} parameters" if ck.get("n_parameters") else "")
        + (f", AEV cutoff {ck.get('cutoff_angstrom')} Å" if ck.get("cutoff_angstrom") else ""),
        "- Downloaded automatically by `aimnet` from "
        "`https://storage.googleapis.com/aimnetcentral/aimnet2v2/AIMNet2/aimnet2_wb97m_d3_3.pt`",
        f"- Composition: `PipelineModelWrapper([[AIMNet2Wrapper, EwaldModelWrapper] (shared autograd), "
        f"[DFTD3ModelWrapper]])`, D3(BJ) {remote.get('d3_params_used') or st.get('d3_params_used')}",
        "",
        "## Validation",
        "- Official example `08_aimnet2_ewald_pipeline.py` reproduced on the GPU: see `official_example/`.",
        f"- Selftest all finite: {st.get('all_finite')}; ALCHEMI pipeline vs upstream `aimnet` calculator "
        + ", ".join(f"{r['id']}: ΔE={r.get('delta_energy_ev_alchemi_minus_upstream', float('nan')) * 1000:+.2f} meV, "
                    f"max|ΔF|={r.get('max_abs_force_diff_ev_per_angstrom', float('nan')):.1e} eV/Å"
                    for r in st.get("rows", []) if "delta_energy_ev_alchemi_minus_upstream" in r),
        "",
        "## Runtime and cost",
        f"- Instance created {started_utc}; stopped/deleted {stopped_utc or '(still running at report time)'}",
        f"- Elapsed instance time {hours:.2f} h × ${rate_per_hour:.2f}/h ≈ **${hours * rate_per_hour:.2f}** (estimate; "
        "Brev's invoice is authoritative)",
        f"- Sum of per-structure optimisation wall time: {wall / 60:.1f} min; MD wall time: {md_wall / 60:.1f} min",
        f"- Calculations: {status['n_optimisations']} optimisations ({status['n_converged']} converged, "
        f"{status['n_failed']} failed), {status['n_md_runs']} MD runs ({status['n_md_completed']} completed)",
        "",
        "## Notes",
        "- PyPI `warp-lang` ≥1.10 is built with CUDA 13 and needs an R580+ driver. On this instance (driver "
        f"{driver}, CUDA {cuda_drv}) the first run of example 08 failed with `ValueError: Invalid device identifier: "
        "cuda:0` (log preserved: `official_example/08_aimnet2_ewald_pipeline__attempt1_FAILED_warp_cu13_driver12.8.log`). "
        "Fixed per Warp's install guide by installing the `+cu12` wheel from GitHub Releases "
        "(automated in `remote/setup_env.sh`).",
        *[f"- {n}" for n in (notes or [])],
    ]
    out = env / "report.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="ALCHEMI orchestration (Mac side)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("setup")
    sub.add_parser("collect")
    r = sub.add_parser("remote")
    r.add_argument("mode")
    r.add_argument("args", nargs=argparse.REMAINDER)
    s = sub.add_parser("stop")
    s.add_argument("--delete-if-unstoppable", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--rate", type=float, required=True, help="listed $/hr")
    rep.add_argument("--start", required=True, help="instance create time, UTC ISO (YYYY-MM-DDTHH:MM:SSZ)")
    rep.add_argument("--stop", default=None, help="instance stop/delete time, UTC ISO")
    rep.add_argument("--type", default="hyperstack_A6000")
    rep.add_argument("--note", action="append", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(cached_status(), indent=2))
    elif a.cmd == "setup":
        setup_remote()
    elif a.cmd == "collect":
        collect()
    elif a.cmd == "remote":
        run_remote(a.mode, *a.args)
    elif a.cmd == "stop":
        stop(a.delete_if_unstoppable)
    elif a.cmd == "report":
        print(write_environment_report(a.rate, a.start, a.stop, a.type, a.note))


if __name__ == "__main__":
    main()
