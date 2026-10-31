"""Regenerate the Formlabs crumb models deterministically.

Two families of models (both by default):

* baseline presets (mild / sour): hand-picked illustrative parameters (crumb.MILD / crumb.SOUR)
* simulation-derived chamber specimens (A / B): ALCHEMI descriptors -> kinetics at 16 h ->
  morphology_map.map_to_crumb_params -> STL (see docs/morphology_coupling.md)

Outputs (data/stl/), baseline presets:
  crumb_mild.stl, crumb_sour.stl                 binary STL, millimetres
  crumb_<name>_validation.json                   ValidationReport per model
  crumb_<name>.png                               per-model render (top-down, oblique, histogram)
  crumb_comparison.png                           side-by-side render
  crumb_summary.json / crumb_summary.csv         geometric summary statistics
  crumb_params.json                              exact generator parameters + disclaimer

Outputs (data/stl/), simulation-derived chamber specimens:
  crumb_chamber_{A,B}.stl                        binary STL, millimetres
  crumb_chamber_{A,B}_validation.json            ValidationReport per model
  crumb_chamber_{A,B}_mapping.json               MappingReport (inputs, formulas, provenance)
  crumb_chamber_comparison.png                   side-by-side render
  chain_summary.json                             STL <-> kinetics numbers <-> ALCHEMI provenance

Usage:  uv run python scripts/generate_stls.py [--out data/stl] [--skip-validation]
                                               [--from-simulation | --presets-only]
                                               [--predictions data/predictions]
  (default: both families; --from-simulation: chamber specimens only;
   --presets-only: the original mild/sour baseline only)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from fermentai.geometry.crumb import DISCLAIMER, MILD, SOUR, generate_crumb
from fermentai.geometry.image_analysis import compare_to_params
from fermentai.geometry.render import mesh_summary, render_comparison, render_single
from fermentai.geometry.validation import _jsonable, validate_mesh

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "stl"))
    ap.add_argument("--skip-validation", action="store_true")
    ap.add_argument("--predictions", default=str(ROOT / "data" / "predictions"),
                    help="directory with chamber_<id>_morphology.json (simulation mode)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--from-simulation", action="store_true",
                      help="only the simulation-derived chamber specimens (A, B)")
    mode.add_argument("--presets-only", action="store_true",
                      help="only the original mild/sour baseline presets")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ok = True
    if not args.from_simulation:
        ok &= run_presets(out, args.skip_validation)
    if not args.presets_only:
        ok &= run_simulation(out, Path(args.predictions), args.skip_validation)
    return 0 if ok else 1


def run_simulation(out: Path, predictions: Path, skip_validation: bool) -> bool:
    """Chamber A/B specimens derived from the kinetics chain (morphology_map)."""
    from fermentai.geometry.morphology_map import generate_chain

    t0 = time.time()
    res = generate_chain(("A", "B"), predictions, out, validate=not skip_validation)
    print(f"[chain] {time.time() - t0:.1f}s -> {res['render']}, {res['chain_summary']}")
    if skip_validation:
        return True
    ok = all(sp.validation.passed for sp in res["specimens"])
    for sp in res["specimens"]:
        failed = [k for k, c in sp.validation.checks.items() if c.passed is False]
        print(f"   chamber {sp.chamber_id}: {'PASS' if not failed else 'FAIL ' + str(failed)}")
    print("CHAMBER VALIDATION PASSED" if ok else "CHAMBER VALIDATION FAILURES - see JSON reports")
    return ok


def run_presets(out: Path, skip_validation: bool) -> bool:
    """Original baseline: hand-picked mild/sour illustrative presets (unchanged outputs)."""
    items, summaries, all_ok = [], [], True
    for params in (MILD, SOUR):
        t0 = time.time()
        mesh, cf = generate_crumb(params, return_field=True)
        stl = out / f"crumb_{params.name}.stl"
        mesh.export(stl)
        print(f"[{params.name}] generated {len(mesh.faces):,} triangles in {time.time()-t0:.1f}s "
              f"-> {stl} ({stl.stat().st_size/1e6:.2f} MB)")

        s = mesh_summary(mesh, params)
        s["stl_file"] = stl.name
        s["stl_bytes"] = stl.stat().st_size
        s["generator_stats"] = cf.stats
        s["self_consistency_vs_params"] = compare_to_params(
            {"pore_area_fraction": s["cut_face_pore_area_fraction"],
             "eq_diameter_mm": s["cut_face_eq_diameter_mm"], "provenance": "simulated"},
            params,
        )
        if not skip_validation:
            rep = validate_mesh(mesh, params.min_wall_mm, name=params.name)
            rep.save(out / f"crumb_{params.name}_validation.json")
            s["validation_passed"] = rep.passed
            all_ok &= rep.passed
            for k, c in rep.checks.items():
                flag = {True: "PASS", False: "FAIL", None: "N/A "}[c.passed]
                print(f"   {flag} {k}{'' if c.exact else ' (approx.)'}")
        items.append((params, s))
        summaries.append(s)

    render_comparison(items, out / "crumb_comparison.png")
    for params, s in items:
        render_single(params, s, out / f"crumb_{params.name}.png")

    public = [{k: v for k, v in s.items() if not k.startswith("_")} for s in summaries]
    (out / "crumb_summary.json").write_text(json.dumps(_jsonable(
        {"provenance": "simulated", "disclaimer": DISCLAIMER, "models": public}), indent=2))
    cols = ["name", "provenance", "porosity_target", "cut_face_pore_area_fraction",
            "porosity_3d_void_fraction", "cut_face_pore_count", "cut_face_pore_density_per_cm2",
            "eqd_mean_mm", "eqd_sd_mm", "eqd_median_mm", "eqd_p90_mm", "mean_pore_depth_mm",
            "volume_mm3", "surface_area_mm2", "extent_x_mm", "extent_y_mm", "extent_z_mm",
            "triangle_count", "stl_bytes", "validation_passed"]
    with open(out / "crumb_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in public:
            eq = s["cut_face_eq_diameter_mm"] or {}
            row = {k: s.get(k) for k in cols}
            row.update(eqd_mean_mm=eq.get("mean"), eqd_sd_mm=eq.get("sd"),
                       eqd_median_mm=eq.get("median"), eqd_p90_mm=eq.get("p90"),
                       extent_x_mm=s["extents_mm"][0], extent_y_mm=s["extents_mm"][1],
                       extent_z_mm=s["extents_mm"][2])
            w.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()})
    (out / "crumb_params.json").write_text(json.dumps(
        {"provenance": "simulated", "disclaimer": DISCLAIMER,
         "models": {p.name: p.to_dict() for p, _ in items}}, indent=2))
    print("summary:", out / "crumb_summary.csv")
    if skip_validation:
        print("validation skipped")
    else:
        print("ALL VALIDATION PASSED" if all_ok else "VALIDATION FAILURES - see JSON reports")
    return all_ok


if __name__ == "__main__":
    sys.exit(main())
