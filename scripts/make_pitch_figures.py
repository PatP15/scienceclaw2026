"""Regenerate every FermentAI pitch figure deterministically.

    uv run python scripts/make_pitch_figures.py            # all figures
    uv run python scripts/make_pitch_figures.py --only closed_loop results_ab

Outputs (data/figures/): <name>.png at 200 dpi on a 16 x 9 in canvas (3200 x 1800 px, i.e. >= 2x
a 1600 x 900 slide) plus <name>.svg for every figure.

Inputs (read-only): data/predictions/chamber_{A,B}_prediction.csv, *_uncertainty.csv, *_summary.json;
data/alchemi/analysis/c2_summary.json and data/alchemi/environment/report.md; data/stl/hero_chamber_AB.png,
crumb_chamber_comparison.png; data/stl/print/clear_form4_build1_{manifest.json,iso.png}; data/benchmarks/
summary.json and benchmark_results.csv. Every number on a figure is read from those files at build time.

"Few words" rule (pitch feedback): <= ~15 visible words per figure, names <= 2 words, one number per tile,
one-word badges, no sentences, footers or sources (those live in docs/pitch/slides.md), text >= 22 pt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import (
    Circle,
    FancyArrowPatch,
    FancyBboxPatch,
    Polygon,
    Rectangle,
    Wedge,
)

ROOT = Path(__file__).resolve().parents[1]
PRED = ROOT / "data" / "predictions"
STL = ROOT / "data" / "stl"
OUT = ROOT / "data" / "figures"

ALCH = ROOT / "data" / "alchemi" / "analysis"
BENCH = ROOT / "data" / "benchmarks"
# From data/alchemi/environment/report.md ("Runtime and cost"); Brev's invoice is authoritative.




# --------------------------------------------------------------------------------------------
# Style: one palette for every figure
# --------------------------------------------------------------------------------------------
A_COL = "#1f77b4"  # Chamber A (matches existing charts' hue family)
B_COL = "#ff7f0e"  # Chamber B
GREEN = "#2ca02c"  # MEASURED: from the physical world (sensors, loaf photos, tasting)
TEAL = "#00897b"  # COMPUTED: a calculation that actually ran, with logs (ALCHEMI, PreForm)
PURPLE = "#9467bd"  # MODELED: our own process model (forecasts, crumb geometry, benchmark)
GREY = "#7f7f7f"  # replay / example
SLATE = "#37474f"  # AI agent step
HUMAN = "#c2185b"  # the judge
INK = "#1d1d1b"
MUTED = "#55554f"
FAINT = "#a3a39c"
LINE = "#c9c9c2"
BG = "#ffffff"
PANEL = "#f6f6f3"

BADGES = {
    # key: (label, colour)
    "measured": ("MEASURED", GREEN),
    "measured_pending": ("MEASURED · pending", GREEN),  # drawn hollow + dashed: nothing measured yet
    "computed": ("COMPUTED", TEAL),
    "modeled": ("MODELED", PURPLE),
    "replay": ("REPLAY", GREY),
    "example": ("EXAMPLE", GREY),
    "physical": ("PHYSICAL", GREEN),
    "ai": ("AI AGENT", SLATE),
    "human": ("HUMAN", HUMAN),
}

FONT = "DejaVu Sans"  # bundled with matplotlib -> identical output on every machine

plt.rcParams.update(
    {
        "font.family": FONT,
        "svg.fonttype": "path",  # text as outlines: renders the same in any slide tool
        "svg.hashsalt": "fermentai-pitch",
        "path.simplify": True,
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
    }
)

W, H = 160.0, 90.0  # canvas units: 1 unit = 0.1 in on a 16 x 9 in figure


def canvas():
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def save(fig, name: str, svg: bool = True) -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [OUT / f"{name}.png"]
    fig.savefig(paths[0], dpi=200, metadata={"Software": None})
    if svg:
        p = OUT / f"{name}.svg"
        fig.savefig(p, metadata={"Date": None, "Creator": None})
        paths.append(p)
    plt.close(fig)
    return paths


def badge(ax, x, y, kind, size=10.5, ha="center", va="center", hollow=False, text=None, zorder=6):
    label, col = BADGES[kind]
    label = text or label
    hollow = hollow or kind == "measured_pending"
    ax.text(
        x, y, label, ha=ha, va=va, fontsize=size, fontweight="bold",
        color=col if hollow else "white", zorder=zorder,
        bbox={"boxstyle": "round,pad=0.32,rounding_size=0.8", "fc": "white" if hollow else col, "ec": col,
                  "lw": 1.6, "ls": "--" if hollow else "-"},
    )


def rbox(ax, x0, y0, w, h, fc="white", ec=LINE, lw=1.5, ls="-", r=1.6, zorder=1, alpha=1.0):
    p = FancyBboxPatch((x0, y0), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw,
                       ls=ls, zorder=zorder, alpha=alpha)
    ax.add_patch(p)
    return p


def arrow(ax, p0, p1, color=FAINT, lw=3.0, ls="-", head=18, rad=0.0, zorder=2):
    a = FancyArrowPatch(p0, p1, arrowstyle=f"-|>,head_length={head / 18 * 0.9},head_width={head / 18 * 0.55}",
                        mutation_scale=18, color=color, lw=lw, ls=ls,
                        connectionstyle=f"arc3,rad={rad}", zorder=zorder, shrinkA=0, shrinkB=0)
    ax.add_patch(a)


# --------------------------------------------------------------------------------------------
# Icons: simple shapes only, centred at (cx, cy), size ~ s (half-width)
# --------------------------------------------------------------------------------------------
def icon_loaf(ax, cx, cy, s, color="#c98b4a"):
    ax.add_patch(Wedge((cx, cy - 0.35 * s), 0.95 * s, 0, 180, fc=color, ec=INK, lw=1.4, zorder=4))
    ax.add_patch(Rectangle((cx - 0.95 * s, cy - 0.55 * s), 1.9 * s, 0.22 * s, fc=color, ec=INK, lw=1.4, zorder=4))
    for dx in (-0.4, 0.0, 0.4):
        ax.plot([cx + dx * s - 0.12 * s, cx + dx * s + 0.12 * s], [cy + 0.05 * s, cy + 0.35 * s],
                color="#f3dcb8", lw=2.2, zorder=5, solid_capstyle="round")


def icon_person(ax, cx, cy, s, color=HUMAN):
    ax.add_patch(Circle((cx - 0.15 * s, cy + 0.32 * s), 0.3 * s, fc=color, ec="none", zorder=4))
    ax.add_patch(Wedge((cx - 0.15 * s, cy - 0.65 * s), 0.62 * s, 0, 180, fc=color, ec="none", zorder=4))
    # speech bubble
    bx, by = cx + 0.45 * s, cy + 0.55 * s
    rbox(ax, bx - 0.05 * s, by - 0.2 * s, 0.75 * s, 0.5 * s, fc="white", ec=color, lw=1.6, r=0.25 * s, zorder=5)
    ax.add_patch(Polygon([(bx + 0.05 * s, by - 0.18 * s), (bx - 0.12 * s, by - 0.42 * s), (bx + 0.25 * s, by - 0.18 * s)],
                         fc="white", ec=color, lw=1.6, zorder=5))
    for i, ln in enumerate((0.5, 0.35)):
        ax.plot([bx + 0.08 * s, bx + (0.08 + ln) * s], [by + 0.15 * s - i * 0.17 * s] * 2, color=color, lw=1.6, zorder=6)


def icon_sliders(ax, cx, cy, s, color=SLATE):
    for i, (yy, k) in enumerate(((0.45, 0.65), (0.0, 0.25), (-0.45, 0.8))):
        y = cy + yy * s
        ax.plot([cx - 0.8 * s, cx + 0.8 * s], [y, y], color=LINE, lw=3, zorder=4, solid_capstyle="round")
        kx = cx + (-0.8 + 1.6 * k) * s
        ax.plot([cx - 0.8 * s, kx], [y, y], color=color, lw=3, zorder=4, solid_capstyle="round")
        ax.add_patch(Circle((kx, y), 0.16 * s, fc="white", ec=color, lw=2, zorder=5))


def icon_bars(ax, cx, cy, s):
    base = cy - 0.6 * s
    ax.plot([cx - 0.85 * s, cx + 0.85 * s], [base, base], color=INK, lw=1.5, zorder=4)
    for dx, h, c in ((-0.55, 0.7, A_COL), (-0.2, 1.05, B_COL), (0.25, 0.85, A_COL), (0.6, 1.25, B_COL)):
        ax.add_patch(Rectangle((cx + dx * s - 0.13 * s, base), 0.26 * s, h * s, fc=c, ec="none", zorder=4))
    xs = np.linspace(cx - 0.8 * s, cx + 0.8 * s, 30)
    ax.plot(xs, base + 0.3 * s + 0.9 * s / (1 + np.exp(-(xs - cx) / (0.25 * s))), color=PURPLE, lw=2.2, ls="--", zorder=5)


def icon_chat(ax, cx, cy, s):
    for (dx, dy, c, t) in ((-0.32, 0.25, A_COL, "A"), (0.32, -0.25, B_COL, "B")):
        x0, y0 = cx + dx * s - 0.5 * s, cy + dy * s - 0.32 * s
        rbox(ax, x0, y0, 1.0 * s, 0.64 * s, fc=c, ec="white", lw=1.5, r=0.2 * s, zorder=4 if t == "A" else 5)
        tail_x = x0 + (0.2 * s if t == "A" else 0.8 * s)
        ax.add_patch(Polygon([(tail_x - 0.1 * s, y0 + 0.02 * s), (tail_x, y0 - 0.2 * s), (tail_x + 0.1 * s, y0 + 0.02 * s)],
                             fc=c, ec="none", zorder=4 if t == "A" else 5))
        ax.text(cx + dx * s, cy + dy * s, t, color="white", fontsize=2.8 * s, fontweight="bold", ha="center", va="center",
                zorder=6)


def icon_chefhat(ax, cx, cy, s, color="white"):
    for dx, dy, r in ((-0.42, 0.2, 0.36), (0.0, 0.42, 0.42), (0.42, 0.2, 0.36)):
        ax.add_patch(Circle((cx + dx * s, cy + dy * s), r * s, fc=color, ec=INK, lw=1.5, zorder=4))
    ax.add_patch(Rectangle((cx - 0.55 * s, cy - 0.6 * s), 1.1 * s, 0.68 * s, fc=color, ec=INK, lw=1.5, zorder=5))
    ax.add_patch(Rectangle((cx - 0.53 * s, cy - 0.12 * s), 1.06 * s, 0.25 * s, fc=color, ec="none", zorder=6))
    ax.plot([cx - 0.55 * s, cx + 0.55 * s], [cy - 0.38 * s] * 2, color=INK, lw=1.2, zorder=6)
    # motion marks
    for sgn in (-1, 1):
        for k in (0.0, 0.18):
            ax.plot([cx + sgn * (0.95 + k) * s] * 2, [cy - 0.1 * s, cy + 0.45 * s], color=GREEN, lw=1.8, zorder=4,
                    solid_capstyle="round")


def icon_chamber(ax, cx, cy, s):
    rbox(ax, cx - 0.75 * s, cy - 0.75 * s, 1.1 * s, 1.45 * s, fc="white", ec=INK, lw=1.5, r=0.15 * s, zorder=4)
    ax.add_patch(Rectangle((cx - 0.62 * s, cy - 0.62 * s), 0.84 * s, 0.75 * s, fc="#e8c38f", ec="none", zorder=5))
    ax.plot([cx - 0.62 * s, cx + 0.22 * s], [cy + 0.32 * s] * 2, color=INK, lw=1, ls=":", zorder=5)
    # thermometer
    tx = cx + 0.65 * s
    rbox(ax, tx - 0.1 * s, cy - 0.35 * s, 0.2 * s, 1.0 * s, fc="white", ec=INK, lw=1.3, r=0.1 * s, zorder=4)
    ax.add_patch(Circle((tx, cy - 0.5 * s), 0.2 * s, fc="#d64545", ec=INK, lw=1.3, zorder=5))
    ax.add_patch(Rectangle((tx - 0.04 * s, cy - 0.45 * s), 0.08 * s, 0.6 * s, fc="#d64545", ec="none", zorder=6))
    # camera dot
    ax.add_patch(Circle((cx - 0.2 * s, cy + 0.5 * s), 0.12 * s, fc=INK, zorder=6))


def icon_fit(ax, cx, cy, s):
    xs = np.linspace(-0.8, 0.8, 40)
    ys = -0.5 + 1.0 / (1 + np.exp(-xs / 0.22))
    ax.plot(cx + xs * s, cy + ys * s * 0.95, color=PURPLE, lw=2.6, zorder=5)
    pts = [(-0.7, -0.5), (-0.35, -0.38), (-0.05, -0.05), (0.2, 0.25), (0.5, 0.45), (0.75, 0.43)]
    for px, py in pts:
        ax.add_patch(Circle((cx + px * s, cy + py * s + 0.06 * s), 0.09 * s, fc=GREEN, ec="white", lw=0.8, zorder=6))
    ax.plot([cx - 0.85 * s, cx - 0.85 * s, cx + 0.85 * s], [cy + 0.75 * s, cy - 0.7 * s, cy - 0.7 * s], color=INK, lw=1.3,
            zorder=4)


def icon_gpu(ax, cx, cy, s, color=TEAL):
    rbox(ax, cx - 0.65 * s, cy - 0.65 * s, 1.3 * s, 1.3 * s, fc=INK, ec=INK, lw=1, r=0.12 * s, zorder=4)
    for k in np.linspace(-0.45, 0.45, 4):
        for (x0, y0, x1, y1) in ((cx + k * s, cy + 0.65 * s, cx + k * s, cy + 0.9 * s),
                                 (cx + k * s, cy - 0.65 * s, cx + k * s, cy - 0.9 * s),
                                 (cx + 0.65 * s, cy + k * s, cx + 0.9 * s, cy + k * s),
                                 (cx - 0.65 * s, cy + k * s, cx - 0.9 * s, cy + k * s)):
            ax.plot([x0, x1], [y0, y1], color=INK, lw=1.6, zorder=3)
    ax.text(cx, cy, "GPU", color=color, fontsize=12 * s / 3.6, fontweight="bold", ha="center", va="center", zorder=5)


def icon_molecule(ax, cx, cy, s):
    atoms = [(-0.55, 0.1, "#d64545", 0.2), (0.0, 0.0, "#555555", 0.22), (0.45, 0.35, "#d64545", 0.2),
             (0.45, -0.4, "#555555", 0.2), (-0.95, 0.55, "#9ecae1", 0.14), (-1.0, -0.3, "#9ecae1", 0.14)]
    for (x, y, *_r) in atoms[1:4]:
        ax.plot([cx + atoms[1][0] * s, cx + x * s], [cy + atoms[1][1] * s, cy + y * s], color=INK, lw=2, zorder=4)
    ax.plot([cx + atoms[0][0] * s, cx], [cy + atoms[0][1] * s, cy], color=INK, lw=2, zorder=4)
    for x, y in ((-0.95, 0.55), (-1.0, -0.3)):  # H-bonds to water (dashed)
        ax.plot([cx - 0.55 * s, cx + x * s], [cy + 0.1 * s, cy + y * s], color="#4a90c2", lw=1.6, ls=(0, (2, 2)), zorder=4)
    for x, y, c, r in atoms:
        ax.add_patch(Circle((cx + x * s, cy + y * s), r * s, fc=c, ec=INK, lw=1, zorder=5))


def icon_curves(ax, cx, cy, s):
    ax.plot([cx - 0.85 * s, cx - 0.85 * s, cx + 0.9 * s], [cy + 0.8 * s, cy - 0.7 * s, cy - 0.7 * s], color=INK, lw=1.4,
            zorder=4)
    xs = np.linspace(0, 1, 50)
    for col, k in ((A_COL, 3.0), (B_COL, 5.0)):
        ys = 1 - np.exp(-k * xs)
        ax.plot(cx + (-0.8 + 1.65 * xs) * s, cy + (0.65 - 1.25 * ys) * s, color=col, lw=2.6, zorder=5)


def icon_pores(ax, cx, cy, s):
    rbox(ax, cx - 0.85 * s, cy - 0.8 * s, 1.7 * s, 1.6 * s, fc="#efe2c8", ec="#b98a52", lw=2, r=0.3 * s, zorder=4)
    rng = np.random.default_rng(3)
    for _ in range(16):
        x, y = rng.uniform(-0.65, 0.65), rng.uniform(-0.6, 0.6)
        r = rng.uniform(0.05, 0.2)
        ax.add_patch(Circle((cx + x * s, cy + y * s), r * s, fc="#8a5a2b", ec="none", zorder=5, alpha=0.85))


def icon_camera(ax, cx, cy, s):
    rbox(ax, cx - 0.85 * s, cy - 0.55 * s, 1.7 * s, 1.1 * s, fc=INK, ec=INK, r=0.18 * s, zorder=4)
    ax.add_patch(Rectangle((cx - 0.3 * s, cy + 0.5 * s), 0.6 * s, 0.22 * s, fc=INK, zorder=4))
    ax.add_patch(Circle((cx, cy - 0.02 * s), 0.38 * s, fc="white", ec=INK, lw=1, zorder=5))
    ax.add_patch(Circle((cx, cy - 0.02 * s), 0.22 * s, fc="#8a5a2b", zorder=6))


# ============================================================================================
# 1. Closed loop
# ============================================================================================


# ============================================================================================
# 2. Evidence pipeline
# ============================================================================================
def _crop(png: Path, box_frac: tuple[float, float, float, float]):
    if not png.exists():
        return None
    from PIL import Image

    im = Image.open(png).convert("RGB")
    w, h = im.size
    x0, y0, x1, y1 = box_frac
    return np.asarray(im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1))))


# oblique-view regions of data/stl/crumb_chamber_comparison.png (generate_stls.py layout)
CHAMBER_THUMB_BOX = {"A": (0.375, 0.185, 0.64, 0.445), "B": (0.375, 0.635, 0.64, 0.895)}






# ============================================================================================
# 3. Architecture
# ============================================================================================
def icon_laptop(ax, cx, cy, s):
    rbox(ax, cx - 0.8 * s, cy - 0.3 * s, 1.6 * s, 1.0 * s, fc=INK, ec=INK, r=0.08 * s, zorder=4)
    ax.add_patch(Rectangle((cx - 0.7 * s, cy - 0.2 * s), 1.4 * s, 0.8 * s, fc="#dfe7ef", zorder=5))
    ax.add_patch(Polygon([(cx - 1.0 * s, cy - 0.5 * s), (cx + 1.0 * s, cy - 0.5 * s), (cx + 0.85 * s, cy - 0.32 * s),
                          (cx - 0.85 * s, cy - 0.32 * s)], fc=INK, zorder=4))


def icon_router(ax, cx, cy, s):
    rbox(ax, cx - 0.9 * s, cy - 0.35 * s, 1.8 * s, 0.6 * s, fc=INK, ec=INK, r=0.15 * s, zorder=4)
    for dx in (-0.55, 0.55):
        ax.plot([cx + dx * s, cx + dx * s * 1.2], [cy + 0.25 * s, cy + 0.95 * s], color=INK, lw=2.4, zorder=4)
    for k in (0.5, 0.8):
        ax.add_patch(matplotlib.patches.Arc((cx, cy + 0.4 * s), 2 * k * s, 1.4 * k * s, theta1=50, theta2=130,
                                            color=GREEN, lw=2, zorder=4))
    for dx in (-0.5, -0.25, 0.0):
        ax.add_patch(Circle((cx + dx * s, cy - 0.05 * s), 0.07 * s, fc=GREEN, zorder=5))


def icon_tablet(ax, cx, cy, s):
    rbox(ax, cx - 0.95 * s, cy - 0.65 * s, 1.9 * s, 1.3 * s, fc=INK, ec=INK, r=0.15 * s, zorder=4)
    ax.add_patch(Rectangle((cx - 0.82 * s, cy - 0.52 * s), 1.64 * s, 1.04 * s, fc="#f4efe6", zorder=5))
    ax.plot([cx - 0.65 * s, cx + 0.2 * s], [cy + 0.25 * s] * 2, color=HUMAN, lw=2, zorder=6)
    ax.plot([cx - 0.65 * s, cx + 0.45 * s], [cy + 0.0 * s] * 2, color=FAINT, lw=2, zorder=6)
    ax.plot([cx - 0.65 * s, cx + 0.05 * s], [cy - 0.25 * s] * 2, color=FAINT, lw=2, zorder=6)




# ============================================================================================
# 4. Results A vs B
# ============================================================================================
def _load_chamber(c: str):
    df = pd.read_csv(PRED / f"chamber_{c}_prediction.csv")
    unc = pd.read_csv(PRED / f"chamber_{c}_uncertainty.csv")
    summ = json.loads((PRED / f"chamber_{c}_summary.json").read_text())
    assert set(df.provenance) == {"simulated"}, "results figure expects simulated predictions"
    return df, unc, summ






# ============================================================================================
# 5. Agent exchange mock-up
# ============================================================================================






# ============================================================================================
# 6. In-silico benchmark summary
# ============================================================================================
def benchmark_numbers(variant: str = "well_specified") -> dict:
    summ = json.loads((BENCH / "summary.json").read_text())
    pf = summ["variants"][variant]["paired_final"]
    df = pd.read_csv(BENCH / "benchmark_results.csv")
    last = df[(df.variant == variant) & (df["round"] == df["round"].max())]
    g = last.groupby(["selector", "mode"])[["within_round_dup_cum", "peer_changed_cum"]].mean()
    return {
        "n_worlds": summ["variants"][variant]["n_worlds"],
        "n_rounds": summ["config"]["n_rounds"],
        "changed": g.loc[("agent", "collaborative"), "peer_changed_cum"],
        "dup_ind": g.loc[("agent", "independent"), "within_round_dup_cum"],
        "dup_col": g.loc[("agent", "collaborative"), "within_round_dup_cum"],
        "info": {s: pf[s]["pooled_info_gain_nats"] for s in ("agent", "info")},
        "regret": pf["agent"]["agent_regret"],
    }




# ============================================================================================
# 7. Technology partners: NVIDIA ALCHEMI | Formlabs PreForm (text wordmarks, no logos)
# ============================================================================================
# Shown only if the live PreForm manifest is missing. Never invent these.
HERO_PNG = STL / "hero_chamber_AB.png"






def alchemi_c2() -> dict:
    """Campaign-2 headline numbers (data/alchemi/analysis/c2_summary.json + environment/report.md)."""
    import re

    c2 = json.loads((ALCH / "c2_summary.json").read_text())
    rp = c2["relative_pka_estimate"]
    per6 = rp["per_n"]["6"]
    to_pka = per6["dpKa"] / per6["ddG_qh_kcal"]  # 1/(RT ln 10), as used by the analysis
    by_n = [(tc["n_water"], tc["dd_boltzmann_kcal"] * to_pka) for tc in c2["thermo_cycles"]]
    rep = (ROOT / "data" / "alchemi" / "environment" / "report.md").read_text()
    tot = re.search(r"Total across sessions: ([\d.]+) h ≈ \$([\d.]+)", rep)
    c1 = re.search(r"Calculations: (\d+) optimisations.*?(\d+) MD runs", rep)
    cc = re.search(r"Campaign 2: (\d+) optimisations.*?(\d+) vibrational analyses, (\d+) long-MD runs", rep)
    return {
        "dpka": rp["value"], "dpka_unc": rp["uncertainty"], "dpka_exp": rp["experiment"], "by_n": by_n,
        "pka_exp": {k.replace("_acid", ""): v for k, v in rp["experiment_pka"].items()},
        "dimer": c2["acid_pairs"]["acetic_dimer_kcal"], "dimer_lit": c2["acid_pairs"]["acetic_dimer_literature_kcal"],
        "gpu_h": float(tot.group(1)), "cost_usd": float(tot.group(2)),
        "n_opt": int(c1.group(1)) + int(cc.group(1)), "n_vib": int(cc.group(2)),
        "n_md": int(c1.group(2)) + int(cc.group(3)),
    }




def _fit_image(ax, im, x0, y0, w, h):
    ih, iw = im.shape[:2]
    if iw / ih > w / h:
        hh = w * ih / iw
        ext = (x0, x0 + w, y0 + (h - hh) / 2, y0 + (h + hh) / 2)
    else:
        ww = h * iw / ih
        ext = (x0 + (w - ww) / 2, x0 + (w + ww) / 2, y0, y0 + h)
    ax.imshow(im, extent=ext, zorder=3, interpolation="lanczos")
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)




# ============================================================================================
# "Few words" pitch figures. Rule: <= ~15 visible words per figure, names <= 2 words, one number
# per tile, one-word badges, no sentences / footers / sources (those live in docs/pitch/slides.md).
# Text is big: >= 22 pt on a 16 x 9 in canvas (>= ~37 px when the figure spans 1920 px).
# ============================================================================================
LBL = 26  # node / box names
SMALL = 22  # smallest text on any figure
CLEAR_MANIFEST = STL / "print" / "clear_form4_build1_manifest.json"
CLEAR_ISO = STL / "print" / "clear_form4_build1_iso.png"


def _b(ax, x, y, text, kind, size=SMALL, ha="center", va="center", hollow=False):
    badge(ax, x, y, kind, size=size, ha=ha, va=va, hollow=hollow, text=text)


def _mini_schedule(ax, x0, y0, w, h, series, t_end=16.0, tlo=19.0, thi=29.0):
    """Tiny temperature-schedule plot: series = [(pts, colour)] with pts [(t_h, temp_c)]."""
    ax.plot([x0, x0 + w], [y0, y0], color=LINE, lw=1.5)
    for pts, col in series:
        xs = [x0 + w * t / t_end for t, _ in pts]
        ys = [y0 + h * (T - tlo) / (thi - tlo) for _, T in pts]
        ax.plot(xs, ys, color=col, lw=6, solid_capstyle="round", solid_joinstyle="round", zorder=3)


def fig_closed_loop():
    fig, ax = canvas()
    cx, cy, rx, ry, r = 80.0, 45.0, 46.0, 30.0, 7.6
    nodes = [
        (135, "physical", "Bake", icon_loaf), (90, "human", "Taste", icon_person),
        (45, "ai", "Parse", icon_sliders), (0, "modeled", "Compare", icon_bars),
        (-45, "ai", "Debate", icon_chat), (-90, "physical", "Publish", icon_chefhat),
        (-135, "physical", "Ferment", icon_chamber), (180, "modeled", "Recalibrate", icon_fit),
    ]
    pos = [(cx + rx * np.cos(np.deg2rad(a)), cy + ry * np.sin(np.deg2rad(a))) for a, *_ in nodes]
    rad = [r + (1.8 if k == "human" else 0.0) for _, k, *_ in nodes]
    for i in range(len(nodes)):
        j = (i + 1) % len(nodes)
        a0, a1 = nodes[i][0], nodes[j][0]
        if a1 > a0:
            a1 -= 360
        ts = np.deg2rad(np.linspace(a0, a1, 240))
        xs, ys = cx + rx * np.cos(ts), cy + ry * np.sin(ts)
        keep = (np.hypot(xs - pos[i][0], ys - pos[i][1]) > rad[i] + 1.2) & \
               (np.hypot(xs - pos[j][0], ys - pos[j][1]) > rad[j] + 2.0)
        xs, ys = xs[keep], ys[keep]
        ax.plot(xs[:-3], ys[:-3], color=FAINT, lw=4, zorder=1, solid_capstyle="round")
        arrow(ax, (xs[-6], ys[-6]), (xs[-1], ys[-1]), color=FAINT, lw=4, head=26, zorder=1)
    place = {90: (0, 1, "center", "bottom"), -90: (0, -1, "center", "top"), 0: (1, 0, "left", "center"),
             180: (-1, 0, "right", "center"), 45: (0.75, 0.75, "left", "bottom"),
             135: (-0.75, 0.75, "right", "bottom"), -45: (0.75, -0.75, "left", "top"),
             -135: (-0.75, -0.75, "right", "top")}
    for (ang, kind, name, icon), (x, y), rr in zip(nodes, pos, rad):
        col = BADGES[kind][1]
        if kind == "human":
            ax.add_patch(Circle((x, y), rr + 1.4, fc="#fbe3ec", ec="none", zorder=2))
        ax.add_patch(Circle((x, y), rr, fc="white", ec=col, lw=6 if kind == "human" else 4, zorder=3,
                            ls=(0, (4, 2.5)) if kind == "physical" else "-"))  # dashed green = measured, pending
        icon(ax, x, y + 0.3, rr * 0.62)
        dx, dy, ha, va = place[ang]
        ax.text(x + dx * (rr + 1.8), y + dy * (rr + 1.6), name, ha=ha, va=va, fontsize=LBL, fontweight="bold",
                color=HUMAN if kind == "human" else INK)
    ax.text(cx, cy, "Taste → next experiment", ha="center", va="center", fontsize=36, fontweight="bold", color=INK)
    return save(fig, "closed_loop")


def fig_evidence_pipeline():
    fig, ax = canvas()
    bw, gap, x0, y0, bh = 28.0, 4.0, 2.0, 14.0, 54.0
    stages = [("NVIDIA\nALCHEMI", "computed", "COMPUTED", "alchemi"),
              ("Kinetics\nmodel", "modeled", "MODELED", icon_curves), ("Crumb\nmap", "modeled", "MODELED", icon_pores),
              ("Printed\nspecimens", "modeled", "MODELED", "stl"),
              ("Loaf\nphotos", "measured_pending", "MEASURED", icon_camera)]
    icy = y0 + 16.0
    for i, (name, kind, word, icon) in enumerate(stages):
        bx = x0 + i * (bw + gap)
        col = BADGES[kind][1]
        pend = kind == "measured_pending"
        rbox(ax, bx, y0, bw, bh, fc="white", ec=col, lw=3, r=2.0, ls="--" if pend else "-")
        ax.text(bx + bw / 2, y0 + bh - 3.0, name, ha="center", va="top", fontsize=LBL, fontweight="bold", color=INK,
                linespacing=1.1)
        _b(ax, bx + bw / 2, y0 + bh - 17.0, word, kind, hollow=pend)
        if icon == "alchemi":
            icon_gpu(ax, bx + 8.0, icy, 4.6)
            icon_molecule(ax, bx + 19.5, icy, 6.2)
        elif icon == "stl":
            for j, c in enumerate("AB"):
                im = _crop(STL / "crumb_chamber_comparison.png", CHAMBER_THUMB_BOX[c])
                ex0 = bx + 1.0 + j * (bw / 2 - 0.5)
                if im is not None:
                    _fit_image(ax, im, ex0, icy - 8.0, bw / 2 - 1.5, 13.0)
                ax.text(ex0 + (bw / 2 - 1.5) / 2, icy + 8.0, c, ha="center", va="center", fontsize=LBL,
                        fontweight="bold", color=A_COL if c == "A" else "#d4660a")
        else:
            icon(ax, bx + bw / 2, icy, 8.5)
        if i < len(stages) - 1:
            arrow(ax, (bx + bw + 0.4, icy), (bx + bw + gap - 0.4, icy), color=TEAL if i == 0 else INK, lw=4, head=24)
    # ALCHEMI informs only the acid terms of the kinetics model
    bx1, bx2, yb = x0 + bw / 2, x0 + bw + gap + bw / 2, y0 + bh + 2.6
    ax.plot([bx1, bx1, bx2, bx2], [yb - 1.0, yb + 1.0, yb + 1.0, yb - 1.0], color=TEAL, lw=3)
    ax.text(bx2 + 2.0, yb + 1.0, "acid terms only", fontsize=LBL, color=TEAL, fontweight="bold", va="center")
    return save(fig, "evidence_pipeline")


def icon_lock(ax, cx, cy, s, color=INK):
    """Padlock: firmware safety limits."""
    ax.add_patch(matplotlib.patches.Arc((cx, cy + 0.35 * s), 0.9 * s, 1.1 * s, theta1=0, theta2=180, color=color,
                                        lw=2.6, zorder=5))
    ax.plot([cx - 0.45 * s, cx - 0.45 * s], [cy + 0.35 * s, cy + 0.1 * s], color=color, lw=2.6, zorder=5)
    ax.plot([cx + 0.45 * s, cx + 0.45 * s], [cy + 0.35 * s, cy + 0.1 * s], color=color, lw=2.6, zorder=5)
    rbox(ax, cx - 0.7 * s, cy - 0.6 * s, 1.4 * s, 0.75 * s, fc=color, ec=color, r=0.12 * s, zorder=6)


def fig_architecture():
    """Three clean rows: shared evidence -> two scientists debating -> their chambers. <= 5 arrows."""
    fig, ax = canvas()
    # row 1: one evidence strip with three chips
    rbox(ax, 4.0, 72.0, 152.0, 15.0, fc=PANEL, ec="none", r=3.0, zorder=0)
    chips = [("NVIDIA ALCHEMI", "computed", "COMPUTED"), ("Fermentation model", "modeled", "MODELED"),
             ("Judge feedback", "human", None)]
    cw = 46.0
    for k, (name, kind, word) in enumerate(chips):
        x = 8.0 + k * (cw + 4.0)
        col = BADGES[kind][1]
        rbox(ax, x, 74.5, cw, 10.0, fc="white", ec=col, lw=3, r=2.0, zorder=2)
        if word:
            ax.text(x + 2.5, 82.0, name, fontsize=24, fontweight="bold", color=INK, va="center", zorder=3)
            _b(ax, x + 2.5, 77.2, word, kind, size=18, ha="left")
        else:
            ax.text(x + cw / 2, 79.5, name, fontsize=LBL, fontweight="bold", color=HUMAN, ha="center", va="center",
                    zorder=3)
    # one wide arrow into the agents row
    ax.add_patch(Polygon([(62, 70.5), (98, 70.5), (98, 66.0), (104, 66.0), (80, 59.5), (56, 66.0), (62, 66.0)],
                         fc="#e4e2dc", ec="none", zorder=0))
    # row 2: the two scientists with the exchange between them
    for x, name, col in ((4.0, "Scientist A", A_COL), (112.0, "Scientist B", B_COL)):
        rbox(ax, x, 38.0, 44.0, 16.0, fc=col, ec=col, r=3.0, zorder=2)
        ax.text(x + 22.0, 46.0, name, fontsize=32, fontweight="bold", color="white", ha="center", va="center",
                zorder=3)
    bubbles = [("32 °C, then cool →", "#e3eef8", A_COL), ("← overproof risk: 24 °C", "#fde9d8", "#d4660a"),
               ("trial 002 agreed", INK, "white")]
    for k, (text, fc, tc) in enumerate(bubbles):
        by = 53.0 - k * 7.0
        rbox(ax, 53.0, by - 2.9, 54.0, 5.8, fc=fc, ec="none", r=2.6, zorder=2)
        ax.text(80.0, by, text, fontsize=24, fontweight="bold", color=tc, ha="center", va="center", zorder=3)
    # row 3: one straight arrow per scientist down to its own chamber
    for cx, lab_x, ha in ((26.0, 28.0, "left"), (134.0, 132.0, "right")):
        arrow(ax, (cx, 37.6), (cx, 22.4), color=INK, lw=4, head=24)
        ax.text(lab_x, 30.0, "new schedule", fontsize=SMALL, fontweight="bold", color=INK, ha=ha, va="center")
    for x, name, col in ((4.0, "Chamber A", A_COL), (112.0, "Chamber B", B_COL)):
        rbox(ax, x, 4.0, 44.0, 18.0, fc="white", ec=col, lw=3.5, ls=(0, (5, 3)), r=2.0, zorder=2)
        ax.text(x + 4.0, 13.0, name, fontsize=LBL + 2, fontweight="bold", color=INK, ha="left", va="center", zorder=3)
        icon_lock(ax, x + 37.0, 12.6, 4.2, color=INK)
    return save(fig, "architecture")




def fig_dataflow():
    """What passes between the boxes: modeled/computed chain (left) and the physical loop (right)."""
    fig, ax = canvas()
    bh, gap, lx, lw_, rx, rw = 10.5, 4.6, 36.0, 44.0, 122.0, 35.0
    tops = [89.0 - k * (bh + gap) for k in range(6)]  # box tops, row k
    mid = [t - bh / 2 for t in tops]

    def box(x, w, row, name, kind=None, word=None, ec=INK, ls="-"):
        y0 = tops[row] - bh
        rbox(ax, x, y0, w, bh, fc="white", ec=BADGES[kind][1] if kind else ec, lw=3, ls=ls, r=1.8, zorder=2)
        if word:
            ax.text(x + 2.0, y0 + bh - 3.4, name, fontsize=LBL, fontweight="bold", color=INK, va="center", zorder=4)
            _b(ax, x + 2.0, y0 + 3.0, word, kind, size=18, ha="left")
        else:
            ax.text(x + w / 2, y0 + bh / 2, name, fontsize=LBL, fontweight="bold", color=INK, ha="center",
                    va="center", zorder=4)

    def down(x, row, label, side="left"):
        y0, y1 = tops[row] - bh - 0.2, tops[row + 1] + 0.2
        arrow(ax, (x, y0), (x, y1), color=INK, lw=3, head=16, zorder=1)
        if label:
            if side == "left":
                ax.text(x - 2.0, (y0 + y1) / 2, label, fontsize=SMALL, color=INK, ha="right", va="center")
            else:
                ax.text(x + 2.0, (y0 + y1) / 2, label, fontsize=SMALL, color=INK, ha="left", va="center")

    # ---- left: computed / modeled chain
    left = [("NVIDIA ALCHEMI", "computed", "COMPUTED"), ("Fermentation model", "modeled", "MODELED"),
            ("Morphology map", "modeled", "MODELED"), ("Crumb generator", "modeled", "MODELED"),
            ("PreForm API", "computed", "COMPUTED"), ("Form 4 print", None, None)]
    for k, (name, kind, word) in enumerate(left):
        box(lx, lw_, k, name, kind, word, ls=(0, (5, 3)) if kind is None else "-")
    cxl = lx + lw_ / 2
    for k, lab in enumerate(("ΔpKa, hydration, H-bond", "pH, CO₂, rise, overproof", "porosity, pore size, clustering",
                             "STL", ".form")):
        down(cxl, k, lab)
    ax.text(lx - 1.5, mid[1], "recipe, schedule,\nstarter →", fontsize=SMALL, color=MUTED, ha="right", va="center",
            linespacing=1.1)
    # ---- right: the physical loop (dashed green = measured, pending)
    right = ["Judges taste", "Agents A/B", None, "Chambers A/B", "Bake", "Photo analysis"]
    for k, name in enumerate(right):
        if name:
            pend = name not in ("Agents A/B",)
            box(rx, rw, k, name, ec=GREEN if pend else SLATE, ls=(0, (5, 3)) if pend else "-")
    _b(ax, rx + rw, tops[2] - bh / 2, "MEASURED · pending", "measured_pending", size=20, ha="right")
    cxr = rx + rw / 2
    down(cxr, 0, "free text", side="right")
    for k in (3, 4):
        down(cxr, k, None)
    # cross-links into the left chain
    xr, xl = rx - 0.3, lx + lw_ + 0.3
    arrow(ax, (xr, mid[1]), (xl, mid[1]), color=SLATE, lw=3, head=18)
    ax.text((xr + xl) / 2, mid[1] + 1.0, "new schedule", fontsize=SMALL, fontweight="bold", color=SLATE,
            ha="center", va="bottom")
    # chambers -> model: sensor data, recalibrate (elbow up into the model box)
    ex = xl + 16.0
    ax.plot([xr, ex], [mid[3], mid[3]], color=GREEN, lw=3, ls=(0, (5, 3)), zorder=1)
    ax.plot([ex, ex], [mid[3], mid[1] - 3.0], color=GREEN, lw=3, ls=(0, (5, 3)), zorder=1)
    arrow(ax, (ex, mid[1] - 3.0), (xl, mid[1] - 3.0), color=GREEN, lw=3, head=18)
    ax.text((xr + ex) / 2, mid[3] + 1.0, "temp, RH,\ngas, height", fontsize=SMALL, fontweight="bold",
            color=GREEN, ha="center", va="bottom")
    ax.text(ex + 1.5, (mid[3] + mid[1] - 3.0) / 2 + 2.0, "recalibrate", fontsize=SMALL, color=GREEN, ha="left",
            va="center")
    # photo analysis <-> morphology map output
    ex2 = xl + 8.0
    ax.plot([xr, ex2], [mid[5], mid[5]], color=GREEN, lw=3, ls=(0, (5, 3)), zorder=1)
    ax.plot([ex2, ex2], [mid[5], mid[2] - 2.0], color=GREEN, lw=3, ls=(0, (5, 3)), zorder=1)
    ax.add_patch(FancyArrowPatch((ex2, mid[2] - 2.0), (xl, mid[2] - 2.0), arrowstyle="<|-|>,head_length=0.8,head_width=0.45",
                                 mutation_scale=18, color=GREEN, lw=3, zorder=2, shrinkA=0, shrinkB=0))
    ax.text((xr + ex2) / 2 - 1.0, mid[5] + 1.0, "porosity, pore size", fontsize=SMALL, fontweight="bold",
            color=GREEN, ha="center", va="bottom")
    ax.text(ex2 + 1.5, (mid[5] + mid[2]) / 2, "compare", fontsize=SMALL, color=GREEN, ha="left", va="center")
    return save(fig, "dataflow")




def fig_results_ab():
    dA, uA, sA = _load_chamber("A")
    dB, uB, sB = _load_chamber("B")
    tmax = 18.0
    fig = plt.figure(figsize=(16, 9))
    axs = [fig.add_axes((0.075, 0.15, 0.40, 0.70)), fig.add_axes((0.575, 0.15, 0.40, 0.70))]
    fig.text(0.975, 0.95, "MODELED", fontsize=SMALL, fontweight="bold", color="white", ha="right", va="center",
             bbox={"boxstyle": "round,pad=0.4,rounding_size=0.6", "fc": PURPLE, "ec": PURPLE})
    for ax, title in zip(axs, ("pH", "Rise")):
        ax.axvspan(12, 16, color="#ecebe6", zorder=0, lw=0)
        ax.set_xlim(0, tmax)
        ax.set_xticks(range(0, int(tmax) + 1, 6))
        ax.tick_params(labelsize=SMALL, colors=MUTED, length=0, pad=8)
        ax.grid(axis="y", color="#e6e6e1", lw=1)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(FAINT)
        ax.set_xlabel("hours", fontsize=SMALL, color=MUTED, labelpad=6)
        ax.set_title(title, fontsize=32, fontweight="bold", color=INK, loc="left", pad=14)
    for ax, col_v, col_lo, col_hi in ((axs[0], "ph", "ph_p05", "ph_p95"),
                                      (axs[1], "expansion_ratio", "expansion_ratio_p05", "expansion_ratio_p95")):
        for d, u, col, lab in ((dA, uA, A_COL, "A"), (dB, uB, B_COL, "B")):
            m = u.time_h <= tmax
            ax.fill_between(u.time_h[m], u[col_lo][m], u[col_hi][m], color=col, alpha=0.12, lw=0, zorder=1)
            md = d.time_h <= tmax
            ax.plot(d.time_h[md], d[col_v][md], color=col, lw=5, zorder=3, solid_capstyle="round")
            yv = float(np.interp(tmax - 0.2, d.time_h, d[col_v]))
            ax.text(tmax + 0.25, yv, lab, fontsize=LBL, fontweight="bold", color=col if lab == "A" else "#d4660a",
                    va="center", ha="left", clip_on=False)
    axs[0].set_ylim(3.7, 5.45)
    axs[0].set_yticks([4.0, 4.5, 5.0])
    axs[0].text(2.0, 3.85, "B more acidic", fontsize=LBL, fontweight="bold", color=INK, va="center")
    ax = axs[1]
    ax.set_ylim(0.95, 2.65)
    ax.set_yticks([1.0, 1.5, 2.0, 2.5])
    oA, oB = sA["overproof_onset_h"], sB["overproof_onset_h"]
    yo = {}
    for d, o, col in ((dA, oA, A_COL), (dB, oB, B_COL)):
        yo[o] = float(np.interp(o, d.time_h, d.expansion_ratio))
        ax.plot([o], [yo[o]], "v", ms=22, color="#c62828", mec="white", mew=2.5, zorder=6)
    ya = 2.45
    for o in (oA, oB):
        ax.plot([o, o], [yo[o] + 0.06, ya], color="#c62828", lw=1.6, ls=(0, (3, 3)), zorder=2)
    ax.add_patch(FancyArrowPatch((oB, ya), (oA, ya), arrowstyle="<|-|>,head_length=0.8,head_width=0.4",
                                 mutation_scale=20, color="#c62828", lw=2.4, zorder=4))
    ax.text((oA + oB) / 2, ya + 0.05, f"B overproofs ~{oA - oB:.0f} h earlier", fontsize=SMALL, fontweight="bold",
            color="#c62828", ha="center", va="bottom")
    return save(fig, "results_ab")


def fig_agents_exchange():
    fig, ax = canvas()
    _b(ax, 157, 87.0, "EXAMPLE", "example", ha="right", va="top")
    cw, cg, cy0, ch = 46.0, 6.5, 12.0, 62.0
    cards = [("A proposes", A_COL, [([(0, 27), (16, 27)], B_COL)], "27 °C"),
             ("B critiques", B_COL, [([(0, 27), (6, 27), (6, 22), (16, 22)], B_COL)], "27 → 22 °C"),
             ("Agreed", INK, [([(0, 22), (14, 22)], A_COL), ([(0, 27), (6, 27), (6, 22), (16, 22)], B_COL)], None)]
    for k, (head, col, series, lab) in enumerate(cards):
        x = 3 + k * (cw + cg)
        rbox(ax, x, cy0, cw, ch, fc="white", ec=col, lw=3.5, r=2.2)
        ax.add_patch(FancyBboxPatch((x, cy0 + ch - 11), cw, 11, boxstyle="round,pad=0,rounding_size=2.2", fc=col,
                                    ec=col, lw=3.5, zorder=2))
        ax.add_patch(Rectangle((x + 0.2, cy0 + ch - 11), cw - 0.4, 4, fc=col, ec="none", zorder=2))
        ax.text(x + cw / 2, cy0 + ch - 5.5, head, fontsize=30, fontweight="bold", color="white", ha="center",
                va="center", zorder=3)
        _mini_schedule(ax, x + 5, cy0 + 12, cw - 10, 28, series)
        if lab:
            ax.text(x + cw / 2, cy0 + 5.5, lab, fontsize=LBL, fontweight="bold", color=INK, ha="center", va="center")
        else:
            ax.text(x + cw - 4, cy0 + 12 + 28 * (22 - 19) / 10 + 3.0, "A", fontsize=LBL, fontweight="bold",
                    color=A_COL, ha="center")
            ax.text(x + 9, cy0 + 12 + 28 * (27 - 19) / 10 + 2.0, "B", fontsize=LBL, fontweight="bold",
                    color="#d4660a", ha="center")
        if k < 2:
            arrow(ax, (x + cw + 0.6, cy0 + ch / 2), (x + cw + cg - 0.6, cy0 + ch / 2), color=FAINT, lw=4, head=26)
    return save(fig, "agents_exchange")


def fig_benchmark_summary():
    b = benchmark_numbers()
    fig, ax = canvas()
    _b(ax, 157, 87.0, "MODELED · in-silico", "modeled", ha="right", va="top")
    tiles = [(f"{b['changed']:.1f} / {b['n_rounds']}", "choices changed"),
             (f"{b['dup_ind']:.1f} → {b['dup_col']:.0f}", "duplicate experiments"),
             (f"+{b['info']['agent']['mean']:.2f} nats", "more information"),
             ("same", "final pick")]
    tw, th = 75.5, 37.0
    for i, (big, small) in enumerate(tiles):
        x = 3 + (i % 2) * (tw + 3.0)
        y = 44.5 - (i // 2) * (th + 3.5)
        rbox(ax, x, y, tw, th, fc=PANEL, ec="none", r=2.4)
        ax.text(x + 5, y + th * 0.60, big, fontsize=64, fontweight="bold", color=INK if i < 3 else MUTED,
                va="center")
        ax.text(x + 5, y + th * 0.20, small, fontsize=30, color=MUTED, va="center")
    return save(fig, "benchmark_summary")


def clear_build_numbers() -> dict | None:
    """Clear V5 Form 4 build (both crumb specimens + two chef hats), from the live PreForm run."""
    if not CLEAR_MANIFEST.exists():
        return None
    m = json.loads(CLEAR_MANIFEST.read_text())
    assert m["job_submitted"] is False
    e = m["build"]["estimates"]
    return {"time_h": e["print_time_h"], "resin_ml": e["material_volume_ml"], "material": m["material"]["label"]}




def _acid_scale(ax, x0, x1, y, c2):
    """Horizontal pKa / pH scale: experiment (solid), GPU gap anchored on acetic (hollow), sourdough pH band."""
    lo, hi = -1.5, 6.5
    PKA_EXP = c2["pka_exp"]  # experimental aqueous pKa, as recorded in c2_summary.json

    def X(v):
        return x0 + (x1 - x0) * (v - lo) / (hi - lo)

    ax.add_patch(Rectangle((X(3.9), y - 1.8), X(4.3) - X(3.9), 3.6, fc="#f3dcb8", ec="none", zorder=1))
    ax.plot([x0, x1], [y, y], color=INK, lw=2.5, zorder=2)
    for v in (0, 2, 4, 6):
        ax.plot([X(v), X(v)], [y - 0.8, y + 0.8], color=INK, lw=2, zorder=2)
        ax.text(X(v), y - 2.4, f"{v}", fontsize=SMALL, color=MUTED, ha="center", va="top")
    # row above: direction words and the unit
    ax.text(x0, y + 8.0, "◄ stronger acid", fontsize=SMALL, color=MUTED, ha="left", va="center")
    ax.text(x1, y + 8.0, "weaker acid ►", fontsize=SMALL, color=MUTED, ha="right", va="center")
    ax.text((x0 + x1) / 2 - 1.0, y + 8.0, "pKa", fontsize=SMALL, color=MUTED, ha="center", va="center")
    # experiment: solid markers with names just above
    for v in PKA_EXP.values():
        ax.add_patch(Circle((X(v), y), 1.25, fc=INK, ec="white", lw=2, zorder=5))
    ax.text(X(PKA_EXP["lactic"]) + 0.6, y + 2.2, "lactic", fontsize=SMALL, fontweight="bold", color=INK,
            ha="right", va="bottom")
    ax.text(X(PKA_EXP["acetic"]) - 0.2, y + 2.2, "acetic", fontsize=SMALL, fontweight="bold", color=INK,
            ha="left", va="bottom")
    # GPU: lactic = acetic(exp) + computed dpKa for n = 3, 6, 10, hollow markers walking toward experiment
    gpu = [PKA_EXP["acetic"] + v for n, v in c2["by_n"] if n in (3, 6, 10)]
    ax.add_patch(FancyArrowPatch((X(gpu[0]), y + 1.6), (X(PKA_EXP["lactic"]) - 1.6, y + 1.6),
                                 arrowstyle="-|>,head_length=0.7,head_width=0.4", mutation_scale=18, color=TEAL,
                                 lw=2.2, alpha=0.55, zorder=3))
    for v in gpu:
        ax.add_patch(Circle((X(v), y), 1.15, fc="white", ec=TEAL, lw=3, zorder=4))
    # row below the tick labels: what the hollow markers are, and the sourdough band
    ax.text(x0, y - 7.6, "GPU, more water →", fontsize=SMALL, color=TEAL, fontweight="bold", ha="left", va="center")
    ax.text((X(3.9) + X(4.3)) / 2 + 4.0, y - 7.6, "sourdough pH", fontsize=SMALL, color="#9a6a2c",
            fontweight="bold", ha="center", va="center")


def _mini_curves(ax, x0, y0, w, h, series, t_end=16.0):
    """Two tiny A/B curves (no axes) in a w x h box; series = [(t, v, colour)]."""
    vals = np.concatenate([v[t <= t_end] for t, v, _ in series])
    vmin, vmax = float(vals.min()), float(vals.max())
    for t, v, col in series:
        m = t <= t_end
        ax.plot(x0 + w * t[m] / t_end, y0 + h * (v[m] - vmin) / max(vmax - vmin, 1e-9), color=col, lw=3,
                solid_capstyle="round", zorder=4)


def fig_tech_highlights():
    c2 = alchemi_c2()
    cb = clear_build_numbers()
    dA, _, sA = _load_chamber("A")
    dB, _, sB = _load_chamber("B")
    fig, ax = canvas()
    ty, th = 46.0, 42.0  # top row
    # ---------------- NVIDIA ALCHEMI
    x, w = 2.0, 68.0
    rbox(ax, x, ty, w, th, fc="white", ec=TEAL, lw=3, r=2.2)
    ax.text(x + 2.5, ty + th - 4.5, "NVIDIA ALCHEMI", fontsize=28, fontweight="bold", color=INK, va="center")
    _b(ax, x + w - 2.5, ty + th - 4.5, "COMPUTED", "computed", ha="right")
    ax.text(x + 2.5, ty + th - 10.0, "which acid is stronger?", fontsize=SMALL, color=MUTED, va="center")
    _acid_scale(ax, x + 3.0, x + w - 3.0, ty + 18.5, c2)
    for k, big in enumerate((f"{c2['n_opt']:,} GPU runs",)):  # single tile; no cost on figures
        bw = w - 5.0
        bx = x + 2.5
        rbox(ax, bx, ty + 1.8, bw, 6.0, fc=PANEL, ec="none", r=1.4)
        ax.text(bx + bw / 2, ty + 4.8, big, fontsize=24, fontweight="bold", color=INK, ha="center", va="center")
    # ---------------- Fermentation model
    x, w = 73.0, 85.0
    rbox(ax, x, ty, w, th, fc="white", ec=PURPLE, lw=3, r=2.2)
    ax.text(x + 2.5, ty + th - 4.5, "Fermentation model", fontsize=28, fontweight="bold", color=INK, va="center")
    _b(ax, x + w - 2.5, ty + th - 4.5, "MODELED", "modeled", ha="right")
    top = ty + th - 12.5
    inputs = ["recipe", "schedule", "starter", "acid terms"]
    iy = [top - k * 7.6 for k in range(4)]
    for k, (name, yy) in enumerate(zip(inputs, iy)):
        col = TEAL if k == 3 else INK
        ax.text(x + 2.5, yy, name, fontsize=SMALL, fontweight="bold", color=col, va="center")
        if k == 1:  # tiny temperature-step glyph
            gx = x + 18.3
            ax.plot([gx, gx + 2.2, gx + 2.2, gx + 4.4], [yy + 1.0, yy + 1.0, yy - 1.0, yy - 1.0], color=B_COL, lw=3)
    mbx, mby, mbw, mbh = x + 26.5, ty + 3.0, 20.0, top - ty + 2.5
    rbox(ax, mbx, mby, mbw, mbh, fc="#f1ecf8", ec=PURPLE, lw=2.5, r=1.8)
    ax.text(mbx + mbw / 2, mby + mbh - 2.4, "ODE\nkinetics", fontsize=SMALL, fontweight="bold", color=INK,
            ha="center", va="top", linespacing=1.05)
    for k, sub in enumerate(("yeast", "bacteria", "sugar", "acids")):
        ax.text(mbx + mbw / 2, mby + mbh - 12.5 - k * 4.3, sub, fontsize=SMALL, color=MUTED, ha="center",
                va="center")
    for k, yy in enumerate(iy):
        arrow(ax, (x + (23.2 if k == 1 else 22.0), yy), (mbx - 0.4, yy), color=FAINT, lw=2.5, head=16)
    # green arrow from the NVIDIA panel into "acid terms"
    arrow(ax, (70.4, iy[3]), (x + 2.0, iy[3]), color=TEAL, lw=3.5, head=20)
    outs = [("pH", "ph"), ("lactic/acetic", "lactic_acid"), ("CO₂", "co2_cumulative"), ("rise", "expansion_ratio"),
            ("overproof", None)]
    ox = mbx + mbw + 2.5
    oy = [top + 1.0 - k * 6.2 for k in range(5)]
    for (name, col_), yy in zip(outs, oy):
        arrow(ax, (mbx + mbw + 0.4, yy), (ox - 0.2, yy), color=FAINT, lw=2.0, head=12)
        ax.text(ox + 0.5, yy, name, fontsize=SMALL, fontweight="bold", color=INK, va="center")
        cx0, cw_, ch_ = x + w - 12.5, 10.5, 3.6
        if col_:
            _mini_curves(ax, cx0, yy - ch_ / 2, cw_, ch_, [(dA.time_h.to_numpy(), dA[col_].to_numpy(), A_COL),
                                                             (dB.time_h.to_numpy(), dB[col_].to_numpy(), B_COL)])
        else:  # overproof onset times on a tiny 0-16 h line
            ax.plot([cx0, cx0 + cw_], [yy - 1.2, yy - 1.2], color=LINE, lw=2)
            for o, col in ((sA["overproof_onset_h"], A_COL), (sB["overproof_onset_h"], B_COL)):
                ax.plot([cx0 + cw_ * o / 16.0], [yy], "v", ms=12, color=col, mec="white", mew=1.5, zorder=5)
    # ---------------- Formlabs PreForm API (bottom, full width)
    x, y, w, h = 2.0, 2.0, 156.0, 41.0
    rbox(ax, x, y, w, h, fc="white", ec=TEAL, lw=3, r=2.2)
    ax.text(x + 2.5, y + h - 4.5, "Formlabs PreForm API", fontsize=28, fontweight="bold", color=INK, va="center")
    _b(ax, x + 55.0, y + h - 4.5, "COMPUTED", "computed", ha="left")
    if HERO_PNG.exists():
        from PIL import Image

        hero = Image.open(HERO_PNG).convert("RGB")
        w_, h_ = hero.size
        for j, (fx0, fx1) in enumerate(((0.015, 0.395), (0.515, 0.895))):
            crop = hero.crop((int(w_ * fx0), int(h_ * 0.27), int(w_ * fx1), int(h_ * 0.84)))
            _fit_image(ax, np.asarray(crop), x + 2.5 + j * 36.0, y + 2.5, 34.0, 29.0)
        _b(ax, x + 3.5, y + 30.0, "MODELED", "modeled", ha="left")
    if CLEAR_ISO.exists():
        from PIL import Image

        _fit_image(ax, np.asarray(Image.open(CLEAR_ISO).convert("RGB")), x + 77.0, y + 2.0, 38.0, 35.0)
    if cb:
        for k, big in enumerate((f"{cb['time_h']:.1f} h", f"{cb['resin_ml']:.0f} mL", cb["material"])):
            tyy = y + 26.5 - k * 11.5
            rbox(ax, x + 118.0, tyy, w - 120.5, 10.0, fc=PANEL, ec="none", r=1.6)
            ax.text(x + 118.0 + (w - 120.5) / 2, tyy + 5.0, big, fontsize=30, fontweight="bold", color=INK,
                    ha="center", va="center")
    return save(fig, "tech_highlights")


FIGURES = {
    "closed_loop": fig_closed_loop,
    "evidence_pipeline": fig_evidence_pipeline,
    "architecture": fig_architecture,
    "results_ab": fig_results_ab,
    "agents_exchange": fig_agents_exchange,
    "benchmark_summary": fig_benchmark_summary,
    "tech_highlights": fig_tech_highlights,
    "dataflow": fig_dataflow,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", choices=sorted(FIGURES), help="build only these figures")
    args = ap.parse_args(argv)
    np.random.seed(0)
    for name in args.only or FIGURES:
        for p in FIGURES[name]():
            print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
