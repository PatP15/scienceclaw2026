#!/usr/bin/env python
"""FermentAI campaign-2 remote runner (NVIDIA ALCHEMI / AIMNet2 on the Brev GPU).

Re-uses the validated model composition and single-point code of ``remote/run_alchemi.py``
(AIMNet2 + Ewald + DFT-D3(BJ) PipelineModelWrapper; gas-phase energies = periodic Ewald energy minus
the Makov-Payne monopole term for ions; 0.70 A unphysical-geometry guard).

Modes
-----
optimize  batched FIRE2 (``--batch-size``) over a manifest (``--inputs inputs_c2``), sharded with
          ``--shard k/N`` for parallel processes -> results/<--results-dir>/{json,xyz,traj}
anneal    batched NVT Langevin at 400 K (5 ps) of optimised minima listed in ``--ids-file``,
          snapshots every 1 ps quenched with batched FIRE2 -> new records ``<src>__aq<k>``
vib       tight FIRE2 (fmax <= 0.002 eV/A) + ASE finite-difference harmonic vibrations of the ALCHEMI
          pipeline forces + RRHO / quasi-harmonic (100 cm-1) thermochemistry -> results/vib_c2/
mdlong    batched multi-replica NVT Langevin (e.g. 3 replicas x 20 ps), float32 .npz frames
          -> results/md_long/

Every value written comes from a model evaluation in this process; failures are recorded with
their traceback (status="failed").
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_alchemi as r1  # noqa: E402

ROOT, RESULTS, log, now, versions = r1.ROOT, r1.RESULTS, r1.log, r1.now, r1.versions
Z, KB_EV, HARTREE_EV = r1.Z, r1.KB_EV, r1.HARTREE_EV
EV_TO_KCAL = 23.060547830619
CM1_TO_EV = 1.239841984e-4


def _gas_phase(e_raw: float, q: int, cfg) -> float:
    L = float(cfg["model"]["box_size_angstrom"])
    return e_raw + (q ** 2) * r1.MADELUNG_SC / (2 * L) * r1.COULOMB_EV_A


def _load_manifest(inputs: str) -> dict:
    return json.loads((ROOT / inputs / "manifest.json").read_text())


def _rows_by_graph(rows: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(int(r.get("graph_idx", 0)), []).append(r)
    return out


def _writer(rows):
    def w(step, rs):
        for r in rs:
            rows.append({k: (float(v) if hasattr(v, "item") or isinstance(v, (int, float)) else v)
                         for k, v in r.items()})
    return w


def batched_fire(bundle, items: list[tuple[list[str], np.ndarray, int]], cfg, thr: float, max_steps: int):
    """items = [(symbols, xyz, charge)] -> (final positions list, rows, steps, wall)."""
    from nvalchemi.data import Batch
    from nvalchemi.dynamics.base import ConvergenceHook
    from nvalchemi.dynamics.hooks import LoggingHook
    from nvalchemi.dynamics.optimizers.fire2 import FIRE2

    data = [r1.make_atomic_data(s, x, cfg, q) for s, x, q in items]
    batch = Batch.from_data_list(data, device=bundle.device)
    idx0 = batch.batch_idx.detach().cpu().numpy()
    pos0 = batch.positions.detach().cpu().numpy().astype(float).copy()
    pipe = bundle.pipeline(batch)
    opt = FIRE2(model=pipe, dt=cfg["optimization"]["fire2_dt"], n_steps=max_steps,
                convergence_hook=ConvergenceHook.from_fmax(threshold=thr))
    for hook in pipe.make_neighbor_hooks():
        opt.register_hook(hook)
    rows: list[dict] = []
    t0 = time.time()
    with LoggingHook(backend="custom", writer_fn=_writer(rows), frequency=1) as lh:
        opt.register_hook(lh)
        batch = opt.run(batch)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    wall = time.time() - t0
    idx = batch.batch_idx.detach().cpu().numpy()
    pos = batch.positions.detach().cpu().numpy().astype(float)
    finals = [pos[idx == i] for i in range(len(items))]
    initials = [pos0[idx0 == i] for i in range(len(items))]
    return initials, finals, rows, int(opt.step_count), wall


def _record_common(ent: dict, bundle, cfg, thr, max_steps, extra: dict | None = None) -> dict:
    rec = {k: ent.get(k) for k in ("id", "kind", "acid", "n_water", "conformer_index", "seeds", "charge",
                                    "multiplicity", "formula", "fragments", "n_atoms")}
    rec.update({"input_sha256": ent.get("sha256"),
                "input_meta": {k: v for k, v in ent.items() if k in ("species", "set", "cosolute", "partner",
                                                                      "derivation", "campaign", "derived_from")},
                "provenance": f"real: ALCHEMI/AIMNet2 on {versions().get('gpu', bundle.device)}",
                "method": {"engine": "nvalchemi-toolkit", "optimizer": "FIRE2 (batched)",
                           "model": "PipelineModelWrapper[[AIMNet2Wrapper+EwaldModelWrapper](autograd)+[DFTD3ModelWrapper]]",
                           "checkpoint": bundle.checkpoint, "d3_bj": bundle.d3_params,
                           "box_size_angstrom": cfg["model"]["box_size_angstrom"],
                           "ewald_accuracy": cfg["model"]["ewald_accuracy"],
                           "fmax_threshold_ev_per_angstrom": thr, "max_steps": max_steps,
                           "fire2_dt": cfg["optimization"]["fire2_dt"]},
                "versions": versions(), "campaign": 2})
    if extra:
        rec.update(extra)
    return rec


def finish_record(rec, bundle, cfg, syms, x0, xf, grows, steps_batch, wall, thr, outdir: Path):
    q = int(rec.get("charge") or 0)
    sp = r1.single_point(bundle, syms, xf, cfg, q)
    e_mp = -(q ** 2) * r1.MADELUNG_SC / (2 * float(cfg["model"]["box_size_angstrom"])) * r1.COULOMB_EV_A
    conv_steps = [int(r["step"]) for r in grows if float(r.get("fmax", 9e9)) < thr]
    iters = (conv_steps[0] + 1) if conv_steps else steps_batch
    fmax = sp["fmax_ev_per_angstrom"]
    converged = fmax < thr
    rec.update({
        "status": "converged" if converged else "not_converged", "converged": converged,
        "iterations": iters, "batch_steps": steps_batch, "wall_seconds_batch": wall,
        "energy_periodic_raw_ev": sp["energy_ev"], "makov_payne_monopole_ev": e_mp,
        "energy_ev": sp["energy_ev"] - e_mp, "energy_hartree": (sp["energy_ev"] - e_mp) / HARTREE_EV,
        "energy_definition": "gas-phase = periodic Ewald energy - Makov-Payne monopole (0 for neutral)",
        "max_force_ev_per_angstrom": fmax, "charges_e": sp["charges_e"],
        "forces_ev_per_angstrom": sp["forces_ev_per_angstrom"], "symbols": syms,
        "initial_positions_angstrom": np.asarray(x0).tolist(), "optimized_positions_angstrom": np.asarray(xf).tolist(),
        "initial_energy_ev": float(grows[0]["energy"]) if grows else None,
        "min_interatomic_distance_final_angstrom": r1._min_dist(xf), "finished_at_utc": now()})
    finite = math.isfinite(rec["energy_ev"]) and bool(np.isfinite(sp["forces_ev_per_angstrom"]).all())
    if not finite:
        rec.update(status="failed_nonfinite", converged=False)
    elif rec["min_interatomic_distance_final_angstrom"] < r1.MIN_PHYSICAL_DISTANCE:
        rec.update(status="failed_unphysical_geometry", converged=False)
    sid = rec["id"]
    r1.write_xyz(outdir / "xyz" / f"{sid}_initial.xyz", syms, np.asarray(x0), f"id={sid} initial charge={q}")
    r1.write_xyz(outdir / "xyz" / f"{sid}_optimized.xyz", syms, np.asarray(xf),
                 f"id={sid} optimized E={rec['energy_ev']:.8f} eV fmax={fmax:.5f} converged={converged}")
    with open(outdir / "traj" / f"{sid}.csv", "w") as fh:
        fh.write("step,energy,fmax\n")
        for r in grows:
            fh.write(f"{int(r['step'])},{r.get('energy')},{r.get('fmax')}\n")
    (outdir / "json" / f"{sid}.json").write_text(json.dumps(rec, indent=1))
    return rec


def mode_optimize(cfg, device, inputs, results_dir, shard, batch_size, kinds, ids, thr, max_steps) -> int:
    out = RESULTS / results_dir
    for d in ("json", "xyz", "traj"):
        (out / d).mkdir(parents=True, exist_ok=True)
    man = _load_manifest(inputs)
    ents = [e for e in man["structures"] if (not kinds or e["kind"] in kinds) and (not ids or e["id"] in ids)]
    k, n = shard
    ents = ents[k::n]
    ents = [e for e in ents if not (out / "json" / f"{e['id']}.json").exists()]  # resumable
    ents.sort(key=lambda e: e["n_atoms"])  # similar sizes per batch
    bundle = r1.ModelBundle(cfg, device)
    log(f"optimize shard {k}/{n}: {len(ents)} structures, batch size {batch_size}")
    n_fail = 0
    for b0 in range(0, len(ents), batch_size):
        chunk = ents[b0:b0 + batch_size]
        items = []
        for e in chunk:
            syms, xyz = r1.read_xyz(ROOT / inputs / e["xyz"])
            items.append((syms, xyz, int(e.get("charge", 0))))
        try:
            x0s, xfs, rows, steps, wall = batched_fire(bundle, items, cfg, thr, max_steps)
            byg = _rows_by_graph(rows)
            for i, e in enumerate(chunk):
                rec = _record_common(e, bundle, cfg, thr, max_steps, {"batch_size": len(chunk), "started_at_utc": now()})
                try:
                    rec = finish_record(rec, bundle, cfg, items[i][0], x0s[i], xfs[i], byg.get(i, []), steps, wall, thr, out)
                    log(f"{e['id']}: E={rec['energy_ev']:.6f} fmax={rec['max_force_ev_per_angstrom']:.4f} "
                        f"it={rec['iterations']} {rec['status']}")
                except Exception:
                    n_fail += 1
                    rec.update(status="failed", converged=False, error=traceback.format_exc())
                    (out / "json" / f"{e['id']}.json").write_text(json.dumps(rec, indent=1))
                    log(f"{e['id']}: FAILED\n{rec['error']}")
            log(f"batch {b0 // batch_size}: {len(chunk)} systems, {steps} steps, {wall:.1f}s")
        except Exception:
            n_fail += len(chunk)
            tb = traceback.format_exc()
            log(f"batch {b0 // batch_size} FAILED\n{tb}")
            for e in chunk:
                rec = _record_common(e, bundle, cfg, thr, max_steps)
                rec.update(status="failed", converged=False, error=tb, finished_at_utc=now())
                (out / "json" / f"{e['id']}.json").write_text(json.dumps(rec, indent=1))
    log(f"optimize shard done: {n_fail} failed")
    return 0 if n_fail == 0 else 1


def _velocities(syms, T, seed):
    from ase.data import atomic_masses as am

    m = torch.tensor([am[Z[s]] for s in syms], dtype=torch.float32)
    g = torch.Generator().manual_seed(int(seed))
    v = torch.randn(len(syms), 3, generator=g) * torch.sqrt(KB_EV * T / m)[:, None]
    v -= (m[:, None] * v).sum(0) / m.sum()
    return v.float()


def _nvt_batch(bundle, cfg, starts: list[tuple[list[str], np.ndarray, int]], T, n_steps, dt, gamma, seeds,
               frame_every, log_every):
    from nvalchemi.data import Batch
    from nvalchemi.dynamics import NVTLangevin
    from nvalchemi.dynamics.base import DynamicsStage
    from nvalchemi.dynamics.hooks import LoggingHook

    data = []
    for (s, x, q), sd in zip(starts, seeds):
        d = r1.make_atomic_data(s, x, cfg, q)
        d.add_node_property("velocities", _velocities(s, T, sd))
        data.append(d)
    batch = Batch.from_data_list(data, device=bundle.device)
    idx = batch.batch_idx.detach().cpu().numpy()
    pipe = bundle.pipeline(batch)
    nvt = NVTLangevin(model=pipe, dt=dt, temperature=T, friction=gamma, random_seed=int(seeds[0]), n_steps=n_steps)
    for hook in pipe.make_neighbor_hooks():
        nvt.register_hook(hook, stage=DynamicsStage.BEFORE_COMPUTE)
    frames: list[tuple[int, np.ndarray]] = []

    class FrameHook:
        def __init__(self):
            self.frequency = frame_every
            self.stage = DynamicsStage.AFTER_STEP

        def __call__(self, ctx, stage):
            frames.append((int(getattr(ctx, "step_count", 0)),
                           ctx.batch.positions.detach().cpu().numpy().astype(np.float32).copy()))

    nvt.register_hook(FrameHook())
    rows: list[dict] = []
    t0 = time.time()
    with LoggingHook(backend="custom", writer_fn=_writer(rows), frequency=log_every) as lh:
        nvt.register_hook(lh)
        batch = nvt.run(batch)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    return frames, rows, idx, time.time() - t0


def mode_anneal(cfg, device, inputs, results_dir, ids_file, source_dirs) -> int:
    a = cfg["campaign2"]["anneal"]
    opt = cfg["campaign2"]["optimization"]
    out = RESULTS / results_dir
    for d in ("json", "xyz", "traj"):
        (out / d).mkdir(parents=True, exist_ok=True)
    groups = json.loads((ROOT / ids_file).read_text())  # {group_name: [source ids]}
    bundle = r1.ModelBundle(cfg, device)
    n_fail = 0
    for gname, src_ids in groups.items():
        try:
            srcs = []
            for sid in src_ids:
                p = next(RESULTS / d / "json" / f"{sid}.json" for d in source_dirs
                         if (RESULTS / d / "json" / f"{sid}.json").exists())
                srcs.append(json.loads(p.read_text()))
            starts = [(r["symbols"], np.array(r["optimized_positions_angstrom"]), int(r.get("charge", 0))) for r in srcs]
            seeds = [int(a["seed"]) + 17 * i for i in range(len(starts))]
            frames, rows, idx, wall = _nvt_batch(bundle, cfg, starts, float(a["temperature_k"]), int(a["n_steps"]), 0.5,
                                                 float(a["friction_per_fs"]), seeds, int(a["snapshot_every_steps"]), 100)
            log(f"anneal {gname}: {len(starts)} systems x {a['n_steps']} steps at {a['temperature_k']} K, "
                f"{len(frames)} snapshots, {wall:.1f}s")
            items, meta = [], []
            for step, pos in frames:
                for i, r in enumerate(srcs):
                    items.append((r["symbols"], pos[idx == i].astype(float), int(r.get("charge", 0))))
                    meta.append((r, step))
            thr = float(opt["fmax_ev_per_angstrom"])
            bs = int(opt["batch_size"])
            for b0 in range(0, len(items), bs):
                x0s, xfs, qrows, steps, w2 = batched_fire(bundle, items[b0:b0 + bs], cfg, thr, int(opt["max_steps"]))
                byg = _rows_by_graph(qrows)
                for j in range(len(x0s)):
                    r, step = meta[b0 + j]
                    k = int(step) // int(a["snapshot_every_steps"])
                    ent = {kk: r.get(kk) for kk in ("kind", "acid", "n_water", "conformer_index", "seeds", "charge",
                                                     "multiplicity", "formula", "fragments", "n_atoms")}
                    ent["id"] = f"{r['id']}__aq{k}"
                    ent["sha256"] = None
                    rec = _record_common(ent, bundle, cfg, thr, int(opt["max_steps"]), {
                        "origin": {"anneal_from": r["id"], "anneal_temperature_k": a["temperature_k"],
                                   "snapshot_step": int(step), "snapshot_time_ps": int(step) * 0.5 / 1000.0,
                                   "anneal_seed": seeds[srcs.index(r)], "group": gname},
                        "input_meta": {**(r.get("input_meta") or {}), "set": "anneal_quench"}})
                    rec = finish_record(rec, bundle, cfg, items[b0 + j][0], x0s[j], xfs[j], byg.get(j, []), steps, w2,
                                        thr, out)
                    log(f"  {rec['id']}: E={rec['energy_ev']:.6f} it={rec['iterations']} {rec['status']}")
            with open(out / "traj" / f"anneal_{gname}.csv", "w") as fh:
                keys = sorted({k for row in rows for k in row})
                fh.write(",".join(keys) + "\n")
                for row in rows:
                    fh.write(",".join(str(row.get(k, "")) for k in keys) + "\n")
        except Exception:
            n_fail += 1
            log(f"anneal {gname} FAILED\n{traceback.format_exc()}")
    return 0 if n_fail == 0 else 1


class AlchemiASECalculator:
    """Minimal ASE calculator around the ALCHEMI pipeline single point (gas-phase energy, forces)."""

    def __new__(cls, bundle, cfg, charge):
        from ase.calculators.calculator import Calculator, all_changes

        class _C(Calculator):
            implemented_properties = ["energy", "forces"]

            def calculate(self, atoms=None, properties=("energy",), system_changes=all_changes):
                super().calculate(atoms, properties, system_changes)
                sp = r1.single_point(bundle, list(self.atoms.get_chemical_symbols()), self.atoms.positions, cfg, charge)
                self.results = {"energy": _gas_phase(sp["energy_ev"], charge, cfg),
                                "forces": np.array(sp["forces_ev_per_angstrom"])}

        return _C()


def thermo(atoms, freqs_cm1: np.ndarray, T: float, P: float, qh_cut: float) -> dict:
    """RRHO and quasi-harmonic (raise real modes < qh_cut to qh_cut) ideal-gas thermochemistry (eV)."""
    from ase.thermochemistry import IdealGasThermo

    n = len(atoms)
    order = np.argsort(np.abs(freqs_cm1))
    vib = freqs_cm1[order][6:]  # drop 3 translations + 3 rotations (smallest |nu|)
    n_imag = int((vib < 0).sum())
    real = vib[vib > 0]
    out = {"n_modes": int(len(vib)), "n_imag": n_imag, "imag_cm1": vib[vib < 0].tolist(),
           "lowest_real_cm1": float(real.min()) if len(real) else None, "n_atoms": n}
    for tag, nu in (("rrho", real), ("qh", np.concatenate([np.maximum(real, qh_cut), np.full(n_imag, qh_cut)]))):
        e = (nu * CM1_TO_EV).tolist()
        th = IdealGasThermo(vib_energies=e, geometry="nonlinear", atoms=atoms, symmetrynumber=1, spin=0,
                            potentialenergy=0.0, ignore_imag_modes=True, vib_selection="all")
        zpe = 0.5 * float(np.sum(nu)) * CM1_TO_EV
        out[tag] = {"zpe_ev": zpe, "h_corr_ev": float(th.get_enthalpy(T, verbose=False)),
                    "s_ev_per_k": float(th.get_entropy(T, P, verbose=False)),
                    "g_corr_ev": float(th.get_gibbs_energy(T, P, verbose=False))}
    return out


def mode_vib(cfg, device, ids, ids_file, source_dirs) -> int:
    from ase import Atoms
    from ase.vibrations import Vibrations

    v = cfg["campaign2"]["vib"]
    out = RESULTS / "vib_c2"
    out.mkdir(parents=True, exist_ok=True)
    if ids_file:
        ids = json.loads((ROOT / ids_file).read_text())
    bundle = r1.ModelBundle(cfg, device)
    n_fail = 0
    for sid in ids:
        if (out / f"{sid}.json").exists():
            continue
        rec = {"id": sid, "started_at_utc": now(), "versions": versions(), "checkpoint": bundle.checkpoint,
               "provenance": f"real: ALCHEMI/AIMNet2 on {versions().get('gpu', device)}",
               "method": {"tight_optimizer": "FIRE2", "tight_fmax_ev_per_angstrom": v["tight_fmax_ev_per_angstrom"],
                          "hessian": f"ASE Vibrations finite differences of ALCHEMI forces, delta={v['delta_angstrom']} A, "
                                     f"nfree={v['nfree']}", "thermo": "ase.thermochemistry.IdealGasThermo, nonlinear, "
                                     "symmetry number 1, spin 0; qh = real modes < cutoff raised to cutoff",
                          "temperature_k": v["temperature_k"], "pressure_pa": v["pressure_pa"],
                          "quasi_harmonic_cutoff_cm1": v["quasi_harmonic_cutoff_cm1"]}}
        try:
            src = next(json.loads((RESULTS / d / "json" / f"{sid}.json").read_text()) for d in source_dirs
                       if (RESULTS / d / "json" / f"{sid}.json").exists())
            q = int(src.get("charge", 0))
            syms = src["symbols"]
            x0, xf, rows, steps, wall = batched_fire(bundle, [(syms, np.array(src["optimized_positions_angstrom"]), q)],
                                                     cfg, float(v["tight_fmax_ev_per_angstrom"]), int(v["tight_max_steps"]))
            xt = xf[0]
            sp = r1.single_point(bundle, syms, xt, cfg, q)
            atoms = Atoms(symbols=syms, positions=xt - xt.mean(0))
            atoms.calc = AlchemiASECalculator(bundle, cfg, q)
            t0 = time.time()
            vib = Vibrations(atoms, name=str(out / f"_cache_{sid}"), delta=float(v["delta_angstrom"]), nfree=int(v["nfree"]))
            vib.clean()
            vib.run()
            freqs = vib.get_frequencies()  # cm-1, complex for imaginary
            vib.clean()
            f = np.array([(-abs(z.imag) if abs(z.imag) > abs(z.real) else z.real) for z in np.asarray(freqs, complex)])
            th = thermo(atoms, f, float(v["temperature_k"]), float(v["pressure_pa"]),
                        float(v["quasi_harmonic_cutoff_cm1"]))
            e_gas = _gas_phase(sp["energy_ev"], q, cfg)
            rec.update({"status": "completed", "charge": q, "symbols": syms, "fragments": src.get("fragments"),
                        "kind": src.get("kind"), "acid": src.get("acid"), "n_water": src.get("n_water"),
                        "source_energy_ev": src.get("energy_ev"), "tight_energy_ev": e_gas,
                        "tight_fmax_ev_per_angstrom": sp["fmax_ev_per_angstrom"], "tight_steps": steps,
                        "tight_positions_angstrom": np.asarray(xt).tolist(), "frequencies_cm1": f.tolist(),
                        "thermo": th, "vib_wall_seconds": time.time() - t0,
                        "G_rrho_ev": e_gas + th["rrho"]["g_corr_ev"], "G_qh_ev": e_gas + th["qh"]["g_corr_ev"],
                        "E_plus_zpe_ev": e_gas + th["rrho"]["zpe_ev"], "finished_at_utc": now()})
            log(f"vib {sid}: E={e_gas:.6f} fmax={sp['fmax_ev_per_angstrom']:.4f} n_imag={th['n_imag']} "
                f"lowest={th['lowest_real_cm1']} G_qh-E={th['qh']['g_corr_ev'] * EV_TO_KCAL:.2f} kcal/mol")
        except Exception:
            n_fail += 1
            rec.update(status="failed", error=traceback.format_exc(), finished_at_utc=now())
            log(f"vib {sid}: FAILED\n{rec['error']}")
        (out / f"{sid}.json").write_text(json.dumps(rec, indent=1))
    return 0 if n_fail == 0 else 1


def mode_mdlong(cfg, device, ids, temps, source_dirs, n_steps) -> int:
    m = cfg["campaign2"]["md_long"]
    out = RESULTS / "md_long"
    out.mkdir(parents=True, exist_ok=True)
    bundle = r1.ModelBundle(cfg, device)
    temps = temps or m["temperatures_k"]
    n_steps = int(n_steps or m["n_steps"])
    n_fail = 0
    for sid in ids:
        src = next(json.loads((RESULTS / d / "json" / f"{sid}.json").read_text()) for d in source_dirs
                   if (RESULTS / d / "json" / f"{sid}.json").exists())
        syms, x0, q = src["symbols"], np.array(src["optimized_positions_angstrom"]), int(src.get("charge", 0))
        for T in temps:
            tag = f"{sid}__T{T:.2f}K__{n_steps * m['timestep_fs'] / 1000:.0f}ps"
            seeds = [int(m["seeds_base"]) + 101 * i for i in range(int(m["replicas"]))]
            rec = {"id": tag, "start_structure": sid, "temperature_k": T, "n_steps": n_steps,
                   "timestep_fs": m["timestep_fs"], "friction_per_fs": m["friction_per_fs"], "seeds": seeds,
                   "replicas": len(seeds), "integrator": "nvalchemi.dynamics.NVTLangevin (BAOAB), batched replicas",
                   "checkpoint": bundle.checkpoint, "versions": versions(), "charge": q, "symbols": syms,
                   "fragments": src.get("fragments"), "provenance": f"real: ALCHEMI/AIMNet2 on {versions().get('gpu', device)}",
                   "started_at_utc": now()}
            try:
                frames, rows, idx, wall = _nvt_batch(bundle, cfg, [(syms, x0, q)] * len(seeds), T, n_steps,
                                                     float(m["timestep_fs"]), float(m["friction_per_fs"]), seeds,
                                                     int(m["frame_every_steps"]), int(m["log_frequency"]))
                steps = np.array([s for s, _ in frames], dtype=np.int64)
                arr = np.stack([np.stack([p[idx == i] for i in range(len(seeds))]) for _, p in frames])  # F,R,N,3
                np.savez_compressed(out / f"{tag}_frames.npz", positions=arr.astype(np.float32), steps=steps,
                                    timestep_fs=m["timestep_fs"], symbols=np.array(syms))
                byg = _rows_by_graph(rows)
                for i in range(len(seeds)):
                    with open(out / f"{tag}__r{i}.csv", "w") as fh:
                        keys = sorted({k for row in byg.get(i, []) for k in row})
                        fh.write(",".join(keys) + "\n")
                        for row in byg.get(i, []):
                            fh.write(",".join(str(row.get(k, "")) for k in keys) + "\n")
                ok = all(math.isfinite(float(r.get("energy", float("nan")))) for r in rows)
                tem = [[float(r["temperature"]) for r in byg.get(i, []) if "temperature" in r] for i in range(len(seeds))]
                rec.update({"status": "completed" if ok else "failed_nonfinite", "wall_seconds": wall,
                            "n_frames": int(len(frames)), "frame_every_steps": m["frame_every_steps"],
                            "mean_temperature_second_half_k": [float(np.mean(t[len(t) // 2:])) if t else None for t in tem],
                            "ns_per_day_per_replica": (n_steps * m["timestep_fs"] * 1e-6) / (wall / 86400.0),
                            "finished_at_utc": now()})
                log(f"mdlong {tag}: {rec['status']} {wall:.0f}s <T>={rec['mean_temperature_second_half_k']}")
            except Exception:
                n_fail += 1
                rec.update(status="failed", error=traceback.format_exc(), finished_at_utc=now())
                log(f"mdlong {tag}: FAILED\n{rec['error']}")
            (out / f"{tag}.json").write_text(json.dumps(rec, indent=1))
    return 0 if n_fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="FermentAI campaign-2 ALCHEMI runner (remote GPU)")
    ap.add_argument("mode", choices=["optimize", "anneal", "vib", "mdlong"])
    ap.add_argument("--inputs", default="inputs_c2")
    ap.add_argument("--results-dir", default="results_c2")
    ap.add_argument("--source-dirs", nargs="*", default=["results_c2", "results"])
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--kinds", nargs="*", default=None)
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--ids-file", default=None)
    ap.add_argument("--temps", nargs="*", type=float, default=None)
    ap.add_argument("--n-steps", type=int, default=None)
    ap.add_argument("--device", default="cuda:0")
    a = ap.parse_args()
    if not torch.cuda.is_available():
        log("ERROR: CUDA not available; refusing to run on CPU")
        return 3
    cfg = r1.load_cfg()
    o = cfg["campaign2"]["optimization"]
    log(f"c2 mode={a.mode} versions={json.dumps(versions())}")
    t0 = time.time()
    try:
        if a.mode == "optimize":
            k, n = (int(v) for v in a.shard.split("/"))
            rc = mode_optimize(cfg, a.device, a.inputs, a.results_dir, (k, n), a.batch_size or int(o["batch_size"]),
                               a.kinds, a.ids, float(o["fmax_ev_per_angstrom"]), int(o["max_steps"]))
        elif a.mode == "anneal":
            rc = mode_anneal(cfg, a.device, a.inputs, a.results_dir, a.ids_file, a.source_dirs)
        elif a.mode == "vib":
            rc = mode_vib(cfg, a.device, a.ids, a.ids_file, a.source_dirs)
        else:
            rc = mode_mdlong(cfg, a.device, a.ids, a.temps, a.source_dirs, a.n_steps)
    finally:
        log(f"total wall time {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
