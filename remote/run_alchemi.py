#!/usr/bin/env python
"""FermentAI remote runner: NVIDIA ALCHEMI Toolkit (nvalchemi) + AIMNet2 on the Brev GPU.

Runs ON the GPU box inside ~/fermentai/.venv (see remote/setup_env.sh). Self-contained: reads
configs/molecular.yaml and inputs/manifest.json + inputs/xyz/*.xyz produced on the Mac by
fermentai.molecular.structures, writes everything under results/ (mirrored to data/alchemi/).

Model (the toolkit's documented composition, cf. official example 08_aimnet2_ewald_pipeline):
    PipelineModelWrapper([
        PipelineGroup([AIMNet2Wrapper(aimnet2_wb97m_d3_3), EwaldModelWrapper], use_autograd=True),
        PipelineGroup([DFTD3ModelWrapper(wB97M-D3(BJ) parameters)]),
    ])
AIMNet2Wrapper disables AIMNet2's built-in Coulomb and D3 terms; Ewald (on AIMNet2 charges) and
DFT-D3(BJ) put them back. Molecules sit in a large periodic box (default 60 A) as in example 08.

Modes
-----
env        environment report (GPU, CUDA, torch, nvalchemi, aimnet, checkpoint) -> results/environment/
selftest   single-point E/F for water + both acids; asserts finite; cross-checks against the
           upstream aimnet AIMNet2Calculator (gas phase, its own Coulomb + D3) -> results/selftest/
optimize   FIRE2 geometry optimisation of every manifest structure -> results/results/
md         NVT Langevin (BAOAB) MD of the lowest-energy optimised clusters -> results/md/

Never fabricates numbers: every value written comes from a model evaluation in this process; any
exception is logged with its traceback and recorded as status="failed".
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
import yaml

ROOT = Path(__file__).resolve().parents[1]  # ~/fermentai
RESULTS = ROOT / "results"
KB_EV = 8.617333262e-5
EV_TO_KCAL = 23.060547830619
EV_TO_KJ = 96.48533212
HARTREE_EV = 27.211386245988
MADELUNG_SC = 2.837297  # simple-cubic Madelung constant (Makov & Payne 1995)
COULOMB_EV_A = 14.399645  # e^2/(4 pi eps0) in eV*Angstrom
Z = {"H": 1, "C": 6, "N": 7, "O": 8}
SYM = {v: k for k, v in Z.items()}


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs" / "molecular.yaml").read_text())


def read_xyz(path: Path) -> tuple[list[str], np.ndarray]:
    lines = path.read_text().splitlines()
    n = int(lines[0].split()[0])
    syms, xyz = [], []
    for ln in lines[2 : 2 + n]:
        p = ln.split()
        syms.append(p[0])
        xyz.append([float(v) for v in p[1:4]])
    return syms, np.array(xyz, dtype=float)


MIN_PHYSICAL_DISTANCE = 0.70  # Angstrom; shortest physical contact (H2 bond 0.74 A)


def _min_dist(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    d = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1) + np.eye(len(x)) * 1e9
    return float(d.min())


def write_xyz(path: Path, syms: list[str], xyz: np.ndarray, comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(len(syms)), comment]
    lines += [f"{s:2s} {x:16.10f} {y:16.10f} {z:16.10f}" for s, (x, y, z) in zip(syms, xyz)]
    path.write_text("\n".join(lines) + "\n")


def versions() -> dict:
    import importlib.metadata as md

    out = {"python": sys.version.split()[0], "platform": platform.platform()}
    for p in ("nvalchemi-toolkit", "nvalchemi-toolkit-ops", "aimnet", "torch", "warp-lang", "ase",
              "nvidia-physicsnemo", "numpy"):
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = None
    out["torch_cuda"] = torch.version.cuda
    out["cudnn"] = torch.backends.cudnn.version()
    if torch.cuda.is_available():
        out["gpu"] = torch.cuda.get_device_name(0)
        out["gpu_capability"] = ".".join(map(str, torch.cuda.get_device_capability(0)))
        out["gpu_memory_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2)
    return out


# ----------------------------------------------------------------------------- model


class ModelBundle:
    """AIMNet2 (+Ewald +D3) pipeline built the way the ALCHEMI docs compose it."""

    def __init__(self, cfg: dict, device: str):
        from nvalchemi.models.aimnet2 import AIMNet2Wrapper

        self.cfg = cfg
        self.device = device
        m = cfg["model"]
        t0 = time.time()
        self.aimnet2 = AIMNet2Wrapper.from_checkpoint(m["checkpoint"], device=device, compile_model=False)
        self.load_seconds = time.time() - t0
        self.checkpoint = m["checkpoint"]
        self.checkpoint_info = self._checkpoint_info()
        d3 = dict(m["d3_bj"])
        self.d3_params_config = d3
        self.d3_params = self._d3_params_from_aimnet() or d3
        log(f"AIMNet2 '{self.checkpoint}' loaded on {device} in {self.load_seconds:.1f}s; "
            f"D3(BJ) params used: {self.d3_params}")

    def _checkpoint_info(self) -> dict:
        info = {"name": self.checkpoint}
        try:
            from aimnet.calculators import model_registry as reg  # type: ignore

            for fn in ("get_model_path", "get_model_definition", "load_model_registry"):
                if hasattr(reg, fn):
                    info.setdefault("registry_functions", []).append(fn)
            if hasattr(reg, "get_model_path"):
                p = reg.get_model_path(self.checkpoint)
                info["path"] = str(p)
                if p and os.path.exists(p):
                    import hashlib

                    info["sha256"] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
                    info["size_bytes"] = os.path.getsize(p)
        except Exception as e:  # pragma: no cover - informational only
            info["registry_error"] = repr(e)
        raw = self.aimnet2.model
        info["cutoff_angstrom"] = float(self.aimnet2._cutoff)
        info["n_parameters"] = int(sum(p.numel() for p in raw.parameters()))
        meta = getattr(self.aimnet2._calculator, "metadata", None) or getattr(raw, "metadata", None)
        if isinstance(meta, dict):
            info["metadata"] = {k: (v if isinstance(v, (int, float, str, bool, type(None), list, dict)) else repr(v))
                                for k, v in meta.items()}
        return info

    def _d3_params_from_aimnet(self) -> dict | None:
        """Use the D3 parameters shipped with the AIMNet2 checkpoint if the aimnet package exposes them."""
        meta = self.checkpoint_info.get("metadata") or {}
        for key in ("d3_params", "dispersion_params", "d3"):
            v = meta.get(key)
            if isinstance(v, dict) and {"a1", "a2", "s8"} <= set(v):
                return {"s6": float(v.get("s6", 1.0)), "a1": float(v["a1"]), "s8": float(v["s8"]),
                        "a2": float(v["a2"])}
        return None

    def pipeline(self, batch):
        from nvalchemi.models.dftd3 import DFTD3ModelWrapper
        from nvalchemi.models.ewald import EwaldModelWrapper
        from nvalchemi.models.pipeline import PipelineGroup, PipelineModelWrapper
        from nvalchemiops.torch.interactions.electrostatics.parameters import (
            estimate_ewald_parameters,
        )

        m = self.cfg["model"]
        params = estimate_ewald_parameters(batch.positions, batch.cell, batch.batch_idx)
        ewald = EwaldModelWrapper(cutoff=params.real_space_cutoff.max(), accuracy=m["ewald_accuracy"],
                                  hybrid_forces=False)
        d3p = self.d3_params
        d3 = DFTD3ModelWrapper(a1=d3p["a1"], a2=d3p["a2"], s8=d3p["s8"], s6=d3p.get("s6", 1.0))
        pipe = PipelineModelWrapper(groups=[
            PipelineGroup(steps=[self.aimnet2, ewald], use_autograd=True),
            PipelineGroup(steps=[d3]),
        ]).eval()
        pipe.set_config("active_outputs", {"energy", "forces", "charges"})
        return pipe


def make_atomic_data(syms: list[str], xyz: np.ndarray, cfg: dict, charge: int = 0, box: float | None = None):
    from nvalchemi.data import AtomicData

    box = float(box or cfg["model"]["box_size_angstrom"])
    pos = xyz - xyz.mean(axis=0) + box / 2.0
    n = len(syms)
    return AtomicData(
        positions=torch.tensor(pos, dtype=torch.float32),
        atomic_numbers=torch.tensor([Z[s] for s in syms], dtype=torch.long),
        forces=torch.zeros(n, 3),
        energy=torch.zeros(1, 1),
        cell=torch.eye(3).unsqueeze(0) * box,
        pbc=torch.tensor([[True, True, True]]),
        charge=torch.full((1, 1), float(charge)),
    )


def single_point(bundle: ModelBundle, syms, xyz, cfg, charge: int = 0, box: float | None = None) -> dict:
    """Energy/forces/charges with the full pipeline (neighbour hooks run explicitly)."""
    from nvalchemi.data import Batch

    batch = Batch.from_data_list([make_atomic_data(syms, xyz, cfg, charge, box)], device=bundle.device)
    pipe = bundle.pipeline(batch)
    from nvalchemi.dynamics.base import DynamicsStage
    from nvalchemi.hooks._context import HookContext

    ctx = HookContext(batch=batch, model=pipe)
    for hook in pipe.make_neighbor_hooks():
        hook(ctx, DynamicsStage.BEFORE_COMPUTE)
    out = pipe(batch)
    e = float(out["energy"].detach().reshape(-1)[0].cpu())
    f = out["forces"].detach().cpu().numpy().astype(float)
    q = out.get("charges")
    return {
        "energy_ev": e,
        "forces_ev_per_angstrom": f.tolist(),
        "fmax_ev_per_angstrom": float(np.linalg.norm(f, axis=1).max()),
        "charges_e": None if q is None else q.detach().cpu().reshape(-1).numpy().astype(float).tolist(),
    }


def upstream_aimnet_single_point(checkpoint: str, syms, xyz, device: str, charge: int = 0) -> dict:
    """Reference single point with the upstream aimnet AIMNet2Calculator (gas phase, its own
    Coulomb + D3). Used only to cross-check the ALCHEMI pipeline; labelled as such."""
    from aimnet.calculators import AIMNet2Calculator

    calc = AIMNet2Calculator(checkpoint, device=device)
    data = {
        "coord": torch.tensor(xyz, dtype=torch.float32, device=device),
        "numbers": torch.tensor([Z[s] for s in syms], dtype=torch.long, device=device),
        "charge": torch.tensor([float(charge)], device=device),
    }
    res = calc(data, forces=True)
    e = float(res["energy"].detach().reshape(-1)[0].cpu())
    f = res["forces"].detach().cpu().reshape(-1, 3).numpy().astype(float)
    return {"energy_ev": e, "fmax_ev_per_angstrom": float(np.linalg.norm(f, axis=1).max()),
            "forces_ev_per_angstrom": f.tolist()}


# ----------------------------------------------------------------------------- modes


def mode_env(cfg, device) -> None:
    out = RESULTS / "environment"
    out.mkdir(parents=True, exist_ok=True)
    rep = {"generated_at_utc": now(), "versions": versions(), "device": device}
    try:
        bundle = ModelBundle(cfg, device)
        rep["checkpoint"] = bundle.checkpoint_info
        rep["d3_params_used"] = bundle.d3_params
        rep["d3_params_config"] = bundle.d3_params_config
        rep["aimnet2_load_seconds"] = bundle.load_seconds
    except Exception:
        rep["model_error"] = traceback.format_exc()
    (out / "remote_environment.json").write_text(json.dumps(rep, indent=2))
    log(json.dumps(rep, indent=2))


def mode_selftest(cfg, device) -> int:
    out = RESULTS / "selftest"
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "inputs" / "manifest.json").read_text())
    ids = ["water__n1__c0", "acetic_acid__n0__c0", "lactic_acid__n0__c0", "acetic_acid__n1__c0"]
    bundle = ModelBundle(cfg, device)
    rows, ok = [], True
    for sid in ids:
        ent = next(e for e in manifest["structures"] if e["id"] == sid)
        syms, xyz = read_xyz(ROOT / "inputs" / ent["xyz"])
        t0 = time.time()
        sp = single_point(bundle, syms, xyz, cfg)
        dt = time.time() - t0
        finite = math.isfinite(sp["energy_ev"]) and bool(np.isfinite(sp["forces_ev_per_angstrom"]).all())
        ok &= finite
        row = {"id": sid, "n_atoms": len(syms), "energy_ev": sp["energy_ev"],
               "fmax_ev_per_angstrom": sp["fmax_ev_per_angstrom"], "finite": finite, "seconds": dt,
               "charges_sum_e": None if sp["charges_e"] is None else float(sum(sp["charges_e"]))}
        try:
            ref = upstream_aimnet_single_point(bundle.checkpoint, syms, xyz, device)
            row["upstream_aimnet_energy_ev"] = ref["energy_ev"]
            row["delta_energy_ev_alchemi_minus_upstream"] = sp["energy_ev"] - ref["energy_ev"]
            df = np.array(sp["forces_ev_per_angstrom"]) - np.array(ref["forces_ev_per_angstrom"])
            row["max_abs_force_diff_ev_per_angstrom"] = float(np.abs(df).max())
        except Exception:
            row["upstream_aimnet_error"] = traceback.format_exc()
        log(json.dumps(row))
        rows.append(row)
    rep = {"generated_at_utc": now(), "device": device, "versions": versions(), "checkpoint": bundle.checkpoint,
           "d3_params_used": bundle.d3_params, "all_finite": ok, "rows": rows}
    (out / "selftest.json").write_text(json.dumps(rep, indent=2))
    log(f"selftest all_finite={ok}")
    return 0 if ok else 1


def _optimizer(name: str, pipe, cfg, threshold: float, max_steps: int):
    from nvalchemi.dynamics.base import ConvergenceHook

    o = cfg["optimization"]
    if name == "FIRE2":
        from nvalchemi.dynamics.optimizers.fire2 import FIRE2

        return FIRE2(model=pipe, dt=o["fire2_dt"], n_steps=max_steps,
                     convergence_hook=ConvergenceHook.from_fmax(threshold=threshold))
    if name == "LBFGS":
        from nvalchemi.dynamics import LBFGS  # present in newer toolkit versions only

        return LBFGS(model=pipe, n_steps=max_steps, convergence_hook=ConvergenceHook.from_fmax(threshold))
    raise ValueError(name)


def optimize_one(bundle: ModelBundle, ent: dict, cfg: dict, method: str) -> dict:
    from nvalchemi.data import Batch
    from nvalchemi.dynamics.base import DynamicsStage
    from nvalchemi.dynamics.hooks import LoggingHook

    o = cfg["optimization"]
    syms, xyz0 = read_xyz(ROOT / "inputs" / ent["xyz"])
    batch = Batch.from_data_list([make_atomic_data(syms, xyz0, cfg, int(ent.get("charge", 0)))], device=bundle.device)
    pos_initial = batch.positions.detach().cpu().numpy().astype(float).copy()
    pipe = bundle.pipeline(batch)
    opt = _optimizer(method, pipe, cfg, float(o["fmax_ev_per_angstrom"]), int(o["max_steps"]))
    for hook in pipe.make_neighbor_hooks():
        opt.register_hook(hook, stage=DynamicsStage.BEFORE_COMPUTE) if method == "LBFGS" else opt.register_hook(hook)
    rows: list[dict] = []

    def writer(step, rs):
        for r in rs:
            rows.append({k: (float(v) if isinstance(v, (int, float)) or hasattr(v, "item") else v)
                         for k, v in r.items()})

    t0 = time.time()
    with LoggingHook(backend="custom", writer_fn=writer, frequency=int(o["log_frequency"])) as lh:
        opt.register_hook(lh)
        batch = opt.run(batch)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    wall = time.time() - t0
    pos_final = batch.positions.detach().cpu().numpy().astype(float)
    # Final energy/forces from an independent single point at the optimised geometry.
    sp = single_point(bundle, syms, pos_final, cfg, int(ent.get("charge", 0)))
    steps = int(opt.step_count)
    return {
        "syms": syms, "pos_initial": pos_initial, "pos_final": pos_final, "rows": rows,
        "steps": steps, "wall": wall, "sp": sp,
        "batch_energy_ev": float(batch.energy.detach().reshape(-1)[0].cpu()),
    }


def mode_optimize(cfg, device, only: list[str] | None, kinds: list[str] | None) -> int:
    out = RESULTS / "results"
    (out / "json").mkdir(parents=True, exist_ok=True)
    (out / "xyz").mkdir(parents=True, exist_ok=True)
    (out / "traj").mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "inputs" / "manifest.json").read_text())
    bundle = ModelBundle(cfg, device)
    o = cfg["optimization"]
    thr = float(o["fmax_ev_per_angstrom"])
    entries = [e for e in manifest["structures"]
               if (not only or e["id"] in only) and (not kinds or e["kind"] in kinds)]
    log(f"optimising {len(entries)} structures with {o['method']} (fmax<{thr} eV/A, max {o['max_steps']} steps)")
    summary, n_fail = [], 0
    for i, ent in enumerate(entries):
        sid = ent["id"]
        rec = {"id": sid, "kind": ent["kind"], "acid": ent["acid"], "n_water": ent["n_water"],
               "conformer_index": ent["conformer_index"], "seeds": ent["seeds"], "charge": ent["charge"],
               "multiplicity": ent["multiplicity"], "formula": ent["formula"], "fragments": ent["fragments"],
               "n_atoms": ent["n_atoms"], "input_sha256": ent["sha256"],
               "input_meta": {k: v for k, v in ent.items() if k in ("derived_from", "derivation", "species")},
               "provenance": f"real: ALCHEMI/AIMNet2 on {versions().get('gpu', device)}",
               "method": {"engine": "nvalchemi-toolkit", "optimizer": o["method"],
                          "model": "PipelineModelWrapper[[AIMNet2Wrapper+EwaldModelWrapper](autograd)+[DFTD3ModelWrapper]]",
                          "checkpoint": bundle.checkpoint, "d3_bj": bundle.d3_params,
                          "box_size_angstrom": cfg["model"]["box_size_angstrom"],
                          "ewald_accuracy": cfg["model"]["ewald_accuracy"],
                          "fmax_threshold_ev_per_angstrom": thr, "max_steps": o["max_steps"],
                          "fire2_dt": o["fire2_dt"]},
               "versions": versions(), "started_at_utc": now()}
        try:
            method = o["method"]
            r = optimize_one(bundle, ent, cfg, method)
            attempts = [{"optimizer": method, "steps": r["steps"], "final_fmax": r["sp"]["fmax_ev_per_angstrom"]}]
            if r["sp"]["fmax_ev_per_angstrom"] >= thr and o.get("fallback_method"):
                try:
                    log(f"{sid}: {method} not converged (fmax={r['sp']['fmax_ev_per_angstrom']:.4f}); "
                        f"trying {o['fallback_method']} from the {method} end point")
                    tmp_ent = dict(ent)
                    p = ROOT / "results" / "results" / "xyz" / f"{sid}__{method}_endpoint.xyz"
                    write_xyz(p, r["syms"], r["pos_final"], f"id={sid} {method} end point (not converged)")
                    tmp_ent["xyz"] = os.path.relpath(p, ROOT / "inputs")
                    r2 = optimize_one(bundle, tmp_ent, cfg, o["fallback_method"])
                    r2["pos_initial"] = r["pos_initial"]
                    r2["rows"] = r["rows"] + [dict(x, step=x.get("step", 0) + r["steps"]) for x in r2["rows"]]
                    attempts.append({"optimizer": o["fallback_method"], "steps": r2["steps"],
                                     "final_fmax": r2["sp"]["fmax_ev_per_angstrom"]})
                    r2["steps"] = r["steps"] + r2["steps"]
                    r2["wall"] = r["wall"] + r2["wall"]
                    r = r2
                except Exception:
                    attempts.append({"optimizer": o["fallback_method"], "error": traceback.format_exc()})
            sp = r["sp"]
            converged = sp["fmax_ev_per_angstrom"] < thr
            q = int(ent.get("charge", 0))
            L = float(cfg["model"]["box_size_angstrom"])
            e_mp = -(q ** 2) * MADELUNG_SC / (2 * L) * COULOMB_EV_A
            raw = sp["energy_ev"]
            sp = dict(sp, energy_ev=raw - e_mp)  # gas-phase energy (identical to raw for neutral species)
            rec["energy_periodic_raw_ev"] = raw
            rec["makov_payne_monopole_ev"] = e_mp
            rec["energy_definition"] = ("gas-phase total energy = periodic Ewald energy minus Makov-Payne monopole "
                                        "term -q^2*alpha/(2L) (zero for neutral species); validated in chargetest.json")
            try:
                ref = upstream_aimnet_single_point(bundle.checkpoint, r["syms"], r["pos_final"], device, q)
                rec["crosscheck_upstream_aimnet_gas_phase_energy_ev"] = ref["energy_ev"]
                rec["crosscheck_delta_ev"] = sp["energy_ev"] - ref["energy_ev"]
            except Exception:
                rec["crosscheck_error"] = traceback.format_exc()
            rec.update({
                "status": "converged" if converged else "not_converged",
                "converged": converged,
                "iterations": r["steps"],
                "attempts": attempts,
                "wall_seconds": r["wall"],
                "energy_ev": sp["energy_ev"],
                "energy_hartree": sp["energy_ev"] / HARTREE_EV,
                "max_force_ev_per_angstrom": sp["fmax_ev_per_angstrom"],
                "optimizer_final_energy_ev": r["batch_energy_ev"],
                "initial_energy_ev": r["rows"][0]["energy"] if r["rows"] else None,
                "initial_fmax_ev_per_angstrom": r["rows"][0].get("fmax") if r["rows"] else None,
                "charges_e": sp["charges_e"],
                "forces_ev_per_angstrom": sp["forces_ev_per_angstrom"],
                "symbols": r["syms"],
                "initial_positions_angstrom": r["pos_initial"].tolist(),
                "optimized_positions_angstrom": r["pos_final"].tolist(),
                "finished_at_utc": now(),
            })
            finite = math.isfinite(rec["energy_ev"]) and bool(np.isfinite(sp["forces_ev_per_angstrom"]).all())
            rec["min_interatomic_distance_initial_angstrom"] = _min_dist(r["pos_initial"])
            rec["min_interatomic_distance_final_angstrom"] = _min_dist(r["pos_final"])
            if not finite:
                rec["status"] = "failed_nonfinite"
                rec["converged"] = False
            elif rec["min_interatomic_distance_final_angstrom"] < MIN_PHYSICAL_DISTANCE:
                # ML potentials can have spurious low-energy "holes" at non-physical geometries
                rec["status"] = "failed_unphysical_geometry"
                rec["converged"] = False
            write_xyz(out / "xyz" / f"{sid}_initial.xyz", r["syms"], r["pos_initial"],
                      f"id={sid} initial (box-centred) charge=0 multiplicity=1")
            write_xyz(out / "xyz" / f"{sid}_optimized.xyz", r["syms"], r["pos_final"],
                      f"id={sid} optimized E={rec['energy_ev']:.8f} eV fmax={rec['max_force_ev_per_angstrom']:.5f} "
                      f"eV/A converged={converged} model={bundle.checkpoint}+Ewald+D3BJ")
            with open(out / "traj" / f"{sid}.csv", "w") as fh:
                keys = sorted({k for row in r["rows"] for k in row})
                fh.write(",".join(keys) + "\n")
                fh.writelines(",".join(str(row.get(k, "")) for k in keys) + "\n" for row in r["rows"])
            log(f"[{i + 1}/{len(entries)}] {sid}: E={rec['energy_ev']:.6f} eV fmax={rec['max_force_ev_per_angstrom']:.4f} "
                f"steps={r['steps']} {rec['status']} ({r['wall']:.1f}s)")
        except Exception:
            n_fail += 1
            rec.update({"status": "failed", "converged": False, "error": traceback.format_exc(),
                        "finished_at_utc": now()})
            log(f"[{i + 1}/{len(entries)}] {sid}: FAILED\n{rec['error']}")
        (out / "json" / f"{sid}.json").write_text(json.dumps(rec, indent=1))
        summary.append({k: rec.get(k) for k in ("id", "kind", "acid", "n_water", "conformer_index", "status",
                                                 "energy_ev", "max_force_ev_per_angstrom", "iterations",
                                                 "wall_seconds")})
        (out / "summary.json").write_text(json.dumps({"updated_at_utc": now(), "device": device,
                                                     "versions": versions(), "structures": summary}, indent=1))
    log(f"optimize done: {len(entries) - n_fail} ok, {n_fail} failed")
    return 0 if n_fail == 0 else 1


def mode_md(cfg, device, ids: list[str] | None, temps: list[float] | None, n_steps: int | None,
            seed: int | None = None) -> int:
    from nvalchemi.data import Batch
    from nvalchemi.dynamics import NVTLangevin
    from nvalchemi.dynamics.base import DynamicsStage
    from nvalchemi.dynamics.hooks import LoggingHook

    md = cfg["md"]
    out = RESULTS / "md"
    out.mkdir(parents=True, exist_ok=True)
    bundle = ModelBundle(cfg, device)
    temps = temps or md["temperatures_k"]
    seed = int(seed if seed is not None else md["seed"])
    n_steps = n_steps or int(md["n_steps"])
    if not ids:
        raise SystemExit("md mode needs --ids (optimised structures to start from)")
    n_fail = 0
    for sid in ids:
        res = json.loads((RESULTS / "results" / "json" / f"{sid}.json").read_text())
        syms = res["symbols"]
        x0 = np.array(res["optimized_positions_angstrom"])
        for T in temps:
            tag = f"{sid}__T{T:.2f}K" + ("" if seed == int(md["seed"]) else f"__s{seed}")
            rec = {"id": tag, "start_structure": sid, "temperature_k": T, "n_steps": n_steps,
                   "timestep_fs": md["timestep_fs"], "friction_per_fs": md["friction_per_fs"],
                   "seed": seed, "integrator": "nvalchemi.dynamics.NVTLangevin (BAOAB)",
                   "checkpoint": bundle.checkpoint, "versions": versions(),
                   "provenance": f"real: ALCHEMI/AIMNet2 on {versions().get('gpu', device)}",
                   "started_at_utc": now()}
            try:
                data = make_atomic_data(syms, x0 - x0.mean(0) + cfg["model"]["box_size_angstrom"] / 2, cfg,
                                        int(res.get("charge", 0)))
                gen = torch.Generator().manual_seed(seed)
                masses = data.atomic_masses if hasattr(data, "atomic_masses") else None
                if masses is None:
                    from ase.data import atomic_masses as am

                    masses = torch.tensor([am[Z[s]] for s in syms], dtype=torch.float32)
                v = torch.randn(len(syms), 3, generator=gen) * torch.sqrt(KB_EV * T / masses)[:, None]
                v -= (masses[:, None] * v).sum(0) / masses.sum()
                data.add_node_property("velocities", v.float())
                batch = Batch.from_data_list([data], device=device)
                pipe = bundle.pipeline(batch)
                nvt = NVTLangevin(model=pipe, dt=md["timestep_fs"], temperature=T, friction=md["friction_per_fs"],
                                  random_seed=seed, n_steps=n_steps)
                for hook in pipe.make_neighbor_hooks():
                    nvt.register_hook(hook, stage=DynamicsStage.BEFORE_COMPUTE)
                rows, frames = [], []
                freq = int(md["log_frequency"])

                def writer(step, rs, _rows=rows):
                    for r in rs:
                        _rows.append({k: (float(v) if hasattr(v, "item") or isinstance(v, (int, float)) else v)
                                      for k, v in r.items()})

                frame_every = int(md.get("frame_every_steps", 20))

                class FrameHook:
                    """Capture positions every `frame_every` steps (AFTER_STEP) without restarting run()."""

                    def __init__(self, store):
                        self.frequency = frame_every
                        self.stage = DynamicsStage.AFTER_STEP
                        self.store = store

                    def __call__(self, ctx, stage):
                        self.store.append((int(getattr(ctx, "step_count", len(self.store) * frame_every)),
                                           ctx.batch.positions.detach().cpu().numpy().astype(float).copy()))

                nvt.register_hook(FrameHook(frames))
                t0 = time.time()
                with LoggingHook(backend="custom", writer_fn=writer, frequency=freq) as lh:
                    nvt.register_hook(lh)
                    batch = nvt.run(batch)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                wall = time.time() - t0
                ok = all(math.isfinite(r.get("energy", float("nan"))) for r in rows)
                temps_obs = [r["temperature"] for r in rows if "temperature" in r]
                half = len(temps_obs) // 2
                rec.update({"status": "completed" if ok else "failed_nonfinite", "wall_seconds": wall,
                            "n_frames": len(frames), "frame_every_steps": frame_every,
                            "ns_per_day": (n_steps * md["timestep_fs"] * 1e-6) / (wall / 86400.0),
                            "mean_temperature_second_half_k": float(np.mean(temps_obs[half:])) if temps_obs else None,
                            "std_temperature_second_half_k": float(np.std(temps_obs[half:])) if temps_obs else None,
                            "n_logged_rows": len(rows), "finished_at_utc": now(), "symbols": syms,
                            "fragments": res["fragments"]})
                with open(out / f"{tag}.csv", "w") as fh:
                    keys = sorted({k for row in rows for k in row})
                    fh.write(",".join(keys) + "\n")
                    fh.writelines(",".join(str(row.get(k, "")) for k in keys) + "\n" for row in rows)
                with open(out / f"{tag}_frames.xyz", "w") as fh:
                    for step, pos in frames:
                        fh.write(f"{len(syms)}\nid={tag} step={step} t_fs={step * md['timestep_fs']}\n")
                        fh.writelines(f"{s} {x:.6f} {y:.6f} {z:.6f}\n" for s, (x, y, z) in zip(syms, pos))
                log(f"MD {tag}: {rec['status']} <T>={rec['mean_temperature_second_half_k']} K, {wall:.1f}s")
            except Exception:
                n_fail += 1
                rec.update({"status": "failed", "error": traceback.format_exc(), "finished_at_utc": now()})
                log(f"MD {tag}: FAILED\n{rec['error']}")
            (out / f"{tag}.json").write_text(json.dumps(rec, indent=1))
    return 0 if n_fail == 0 else 1


def mode_chargetest(cfg, device) -> int:
    """Charged species in a periodic Ewald box carry a box-size-dependent self-image energy.
    Compare ALCHEMI pipeline single points for acetate(-), H3O(+) and a neutral reference at
    several box sizes with the upstream gas-phase aimnet calculator, and against the analytic
    Makov-Payne monopole term  E_MP = -q^2 * alpha_M / (2 L) * 14.399645 eV*A  (alpha_M = 2.837297)."""
    out = RESULTS / "selftest"
    out.mkdir(parents=True, exist_ok=True)
    bundle = ModelBundle(cfg, device)
    mols = {
        "acetate_anion": (-1, ["C", "C", "O", "O", "H", "H", "H"],
                          np.array([[-1.52, 0.0, 0.0], [0.03, 0.0, 0.0], [0.62, 1.12, 0.0], [0.62, -1.12, 0.0],
                                    [-1.90, 1.02, 0.0], [-1.90, -0.51, 0.88], [-1.90, -0.51, -0.88]])),
        "hydronium_cation": (1, ["O", "H", "H", "H"],
                             np.array([[0.0, 0.0, 0.10], [0.0, 0.94, -0.25], [0.81, -0.47, -0.25], [-0.81, -0.47, -0.25]])),
        "water_neutral": (0, ["O", "H", "H"], np.array([[0.0, 0.0, 0.0], [0.7572, 0.0, 0.5865], [-0.7572, 0.0, 0.5865]])),
    }
    rows = []
    for name, (q, syms, xyz) in mols.items():
        ref = upstream_aimnet_single_point(bundle.checkpoint, syms, xyz, device, q)
        for L in (40.0, 60.0, 90.0, 120.0, 180.0):
            sp = single_point(bundle, syms, xyz, cfg, q, L)
            mp = -(q ** 2) * 2.837297 / (2 * L) * 14.399645
            row = {"species": name, "charge": q, "box_angstrom": L, "alchemi_energy_ev": sp["energy_ev"],
                   "upstream_gas_phase_energy_ev": ref["energy_ev"],
                   "delta_ev": sp["energy_ev"] - ref["energy_ev"], "makov_payne_monopole_ev": mp,
                   "delta_minus_mp_ev": sp["energy_ev"] - ref["energy_ev"] - mp,
                   "charges_sum_e": None if sp["charges_e"] is None else float(sum(sp["charges_e"])),
                   "fmax_alchemi": sp["fmax_ev_per_angstrom"], "fmax_upstream": ref["fmax_ev_per_angstrom"],
                   "max_abs_force_diff": float(np.abs(np.array(sp["forces_ev_per_angstrom"])
                                                      - np.array(ref["forces_ev_per_angstrom"])).max())}
            log(json.dumps(row))
            rows.append(row)
    (out / "chargetest.json").write_text(json.dumps({"generated_at_utc": now(), "versions": versions(),
                                                     "rows": rows}, indent=2))
    return 0


def mode_crosscheck(cfg, device) -> int:
    """Re-evaluate every optimised structure with the upstream gas-phase aimnet AIMNet2Calculator
    and compare with the stored ALCHEMI gas-phase energy/forces (writes a separate file; the
    per-structure result JSONs are not modified)."""
    from aimnet.calculators import AIMNet2Calculator

    calc = AIMNet2Calculator(cfg["model"]["checkpoint"], device=device)
    rows = []
    for p in sorted((RESULTS / "results" / "json").glob("*.json")):
        r = json.loads(p.read_text())
        if r.get("optimized_positions_angstrom") is None or r.get("energy_ev") is None:
            continue
        syms, x = r["symbols"], np.array(r["optimized_positions_angstrom"])
        q = int(r.get("charge", 0))
        res = calc({"coord": torch.tensor(x, dtype=torch.float32, device=device),
                    "numbers": torch.tensor([Z[s_] for s_ in syms], dtype=torch.long, device=device),
                    "charge": torch.tensor([float(q)], device=device)}, forces=True)
        e = float(res["energy"].detach().reshape(-1)[0].cpu())
        f = res["forces"].detach().cpu().reshape(-1, 3).numpy().astype(float)
        fa = np.array(r["forces_ev_per_angstrom"])
        rows.append({"id": r["id"], "charge": q, "alchemi_energy_ev": r["energy_ev"], "upstream_energy_ev": e,
                     "delta_energy_ev": r["energy_ev"] - e, "max_abs_force_diff_ev_per_angstrom": float(np.abs(fa - f).max()),
                     "upstream_fmax_ev_per_angstrom": float(np.linalg.norm(f, axis=1).max())})
    d = np.array([abs(x["delta_energy_ev"]) for x in rows])
    fd = np.array([x["max_abs_force_diff_ev_per_angstrom"] for x in rows])
    rep = {"generated_at_utc": now(), "versions": versions(), "n": len(rows),
           "max_abs_delta_energy_ev": float(d.max()) if len(d) else None,
           "mean_abs_delta_energy_ev": float(d.mean()) if len(d) else None,
           "max_abs_force_diff_ev_per_angstrom": float(fd.max()) if len(fd) else None, "rows": rows}
    (RESULTS / "results" / "crosscheck_upstream_aimnet.json").write_text(json.dumps(rep, indent=1))
    log(f"crosscheck: n={len(rows)} max|dE|={rep['max_abs_delta_energy_ev']:.2e} eV "
        f"mean|dE|={rep['mean_abs_delta_energy_ev']:.2e} eV max|dF|={rep['max_abs_force_diff_ev_per_angstrom']:.2e} eV/A")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FermentAI ALCHEMI runner (remote GPU)")
    ap.add_argument("mode", choices=["env", "selftest", "chargetest", "crosscheck", "optimize", "md"])
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--kinds", nargs="*", default=None)
    ap.add_argument("--temps", nargs="*", type=float, default=None)
    ap.add_argument("--n-steps", type=int, default=None)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=None, help="MD seed (velocities + Langevin noise)")
    args = ap.parse_args()
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        log("ERROR: CUDA not available; refusing to run on CPU (results must be GPU results)")
        return 3
    cfg = load_cfg()
    log(f"mode={args.mode} device={args.device} versions={json.dumps(versions())}")
    t0 = time.time()
    try:
        if args.mode == "env":
            mode_env(cfg, args.device)
            rc = 0
        elif args.mode == "selftest":
            rc = mode_selftest(cfg, args.device)
        elif args.mode == "chargetest":
            rc = mode_chargetest(cfg, args.device)
        elif args.mode == "crosscheck":
            rc = mode_crosscheck(cfg, args.device)
        elif args.mode == "optimize":
            rc = mode_optimize(cfg, args.device, args.ids, args.kinds)
        else:
            rc = mode_md(cfg, args.device, args.ids, args.temps, args.n_steps, args.seed)
    finally:
        log(f"total wall time {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
