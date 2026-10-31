#!/usr/bin/env python
"""Run the two baseline sourdough scenarios (Chamber A 22 degC, Chamber B 27 degC).

Usage:  uv run python scripts/simulate_loaves.py [--samples 128] [--seed 0] [--horizon 24]
                                                 [--out data/predictions] [--no-html]

Writes to data/predictions/ (all provenance "simulated"):
  chamber_{A,B}_prediction.csv          nominal model trajectory (required outputs)
  chamber_{A,B}_uncertainty.csv         5/50/95 % Monte-Carlo envelopes
  chamber_{A,B}_summary.json            key numbers
  charts/chamber_{A,B}_<var>.png        per-chamber chart set
  charts/compare_<var>.png              A-vs-B overlays
  charts/dashboard_A_vs_B.png           dashboard-style multi-panel comparison
  chamber_{A,B}_charts.html             self-contained Plotly (plotly.js inlined, no CDN)
  dashboard_A_vs_B.html                 self-contained Plotly dashboard
  sensitivity_oat_{A,B}.csv, sensitivity_global_{A,B}.csv, sensitivity.md
  run_manifest.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fermentai.fermentation import plotting, sensitivity
from fermentai.fermentation.kinetics import PROVENANCE
from fermentai.fermentation.simulation import (
    load_scenarios,
    simulate,
    simulate_with_uncertainty,
    summarize_prediction,
)


def _fmt(v, f=".2f"):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, f)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--horizon", type=float, default=None, help="hours (default: experiments.yaml)")
    ap.add_argument("--dt", type=float, default=0.05)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "predictions")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args()

    out = args.out
    charts = out / "charts"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    scenarios = load_scenarios()
    runs, summaries, files = [], {}, []
    mc: dict[str, dict] = {}
    sens_oat, sens_glob = {}, {}
    for cid, sc in scenarios.items():
        horizon = args.horizon or sc.horizon_h
        nominal = simulate(sc.schedule, t_end_h=horizon, dt_h=args.dt)
        nominal.insert(1, "chamber_id", cid)
        nominal["in_observation_window"] = nominal.time_h.between(*sc.window_h)
        p = out / f"chamber_{cid}_prediction.csv"
        nominal.to_csv(p, index=False, float_format="%.5g")
        files.append(p)

        unc = simulate_with_uncertainty(
            sc.schedule,
            n_samples=args.samples,
            seed=args.seed,
            t_end_h=horizon,
            dt_h=max(args.dt, 0.1),
        )
        env = unc["envelope"]
        env.insert(1, "chamber_id", cid)
        p = out / f"chamber_{cid}_uncertainty.csv"
        env.to_csv(p, index=False, float_format="%.5g")
        files.append(p)

        summ = summarize_prediction(nominal, sc.window_h)
        summ.update(
            chamber_id=cid,
            name=sc.name,
            hypothesis=sc.hypothesis,
            caveat=sc.caveat,
            schedule=sc.schedule.to_dict(),
            n_uncertainty_samples=args.samples,
            n_failed_samples=unc["n_failed"],
            seed=args.seed,
        )
        # envelope numbers at the window edges
        for th in sc.window_h:
            i = int(abs(env.time_h - th).idxmin())
            summ[f"ph_at_{th:g}h_p05_p95"] = [float(env.ph_p05[i]), float(env.ph_p95[i])]
        tt = unc["time_h"]
        E = unc["samples"]["expansion_ratio"]
        pk_t = tt[np.argmax(E, axis=1)]
        summ["peak_expansion_time_h_p05_p95"] = [
            float(np.quantile(pk_t, 0.05)),
            float(np.quantile(pk_t, 0.95)),
        ]
        summ["peak_expansion_ratio_p05_p95"] = [
            float(np.quantile(E.max(axis=1), 0.05)),
            float(np.quantile(E.max(axis=1), 0.95)),
        ]
        mc[cid] = {
            "ph12": np.array([np.interp(12.0, tt, r) for r in unc["samples"]["ph"]]),
            "peak_t": pk_t,
        }
        summaries[cid] = summ
        p = out / f"chamber_{cid}_summary.json"
        p.write_text(json.dumps(summ, indent=2))
        files.append(p)

        files += plotting.chamber_pngs(
            cid, nominal, env, charts, sc.window_h, n_samples=args.samples
        )
        if not args.no_html:
            files.append(
                plotting.chamber_html(
                    cid,
                    nominal,
                    env,
                    out / f"chamber_{cid}_charts.html",
                    sc.window_h,
                    n_samples=args.samples,
                )
            )
        runs.append((cid, nominal, env))

        sens_oat[cid] = sensitivity.one_at_a_time(sc.schedule, t_end_h=16.0)
        sens_glob[cid] = sensitivity.global_rank_correlation(unc)
        for name, df in (("oat", sens_oat[cid]), ("global", sens_glob[cid])):
            p = out / f"sensitivity_{name}_{cid}.csv"
            df.to_csv(p, index=False, float_format="%.5g")
            files.append(p)
        print(
            f"[{cid}] final pH {summ['final_ph']:.2f} (t={summ['t_end_h']:g} h), pH@12h {summ.get('ph_at_12h', float('nan')):.2f}, "
            f"pH@16h {summ.get('ph_at_16h', float('nan')):.2f}, peak {summ['peak_expansion_ratio']:.2f}x @ "
            f"{summ['peak_expansion_time_h']:.1f} h, overproof risk from {summ['overproof_onset_h']} h"
        )

    window = next(iter(scenarios.values())).window_h
    if {"A", "B"} <= set(mc) and len(mc["A"]["ph12"]) == len(mc["B"]["ph12"]):
        # Same seed -> identical parameter draws for both chambers: a paired comparison.
        d_ph = mc["B"]["ph12"] - mc["A"]["ph12"]
        d_pk = mc["B"]["peak_t"] - mc["A"]["peak_t"]
        comp = {
            "provenance": PROVENANCE,
            "description": "Paired Monte-Carlo comparison (same parameter draws for A and B); in-silico only.",
            "n_samples": len(d_ph),
            "ph_12h_B_minus_A_median": float(np.median(d_ph)),
            "ph_12h_B_minus_A_p05_p95": [
                float(np.quantile(d_ph, 0.05)),
                float(np.quantile(d_ph, 0.95)),
            ],
            "fraction_samples_B_lower_ph_at_12h": float(np.mean(d_ph < 0)),
            "peak_time_B_minus_A_median_h": float(np.median(d_pk)),
            "fraction_samples_B_peaks_earlier": float(np.mean(d_pk < 0)),
        }
        p = out / "comparison_summary.json"
        p.write_text(json.dumps(comp, indent=2))
        files.append(p)
        print(
            "[A vs B]",
            {
                k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in comp.items()
                if k != "description"
            },
        )
    files += plotting.comparison_pngs(runs, charts, window, n_samples=args.samples)
    files.append(
        plotting.dashboard_png(
            runs, summaries, charts / "dashboard_A_vs_B.png", window, n_samples=args.samples
        )
    )
    if not args.no_html:
        files.append(
            plotting.dashboard_html(
                runs, summaries, out / "dashboard_A_vs_B.html", window, n_samples=args.samples
            )
        )

    # sensitivity markdown
    lines = [
        "# Sensitivity analysis (simulated, in-silico)",
        "",
        "Generated by `scripts/simulate_loaves.py`. One-at-a-time (OAT): each sampled parameter set to its",
        "configured min and max with all others nominal (horizon 16 h). Global: Spearman rank correlation",
        f"over {args.samples} joint Monte-Carlo samples (seed {args.seed}). These are model sensitivities, not",
        "experimental findings.",
        "",
    ]
    for cid in scenarios:
        oat = sens_oat[cid]
        base = oat.attrs.get("baseline", {})
        lines += [
            f"## Chamber {cid} ({scenarios[cid].name})",
            "",
            f"Baseline: pH@12h {base.get('ph_12h', float('nan')):.2f}, peak expansion "
            f"{base.get('peak_expansion', float('nan')):.2f}x at {base.get('peak_time_h', float('nan')):.1f} h.",
            "",
        ]
        for o, lab, f in (
            ("ph_12h", "pH at 12 h", ".3f"),
            ("peak_expansion", "peak expansion (V/V0)", ".3f"),
            ("peak_time_h", "peak expansion time (h)", ".2f"),
        ):
            top = oat.sort_values(f"{o}_abs_swing", ascending=False).head(6)
            g = sens_glob[cid].set_index("parameter")
            lines += [
                f"### {lab}",
                "",
                "| parameter | range | value at min | value at max | swing | Spearman (global) |",
                "|---|---|---|---|---|---|",
            ]
            for _, r in top.iterrows():
                rho = (
                    g[f"{o}_spearman"].get(r.parameter, float("nan"))
                    if f"{o}_spearman" in g
                    else float("nan")
                )
                lines.append(
                    f"| `{r.parameter}` | {r['min']:g}–{r['max']:g} {r.units} | {_fmt(r[f'{o}_at_min'], f)} | "
                    f"{_fmt(r[f'{o}_at_max'], f)} | {_fmt(r[f'{o}_swing'], '+' + f)} | {_fmt(rho)} |"
                )
            lines.append("")
    p = out / "sensitivity.md"
    p.write_text("\n".join(lines))
    files.append(p)

    manifest = {
        "provenance": PROVENANCE,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator": "scripts/simulate_loaves.py",
        "model": "fermentai.fermentation (SciPy LSODA ODE), configs/kinetics.yaml",
        "n_uncertainty_samples": args.samples,
        "seed": args.seed,
        "runtime_s": round(time.time() - t0, 2),
        "note": "All values are simulated. Model CO2 is modelled production; the BME680 does not measure CO2.",
        "files": [str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f) for f in files],
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(files)} files to {out} in {manifest['runtime_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
