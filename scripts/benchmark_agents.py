"""IN-SILICO BENCHMARK — simulated worlds: independent vs collaborative scientist agents (PLAN §10).

    uv run python scripts/benchmark_agents.py                 # 30 well-specified + 30 misspecified worlds
    uv run python scripts/benchmark_agents.py --worlds 4 --misspec-worlds 0 --rounds 2   # quick look

Outputs (data/benchmarks/ by default): benchmark_results.csv (world x variant x arm x round),
summary.json (mean ± 95% CI per arm/round + paired collaborative-minus-independent differences),
time_to_objective.csv, run_manifest.json and PNG figures. Every artifact is labelled simulated.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from fermentai.design import optimizer as O
from fermentai.design.benchmark import (
    HEADLINE_METRICS,
    BenchConfig,
    run_world,
    summarize,
    time_to_objective,
)
from fermentai.design.world import BENCHMARK_LABEL, FREE_PARAMS, HELD_OUT

COLORS = {"independent": "#2a78d6", "collaborative": "#eb6834"}  # validated categorical slots 1-2
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SEL_TITLE = {"agent": "Deployed agent policy (offline_policy)", "info": "Information-aware selector (optimizer)"}


def _job(args):
    seed, cfg, lam = args
    return run_world(seed, cfg, lam)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _banner(fig, subtitle):
    fig.patch.set_facecolor(SURFACE)
    fig.text(0.01, 0.995, BENCHMARK_LABEL, ha="left", va="top", fontsize=11, fontweight="bold", color=INK)
    fig.text(0.01, 0.965, subtitle, ha="left", va="top", fontsize=8.5, color=INK2)


CURVE_METRICS = [
    ("pooled_sd_ratio_mean", "Param. sd / prior sd\n(all team data)"),
    ("pooled_info_gain_nats", "Information gained\n(nats, all team data)"),
    ("agent_rmse_ph", "Held-out pH RMSE\n(agents' own models)"),
    ("agent_rmse_expansion", "Held-out rise RMSE\n(V/V0, agents' own models)"),
    ("agent_regret", "Decision regret\n(true objective cost)"),
    ("redundant_cum", "Redundant experiments\n(cumulative)"),
]


def fig_curves(summary, variant, out: Path):
    import matplotlib.pyplot as plt

    vs = summary["variants"][variant]
    sels = [s for s in ("agent", "info") if f"{s}/independent" in vs["arms"]]
    fig, axes = plt.subplots(len(CURVE_METRICS), len(sels), figsize=(4.4 * len(sels) + 1.2, 13.5), squeeze=False)
    _banner(fig, f"{variant.replace('_', '-')} worlds, n = {vs['n_worlds']}; mean ± 95% CI over worlds; "
                 f"budget 2 chambers x {summary['config']['n_rounds']} rounds")
    for j, sel in enumerate(sels):
        for i, (m, lab) in enumerate(CURVE_METRICS):
            ax = axes[i][j]
            _style(ax)
            for mode, ls in (("independent", "-"), ("collaborative", "--")):
                br = vs["arms"][f"{sel}/{mode}"]["by_round"]
                r = sorted(int(k) for k in br)
                mu = np.array([br[k][m]["mean"] for k in r])
                lo = np.array([br[k][m]["lo"] for k in r])
                hi = np.array([br[k][m]["hi"] for k in r])
                x = np.array(r) * 2
                lo = np.maximum(lo, 0.0)  # every plotted metric is non-negative
                ax.fill_between(x, lo, hi, color=COLORS[mode], alpha=0.15, linewidth=0)
                ax.plot(x, mu, ls, color=COLORS[mode], lw=2, marker="o", ms=4.5, label=mode,
                        markeredgecolor=SURFACE, markeredgewidth=1)
            if j == 0:
                ax.set_ylabel(lab, fontsize=8.5, color=INK)
            if i == 0:
                ax.set_title(SEL_TITLE[sel], fontsize=9.5, color=INK, loc="left")
            if i == len(CURVE_METRICS) - 1:
                ax.set_xlabel("experiments run (both chambers)", fontsize=8.5, color=INK)
            ax.set_xticks(np.arange(0, 2 * summary["config"]["n_rounds"] + 1, 2))
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=2, frameon=False, fontsize=9, bbox_to_anchor=(0.99, 0.995))
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


PAIRED_METRICS = [
    "redundant_cum", "distinct_schedules", "pooled_sd_ratio_mean", "pooled_info_gain_nats",
    "agent_sd_ratio_mean", "agent_info_gain_nats", "pooled_rmse_ph", "pooled_rmse_expansion",
    "agent_rmse_ph", "agent_rmse_expansion", "agent_regret", "pooled_regret",
]


def reference_scale(vs, sel, m, budget):
    """Scale used to express a paired difference in %: counts -> experiment budget; information ->
    independent-arm final mean; error-type metrics (sd ratio, RMSE, regret, param error) -> their
    prior (round-0, no data) value, i.e. 'percent of the initial error'."""
    if m in ("redundant_cum", "distinct_schedules"):
        return float(budget)
    if "info" in m:
        return abs(vs["arms"][f"{sel}/independent"]["final"][m]["mean"]) or 1.0
    return abs(vs["arms"][f"{sel}/independent"]["by_round"][0][m]["mean"]) or 1.0


def fig_paired(summary, variant, out: Path):
    """Paired difference (collaborative - independent) at the final round, in % of a per-metric reference
    scale (:func:`reference_scale`), sign-adjusted so that > 0 always means 'collaborative better'."""
    import matplotlib.pyplot as plt

    vs = summary["variants"][variant]
    sels = [s for s in ("agent", "info") if s in vs["paired_final"]]
    fig, axes = plt.subplots(1, len(sels), figsize=(5.2 * len(sels) + 1.5, 6.4), squeeze=False, sharey=True)
    _banner(fig, f"Paired difference collaborative − independent after {2 * summary['config']['n_rounds']} "
                 f"experiments, {variant.replace('_', '-')} worlds (n = {vs['n_worlds']}); bar = mean, whiskers = 95% CI; "
                 f"> 0 = collaborative better.\nScale: counts in % of the experiment budget, information in % of the "
                 f"independent mean, errors/uncertainty/regret in % of their prior (no-data) value.")
    labels = [HEADLINE_METRICS[m]["label"] for m in PAIRED_METRICS]
    y = np.arange(len(PAIRED_METRICS))[::-1]
    for j, sel in enumerate(sels):
        ax = axes[0][j]
        _style(ax)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        vals, los, his = [], [], []
        for m in PAIRED_METRICS:
            d = vs["paired_final"][sel][m]
            base = reference_scale(vs, sel, m, 2 * summary["config"]["n_rounds"])
            sgn = 1.0 if HEADLINE_METRICS[m]["better"] == "higher" else -1.0
            a, b = sgn * d["lo"] / base * 100, sgn * d["hi"] / base * 100
            vals.append(sgn * d["mean"] / base * 100)
            los.append(min(a, b))
            his.append(max(a, b))
        vals, los, his = map(np.array, (vals, los, his))
        colors = [COLORS["collaborative"] if v > 0 else COLORS["independent"] for v in vals]
        ax.barh(y, vals, height=0.6, color=colors, edgecolor=SURFACE, linewidth=2)
        ax.errorbar(vals, y, xerr=[vals - los, his - vals], fmt="none", ecolor=INK, elinewidth=1, capsize=2.5)
        ax.axvline(0, color=INK2, lw=1)
        lim = max(10.0, np.nanmax(np.abs(np.r_[los, his])) * 1.1)
        ax.set_xlim(-lim, lim)
        ax.set_xlabel("difference, % of reference scale (sign-adjusted)", fontsize=8.5, color=INK)
        ax.set_title(SEL_TITLE[sel], fontsize=9.5, color=INK, loc="left")
        ax.text(0.99, 0.01, "collaborative better →", transform=ax.transAxes, ha="right", fontsize=7.5, color=INK2)
        ax.text(0.01, 0.01, "← independent better", transform=ax.transAxes, ha="left", fontsize=7.5, color=INK2)
    axes[0][0].set_yticks(y)
    axes[0][0].set_yticklabels(labels, fontsize=8, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig_redundancy(df: pd.DataFrame, summary, out: Path):
    import matplotlib.pyplot as plt

    R = int(df["round"].max())
    fin = df[df["round"] == R]
    variants = sorted(fin.variant.unique(), key=lambda v: v != "well_specified")
    fig, axes = plt.subplots(1, len(variants), figsize=(5.4 * len(variants) + 0.6, 4.6), squeeze=False, sharey=True)
    _banner(fig, f"Redundant experiments per world (of {2 * R}): same/near-identical schedule as the other chamber in "
                 f"the same round, or as any earlier run. Mean ± 95% CI.")
    for j, variant in enumerate(variants):
        ax = axes[0][j]
        _style(ax)
        g = fin[fin.variant == variant]
        xs, xt = [], []
        k = 0
        for sel in ("agent", "info"):
            for mode in ("independent", "collaborative"):
                s = g[(g.selector == sel) & (g["mode"] == mode)]
                if s.empty:
                    continue
                w = s.within_round_dup_cum.to_numpy(float)
                rp = s.redundant_cum.to_numpy(float) - w
                tot = s.redundant_cum.to_numpy(float)
                ci = summary["variants"][variant]["arms"][f"{sel}/{mode}"]["final"]["redundant_cum"]
                ax.bar(k, w.mean(), width=0.7, color=COLORS[mode], edgecolor=SURFACE, linewidth=2)
                ax.bar(k, rp.mean(), bottom=w.mean(), width=0.7, color=COLORS[mode], alpha=0.45, edgecolor=SURFACE,
                       linewidth=2, hatch="///")
                ax.errorbar(k, tot.mean(), yerr=[[tot.mean() - max(0.0, ci["lo"])], [ci["hi"] - tot.mean()]], fmt="none",
                            ecolor=INK, elinewidth=1, capsize=3)
                ax.text(k, ci["hi"] + 0.08, f"{tot.mean():.2f}", ha="center", fontsize=8, color=INK)
                xs.append(k)
                xt.append(f"{sel}\n{mode}")
                k += 1
            k += 0.5
        ax.set_xticks(xs)
        ax.set_xticklabels(xt, fontsize=8, color=INK)
        ax.set_title(f"{variant.replace('_', '-')} worlds (n = {g.world.nunique()})", fontsize=9.5, color=INK, loc="left")
        if j == 0:
            ax.set_ylabel("redundant experiments per world", fontsize=8.5, color=INK)
    from matplotlib.patches import Patch

    fig.legend(handles=[Patch(color=INK2, label="same round, both chambers (solid)"),
                        Patch(facecolor=INK2, alpha=0.45, hatch="///", label="repeat of an earlier round (hatched)"),
                        Patch(color=COLORS["independent"], label="independent"),
                        Patch(color=COLORS["collaborative"], label="collaborative")],
               loc="lower center", ncol=4, frameon=False, fontsize=8)
    fig.tight_layout(rect=(0, 0.07, 1, 0.92))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--worlds", type=int, default=30, help="well-specified worlds (seeds 0..N-1)")
    ap.add_argument("--misspec-worlds", type=int, default=30, help="misspecified worlds (0 to skip)")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", type=Path, default=REPO / "data" / "benchmarks")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    lam = O.prior_lambda()
    print(f"[benchmark] {BENCHMARK_LABEL}; lambda (scale-matched at prior) = {lam:.4f}", flush=True)
    jobs = []
    cfgs = {}
    for variant, n in (("well_specified", args.worlds), ("misspecified", args.misspec_worlds)):
        if n <= 0:
            continue
        cfg = BenchConfig(n_rounds=args.rounds, misspecified=variant == "misspecified", lam=lam)
        cfgs[variant] = cfg
        jobs += [(s, cfg, lam) for s in range(n)]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(_job, jobs), 1):
            rows.extend(res)
            if i % 5 == 0 or i == len(jobs):
                print(f"[benchmark] {i}/{len(jobs)} worlds done ({time.perf_counter() - t0:.0f} s)", flush=True)
    df = pd.DataFrame(rows).sort_values(["variant", "world", "selector", "mode", "round"]).reset_index(drop=True)
    df.to_csv(args.out / "benchmark_results.csv", index=False, float_format="%.6g")
    cfg0 = next(iter(cfgs.values()))
    summary = summarize(df, cfg0)
    summary["config"]["misspecified"] = "per variant (see variants)"
    summary["lambda"] = lam
    summary["held_out_schedules"] = {k: [list(p) for p in v] for k, v in HELD_OUT.items()}
    summary["metric_definitions"] = {m: v["label"] + f" ({v['better']} is better)" for m, v in HEADLINE_METRICS.items()}
    runtime = time.perf_counter() - t0
    summary["runtime_s"] = round(runtime, 1)
    (args.out / "summary.json").write_text(json.dumps(_clean(summary), indent=1, default=_json_default))
    time_to_objective(df, int(df["round"].max())).to_csv(args.out / "time_to_objective.csv", index=False)
    for variant in summary["variants"]:
        fig_curves(summary, variant, args.out / f"curves_{variant}.png")
        fig_paired(summary, variant, args.out / f"paired_differences_{variant}.png")
    fig_redundancy(df, summary, args.out / "redundancy.png")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = None
    manifest = {
        "label": BENCHMARK_LABEL, "provenance": "simulated", "command": " ".join(sys.argv),
        "git_head": commit, "python": platform.python_version(), "platform": platform.platform(),
        "workers": args.workers, "n_jobs": len(jobs), "runtime_s": round(runtime, 1), "lambda": lam,
        "free_params": list(FREE_PARAMS), "variants": {k: v.to_dict() for k, v in cfgs.items()},
        "files": sorted(p.name for p in args.out.iterdir()),
    }
    (args.out / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=_json_default))
    print(f"[benchmark] done in {runtime:.0f} s -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
