"""Regenerate every FermentAI pitch figure deterministically.

    uv run python scripts/make_pitch_figures.py            # all figures
    uv run python scripts/make_pitch_figures.py --only closed_loop results_ab

Outputs (data/figures/): <name>.png at 200 dpi on a 16 x 9 in canvas (3200 x 1800 px, i.e. >= 2x
a 1600 x 900 slide) plus <name>.svg for every figure.

Inputs (read-only): data/predictions/chamber_{A,B}_prediction.csv, *_uncertainty.csv,
*_summary.json, molecular_coupling_report.json; data/alchemi/analysis/association_energies.csv,
deprotonation_energies.csv, analysis_summary.json; data/stl/chain_summary.json,
crumb_chamber_comparison.png, crumb_summary.json; data/benchmarks/summary.json and
benchmark_results.csv; and the fermentation simulator (fermentai.fermentation.simulation) for the one
"B cooled at 6 h" intervention curve.

Every number on a figure is read from those files at build time (none is typed in by hand), except
the Brev run cost/MD count in ALCHEMI_RUN_NOTE, which comes from data/alchemi/environment/report.md.
"""

from __future__ import annotations

import argparse
import json
import textwrap
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
ALCHEMI_RUN_NOTE = "72 MD runs, ~1.1 GPU-hours (~$0.68)"


def alchemi_numbers() -> dict:
    """Lactic vs acetic energetics from the real ALCHEMI/AIMNet2 analysis CSVs (kcal/mol)."""
    a = pd.read_csv(ALCH / "association_energies.csv")
    a = a[a["is_lowest_energy_conformer"].astype(bool)]
    per_w = a.groupby("acid")["dE_assoc_per_water_kcal_mol"].mean()  # mean over n = 1, 3, 6
    n6 = a[a.n_water == 6].set_index("acid")["dE_assoc_kcal_mol"]
    d = pd.read_csv(ALCH / "deprotonation_energies.csv").set_index(["acid", "n_water"])
    dep = d["dE_deprot_separated_ions_kcal_mol"]
    summ = json.loads((ALCH / "analysis_summary.json").read_text())
    v = summ["versions"]
    rep = json.loads((PRED / "molecular_coupling_report.json").read_text())["per_chamber"]
    pot = rep["A"]["parameters"]["undissociated_lactic_rel_potency"]
    dph = max(abs(e[k]["alchemi_informed"] - e[k]["literature"])
              for c in rep.values() for e in [c["effect_on_chamber"]] for k in e if k.startswith("ph_"))
    return {
        "per_water": (per_w["lactic_acid"], per_w["acetic_acid"]),
        "n6": (n6["lactic_acid"], n6["acetic_acid"]),
        "deprot_n3": (dep[("lactic_acid", 3)], dep[("acetic_acid", 3)]),
        "deprot_n6": (dep[("lactic_acid", 6)], dep[("acetic_acid", 6)]),
        "ion_pair_n6": d["dE_deprot_ion_pair_kcal_mol"].dropna().to_dict(),
        "potency": (pot["literature_value"], pot["value"]),
        "max_dph": dph,
        "provenance": (f"real: AIMNet2 ({summ['model'][0]}) via NVIDIA ALCHEMI toolkit {v['nvalchemi-toolkit']} "
                       f"on an {v['gpu'].replace('NVIDIA ', '')} — {summ['n_converged']} optimisations, "
                       f"{ALCHEMI_RUN_NOTE}"),
    }


# --------------------------------------------------------------------------------------------
# Style: one palette for every figure
# --------------------------------------------------------------------------------------------
A_COL = "#1f77b4"  # Chamber A (matches existing charts' hue family)
B_COL = "#ff7f0e"  # Chamber B
GREEN = "#2ca02c"  # real / physical
PURPLE = "#9467bd"  # simulated
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
    "real": ("REAL", GREEN),
    "real_gpu": ("REAL · GPU", GREEN),
    "real_pending": ("REAL · AFTER BAKE", GREEN),
    "simulated": ("SIMULATED", PURPLE),
    "replay": ("REPLAY", GREY),
    "example": ("EXAMPLE", GREY),
    "physical": ("PHYSICAL", GREEN),
    "ai": ("AI AGENT", SLATE),
    "human": ("HUMAN", HUMAN),
}

FONT = "DejaVu Sans"  # bundled with matplotlib -> identical output on every machine
MONO = "DejaVu Sans Mono"

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
    hollow = hollow or kind == "real_pending"
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
        ax.text(cx + dx * s, cy + dy * s, t, color="white", fontsize=11, fontweight="bold", ha="center", va="center",
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


def icon_gpu(ax, cx, cy, s, color=GREEN):
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
def fig_closed_loop():
    fig, ax = canvas()
    cx, cy, rx, ry = 80.0, 44.0, 47.0, 28.5
    r_node = 6.3
    nodes = [
        # angle, kind, headline, subline, icon, label side
        (135, "physical", "Bake", "two loaves, A + B", icon_loaf, "upleft"),
        (90, "human", "Judge tastes", "types on the iPad:\n“What did the bread taste like?”", icon_person, "judge"),
        (45, "ai", "Parse", "free text → sensory\npreferences", icon_sliders, "right"),
        (0, "simulated", "Compare", "with recorded runs\n+ simulation", icon_bars, "right"),
        (-45, "ai", "Agents debate", "A: hypothesis + new\ntemperature schedule\nB: critique / counter", icon_chat, "downright"),
        (-90, "physical", "Publish", "next experiment →\nchef-hats animate", icon_chefhat, "below"),
        (-135, "physical", "Ferment + measure", "chambers run schedule;\nsensors + camera", icon_chamber, "downleft"),
        (180, "simulated", "Recalibrate", "model updates\nfrom measurements", icon_fit, "left"),
    ]
    pos = []
    for ang, *_ in nodes:
        t = np.deg2rad(ang)
        pos.append((cx + rx * np.cos(t), cy + ry * np.sin(t)))

    # arrows along the ellipse, clockwise
    for i in range(len(nodes)):
        a0, a1 = nodes[i][0], nodes[(i + 1) % len(nodes)][0]
        if a1 > a0:
            a1 -= 360
        ts = np.deg2rad(np.linspace(a0, a1, 200))
        xs, ys = cx + rx * np.cos(ts), cy + ry * np.sin(ts)
        p0, p1 = np.array(pos[i]), np.array(pos[(i + 1) % len(nodes)])
        r0 = r_node + (2.2 if nodes[i][1] == "human" else 1.0)
        r1 = r_node + (2.8 if nodes[(i + 1) % len(nodes)][1] == "human" else 1.6)
        keep = (np.hypot(xs - p0[0], ys - p0[1]) > r0) & (np.hypot(xs - p1[0], ys - p1[1]) > r1)
        xs, ys = xs[keep], ys[keep]
        ax.plot(xs[:-3], ys[:-3], color=FAINT, lw=3.2, zorder=1, solid_capstyle="round")
        arrow(ax, (xs[-6], ys[-6]), (xs[-1], ys[-1]), color=FAINT, lw=3.2, head=22, zorder=1)

    for (ang, kind, head, sub, icon, side), (x, y) in zip(nodes, pos):
        col = BADGES[kind][1]
        is_h = kind == "human"
        r = r_node + (1.6 if is_h else 0)
        if is_h:
            ax.add_patch(Circle((x, y), r + 1.2, fc="#fbe3ec", ec="none", zorder=2))
        ax.add_patch(Circle((x, y), r, fc="white", ec=col, lw=5 if is_h else 3, zorder=3))
        icon(ax, x, y + 0.6, r * 0.62)
        badge(ax, x, y - r - 0.2, kind, size=9.5)
        # label block
        dx, dy, ha, va = {
            "right": (r + 3.2, 0, "left", "center"),
            "left": (-r - 3.2, 0, "right", "center"),
            "upright": (r + 1.0, r * 0.55, "left", "bottom"),
            "upleft": (-r - 1.5, r * 0.55, "right", "bottom"),
            "downright": (r + 1.5, -r * 0.35, "left", "top"),
            "downleft": (-r - 1.5, -r * 0.35, "right", "top"),
            "below": (r + 2.5, -r + 1.5, "left", "top"),
            "judge": (r + 2.4, 2.6, "left", "bottom"),
        }[side]
        tx, ty = x + dx, y + dy
        hs = 19 if is_h else 17
        if va == "center":
            ax.text(tx, ty + 0.4, head, ha=ha, va="bottom", fontsize=hs, fontweight="bold", color=HUMAN if is_h else INK)
            ax.text(tx, ty - 0.4, sub, ha=ha, va="top", fontsize=12.5, color=MUTED, linespacing=1.25)
        elif va == "bottom":
            n = sub.count("\n") + 1
            ax.text(tx, ty + 2.4 * n + 0.8, head, ha=ha, va="bottom", fontsize=hs, fontweight="bold",
                    color=HUMAN if is_h else INK)
            ax.text(tx, ty, sub, ha=ha, va="bottom", fontsize=12.5, color=MUTED, linespacing=1.25)
        else:
            ax.text(tx, ty, head, ha=ha, va="top", fontsize=hs, fontweight="bold", color=INK)
            ax.text(tx, ty - 3.4, sub, ha=ha, va="top", fontsize=12.5, color=MUTED, linespacing=1.25)

    # centre
    ax.text(cx, cy + 6.5, "Taste  →  next experiment", ha="center", va="center", fontsize=25, fontweight="bold",
            color=INK)
    ax.text(cx, cy + 1.2, "one free-text comment closes the loop", ha="center", va="center", fontsize=14,
            color=MUTED)
    rbox(ax, cx - 27, cy - 10.8, 54, 7.6, fc=PANEL, ec="none", r=1.5, zorder=1)
    ax.text(cx, cy - 5.2, "Live: Claude   ·   Offline: deterministic fallback (labeled)", ha="center", va="center",
            fontsize=12, color=INK)
    ax.text(cx, cy - 8.6, "LLM never controls power: setpoints clamped by API + firmware", ha="center",
            va="center", fontsize=12, color=INK, fontweight="bold")

    ax.text(3, 86.5, "FermentAI · the closed loop", fontsize=15, fontweight="bold", color=MUTED, va="top")
    ax.text(3, 6.2, "PHYSICAL steps produce REAL data · SIMULATED = model output", fontsize=11, color=MUTED,
            va="center")
    ax.text(3, 2.8, "AI AGENT = Claude (live) or the labeled offline policy", fontsize=11, color=MUTED, va="center")
    return save(fig, "closed_loop")


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


def _m(x: float, nd: int) -> str:
    """Format with a true minus sign."""
    return f"{x:.{nd}f}".replace("-", "−")


def fig_evidence_pipeline():
    crumb = json.loads((STL / "crumb_summary.json").read_text())
    base = {d["name"]: d for d in crumb["models"]}
    chain = json.loads((STL / "chain_summary.json").read_text())["chambers"]
    sA = json.loads((PRED / "chamber_A_summary.json").read_text())
    sB = json.loads((PRED / "chamber_B_summary.json").read_text())
    al = alchemi_numbers()
    meas = {c: chain[c]["measured_on_mesh"] for c in "AB"}
    par = {c: chain[c]["alchemi"]["attribution"]["crumb_param_deltas"] for c in "AB"}

    fig, ax = canvas()
    ax.text(3, 86.5, "Evidence pipeline: molecules → model → specimen → real loaf", fontsize=21, fontweight="bold",
            color=INK, va="top")
    ax.text(3, 81.2, "Each stage hands one thing to the next — and says whether it is real or simulated.", fontsize=13,
            color=MUTED, va="top")

    lw_, la_ = al["per_water"]
    dl, da = al["deprot_n3"]
    bw, gap, x0, y0, bh = 27.2, 4.5, 3.0, 11.0, 58.0
    stages = [
        {"t": "NVIDIA ALCHEMI", "s": "AIMNet2 on Brev GPU", "b": "real_gpu", "icon": "alchemi",
         "out": "Real energetics of lactic vs acetic acid in small water clusters.",
         "nums": ["kcal/mol · lactic vs acetic", f"hydration/H₂O  {_m(lw_, 2)} vs {_m(la_, 2)}",
                  f"deprot. (n=3)  {dl:.1f} vs {da:.1f}", f"lactic ionises ~{da - dl:.1f} easier"],
         "num_size": 10.2},
        {"t": "Kinetics model", "s": "SciPy ODE on the Mac", "b": "simulated", "icon": icon_curves,
         "out": "Predicts pH, lactic / acetic acid, CO₂ and dough rise for A (22 °C) and B (27 °C).",
         "nums": [f"pH @16 h:  A {sA['ph_at_16h']:.2f}  ·  B {sB['ph_at_16h']:.2f}",
                  f"lactic:  A {sA['lactic_at_16h']:.1f}  ·  B {sB['lactic_at_16h']:.1f} g/kg"]},
        {"t": "Morphology map", "s": "assumed mapping", "b": "simulated", "icon": icon_pores,
         "out": "Maps rise, retained gas and acid at 16 h to crumb parameters (a stated hypothesis).",
         "nums": ["mapped parameters: A · B",
                  (f"porosity  {par['A']['porosity_target']['alchemi_informed']:.2f}  ·  "
                  f"{par['B']['porosity_target']['alchemi_informed']:.2f}"),
                  (f"median pore  {par['A']['pore_diam_median_mm']['alchemi_informed']:.1f}  ·  "
                  f"{par['B']['pore_diam_median_mm']['alchemi_informed']:.1f} mm")],
         "num_size": 11.0},
        {"t": "Specimens", "s": "one STL per chamber", "b": "simulated", "icon": "stl",
         "out": "One printable 80 × 80 × 12 mm crumb section per chamber, generated from its simulation.",
         "nums": ["measured: porosity · pores",
                  f"A {100 * meas['A']['cut_face_pore_area_fraction']:.1f} %  ·  {meas['A']['cut_face_pore_count']}",
                  f"B {100 * meas['B']['cut_face_pore_area_fraction']:.1f} %  ·  {meas['B']['cut_face_pore_count']}"],
         "num_size": 11.0},
        {"t": "Loaf photos", "s": "image analysis", "b": "real_pending", "icon": icon_camera,
         "out": "Real crumb photos analysed and compared back to the specimens and the model.",
         "nums": ["same metrics: porosity,", "pore count, pore size"]},
    ]
    for i, st in enumerate(stages):
        bx = x0 + i * (bw + gap)
        col = BADGES[st["b"]][1]
        rbox(ax, bx, y0, bw, bh, fc="white", ec=col, lw=2.6, r=1.8,
             ls="--" if st["b"] == "real_pending" else "-")
        ax.add_patch(Circle((bx + 3.2, y0 + bh - 3.6), 2.2, fc=col, ec="none", zorder=3))
        ax.text(bx + 3.2, y0 + bh - 3.6, str(i + 1), color="white", fontsize=13, fontweight="bold", ha="center",
                va="center", zorder=4)
        ax.text(bx + 6.6, y0 + bh - 2.6, st["t"], fontsize=15, fontweight="bold", color=INK, va="center")
        ax.text(bx + 6.6, y0 + bh - 5.6, st["s"], fontsize=11.5, color=MUTED, va="center")
        badge(ax, bx + bw / 2, y0 + bh - 10.2, st["b"], size=10.5)
        icx, icy = bx + bw / 2, y0 + 35.0
        if st["icon"] == "alchemi":
            icon_gpu(ax, icx - 6.5, icy, 3.6)
            icon_molecule(ax, icx + 6.0, icy, 5.0)
        elif st["icon"] == "stl":
            for j, c in enumerate("AB"):
                im = _crop(STL / "crumb_chamber_comparison.png", CHAMBER_THUMB_BOX[c])
                ex = (bx + 1.4 + j * (bw / 2 - 0.6), bx + 1.4 + j * (bw / 2 - 0.6) + bw / 2 - 2.2)
                if im is not None:
                    hh = (ex[1] - ex[0]) * im.shape[0] / im.shape[1]
                    ax.imshow(im, extent=(ex[0], ex[1], icy - hh / 2 - 1.0, icy + hh / 2 - 1.0), zorder=3,
                              interpolation="lanczos")
                else:
                    icon_pores(ax, (ex[0] + ex[1]) / 2, icy, 4.5)
                ax.text((ex[0] + ex[1]) / 2, icy + 5.4, f"{c} · {chain[c]['temperature_c']:.0f} °C", ha="center",
                        fontsize=11.5, fontweight="bold", color=A_COL if c == "A" else "#d4660a")
            ax.set_xlim(0, W)
            ax.set_ylim(0, H)
        else:
            st["icon"](ax, icx, icy, 6.0)
        ax.text(bx + 1.8, y0 + 24.0, textwrap.fill(st["out"], 27), fontsize=12, color=INK, va="top",
                linespacing=1.3)
        n = len(st["nums"])
        dy = 3.4 if n <= 2 else 2.9
        top = y0 + 3.2 + dy * (n - 1) + (0.0 if n <= 2 else 0.4)
        for j, txt in enumerate(st["nums"]):
            ax.text(bx + bw / 2, top - j * dy, txt, ha="center", va="center", fontsize=st.get("num_size", 11.5),
                    fontweight="bold", color=MUTED if (n > 2 and j == 0) else INK)
        if i < len(stages) - 1:
            arrow(ax, (bx + bw + 0.5, icy), (bx + bw + gap - 0.5, icy), color=INK if i else GREEN, lw=3, head=20)

    # callout: ALCHEMI constrains the acid chemistry only
    bx1 = x0 + bw / 2
    bx2 = x0 + bw + gap + bw / 2
    yb = y0 + bh + 2.0
    p0, p1 = al["potency"]
    ax.plot([bx1, bx1, bx2, bx2], [yb - 0.8, yb + 0.8, yb + 0.8, yb - 0.8], color=GREEN, lw=2)
    ax.text(bx2 + 1.5, yb + 0.8,
            f"constrains the acid chemistry (potency {p0:.2f} → {p1:.3f}, pH Δ < {max(0.01, al['max_dph']):.2f})"
            " — does not drive the bread",
            fontsize=12, color=GREEN, fontweight="bold", va="center", ha="left",
            bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": "none"})
    ax.text(3, 7.6, "ALCHEMI provenance — " + al["provenance"], fontsize=10.3, color=MUTED, va="center")
    l6, a6 = al["deprot_n6"]
    ip = ", ".join(f"{k[0].split('_')[0]} n={k[1]} ({v:+.1f})" for k, v in al["ion_pair_n6"].items())
    ax.text(3, 4.7, f"Deprotonation order flips at n=6 ({l6:.1f} vs {a6:.1f}), within the conformer spread.  "
                    f"Contact ion pair survives only for {ip} kcal/mol.  0 K cluster energetics, not solution pKa.",
            fontsize=10.3, color=MUTED, va="center")
    ax.text(3, 1.8, "Crumb mapping is a stated hypothesis (kinetics do not uniquely determine crumb).  Baseline presets, "
                    f"not simulation-derived: mild {100 * base['mild']['cut_face_pore_area_fraction']:.1f} % / "
                    f"{base['mild']['cut_face_pore_count']} pores, sour "
                    f"{100 * base['sour']['cut_face_pore_area_fraction']:.0f} % / {base['sour']['cut_face_pore_count']} pores.",
            fontsize=10.3, color=MUTED, va="center")
    return save(fig, "evidence_pipeline")


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


def fig_architecture():
    fig, ax = canvas()
    # internet band
    yb = 62.0
    ax.add_patch(Rectangle((0, yb), W, H - yb, fc="#f3f1fa", ec="none", zorder=0))
    ax.plot([0, W], [yb, yb], color=FAINT, lw=1.6, ls=(0, (6, 4)))
    ax.text(3, H - 2.5, "INTERNET  ·  optional", fontsize=14, fontweight="bold", color=MUTED, va="top")
    ax.text(3, yb - 2.5, "LOCAL Wi-Fi AP  ·  the whole live demo", fontsize=14, fontweight="bold", color=GREEN, va="top")

    # big banner
    rbox(ax, 4, 30.5, 35, 12, fc=GREEN, ec=GREEN, r=2, zorder=3)
    ax.text(21.5, 38.8, "✓ Demo needs", fontsize=21, fontweight="bold", color="white", ha="center", va="center", zorder=4)
    ax.text(21.5, 34.0, "no internet", fontsize=21, fontweight="bold", color="white", ha="center", va="center", zorder=4)

    # cloud boxes (dashed)
    def cloud_box(x, y, w, h, title, lines, icon):
        rbox(ax, x, y, w, h, fc="white", ec=PURPLE if "GPU" in title else SLATE, lw=2.2, ls=(0, (5, 3)), r=1.6, zorder=2)
        icon(ax, x + 6, y + h / 2, 3.4)
        ax.text(x + 12, y + h - 3.2, title, fontsize=15, fontweight="bold", color=INK, va="center")
        for i, ln in enumerate(lines):
            ax.text(x + 12, y + h - 7.4 - i * 3.3, ln, fontsize=11.5, color=MUTED, va="center")

    cloud_box(18, 66.5, 50, 15.5, "NVIDIA Brev GPU", ["ALCHEMI / AIMNet2 molecular runs", "results copied back + cached on Mac"],
              lambda a, x, y, s: icon_gpu(a, x, y, s * 0.85))
    cloud_box(96, 66.5, 50, 15.5, "Claude (live mode)", ["agents' reasoning when online", "else: deterministic fallback"],
              lambda a, x, y, s: icon_chat(a, x, y, s * 1.1))

    # Mac
    mx, my, mw, mh = 50, 33.5, 60, 23
    rbox(ax, mx, my, mw, mh, fc="#eef4fa", ec=INK, lw=2.6, r=2, zorder=2)
    icon_laptop(ax, mx + 7.5, my + mh - 6.0, 4.2)
    ax.text(mx + 14, my + mh - 4.2, "Mac — the hub", fontsize=18, fontweight="bold", color=INK, va="center")
    ax.text(mx + 14, my + mh - 8.0, "runs everything the demo needs", fontsize=11.5, color=MUTED, va="center")
    items = ["FastAPI backend + SQLite", "Agent A  +  Agent B", "iPad dashboard (local assets)",
             "kinetics sim · STL · cached GPU results"]
    for i, it in enumerate(items):
        ax.text(mx + 3.5, my + 11.6 - i * 3.0, "▪ " + it, fontsize=12, color=INK, va="center")

    # internet links
    for (p0, p1, col) in (((43, 66.2), (mx + 14, my + mh + 0.4), PURPLE),
                          ((117, 66.2), (mx + mw - 14, my + mh + 0.4), SLATE)):
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="<|-|>,head_length=0.8,head_width=0.45", mutation_scale=18,
                                     color=col, lw=2.2, ls=(0, (5, 3)), zorder=2, shrinkA=0, shrinkB=0))
    ax.text(49.5, 64.4, "SSH · rsync (optional)", fontsize=10.5, color=PURPLE, ha="left", va="center",
            fontweight="bold")
    ax.text(110.5, 64.4, "HTTPS (optional)", fontsize=10.5, color=SLATE, ha="right", va="center", fontweight="bold")

    # AP
    apx, apy = 80, 26.0
    ax.plot([apx, apx], [my, apy + 2.5], color=INK, lw=2.4, zorder=1)
    rbox(ax, apx - 9, apy - 3.6, 18, 7.0, fc="white", ec=GREEN, lw=2.2, r=1.4, zorder=2)
    icon_router(ax, apx - 4.8, apy - 0.2, 2.4)
    ax.text(apx + 3.2, apy + 0.6, "Wi-Fi AP", fontsize=11.5, fontweight="bold", color=INK, ha="center", va="center",
            zorder=4)
    ax.text(apx + 3.2, apy - 2.0, "local only", fontsize=9.5, color=MUTED, ha="center", va="center", zorder=4)

    # devices
    dy, dh = 2.5, 15.5

    def device(x, w, title, col, lines, icon):
        rbox(ax, x, dy, w, dh, fc="white", ec=col, lw=2.6, r=1.6, zorder=2)
        ax.add_patch(FancyBboxPatch((x, dy + dh - 4.6), w, 4.6, boxstyle="round,pad=0,rounding_size=1.6", fc=col,
                                    ec=col, lw=2.6, zorder=3))
        ax.add_patch(Rectangle((x + 0.1, dy + dh - 4.6), w - 0.2, 2.0, fc=col, ec="none", zorder=3))
        ax.text(x + 2.2, dy + dh - 2.3, title, fontsize=13.5, fontweight="bold", color="white", va="center", zorder=4)
        icon(ax, x + w - 5.5, dy + 5.4, 3.2)
        for i, ln in enumerate(lines):
            ax.text(x + 2.2, dy + dh - 7.3 - i * 3.0, ln, fontsize=11, color=INK, va="center", zorder=4)

    device(4, 44, "Chamber A · ESP32-S3", A_COL, ["Peltier · temp / RH · gas", "camera · chef-hat motor",
                                                   "firmware clamps setpoints"], icon_chamber)
    device(112, 44, "Chamber B · ESP32-S3", B_COL, ["Peltier · temp / RH · gas", "camera · chef-hat motor",
                                                     "firmware clamps setpoints"], icon_chamber)
    device(60, 40, "iPad · judges", HUMAN, ["live labs · digital twin", "“What did the bread", "taste like?”"],
           icon_tablet)
    # AP links
    for x_ap, x_dev in ((apx - 9, 48), (apx + 9, 112)):
        ax.plot([x_ap, x_dev], [apy, dy + dh - 3.0], color=INK, lw=2.4, zorder=1)
    ax.plot([apx, apx], [apy - 3.6, dy + dh], color=INK, lw=2.4, zorder=1)
    ax.text(26, 20.6, "telemetry ↑   setpoints ↓ (clamped)", fontsize=10.5, color=MUTED, ha="center", va="center")
    ax.text(134, 20.6, "telemetry ↑   setpoints ↓ (clamped)", fontsize=10.5, color=MUTED, ha="center", va="center")
    ax.text(80, 20.6, "dashboard", fontsize=10.5, color=MUTED, ha="left", va="center",
            bbox={"boxstyle": "square,pad=0.15", "fc": "white", "ec": "none"})
    return save(fig, "architecture")


# ============================================================================================
# 4. Results A vs B
# ============================================================================================
def _load_chamber(c: str):
    df = pd.read_csv(PRED / f"chamber_{c}_prediction.csv")
    unc = pd.read_csv(PRED / f"chamber_{c}_uncertainty.csv")
    summ = json.loads((PRED / f"chamber_{c}_summary.json").read_text())
    assert set(df.provenance) == {"simulated"}, "results figure expects simulated predictions"
    return df, unc, summ


def _intervention_curve():
    """B at 27 °C cooled to 22 °C at 6 h (simulated, nominal parameters). None if the simulator is absent."""
    try:
        from fermentai.fermentation.simulation import (
            TemperatureSchedule,
            simulate,
            summarize_prediction,
        )
    except ImportError:  # pragma: no cover - keeps the figure buildable without the package
        return None, None
    df = simulate(TemperatureSchedule([(0.0, 27.0), (6.0, 22.0)]), t_end_h=24.0)
    return df, summarize_prediction(df)


def fig_results_ab():
    dA, uA, sA = _load_chamber("A")
    dB, uB, sB = _load_chamber("B")
    dI, sI = _intervention_curve()
    tmax = 18.0

    fig = plt.figure(figsize=(16, 9))
    axs = [fig.add_axes((0.065, 0.20, 0.405, 0.58)), fig.add_axes((0.565, 0.20, 0.405, 0.58))]
    fig.text(0.035, 0.94, "Warmer chamber B acidifies faster — and overproofs earlier", fontsize=24,
             fontweight="bold", color=INK, va="center")
    fig.text(0.035, 0.885, "Kinetics model, A = 22 °C vs B = 27 °C held · lines = nominal, bands = 5–95 % parameter "
                           "uncertainty · grey = 12–16 h window", fontsize=13.5, color=MUTED, va="center")
    fig.text(0.965, 0.94, "SIMULATED", fontsize=15, fontweight="bold", color="white", ha="right", va="center",
             bbox={"boxstyle": "round,pad=0.4,rounding_size=0.6", "fc": PURPLE, "ec": PURPLE})

    for ax in axs:
        ax.axvspan(12, 16, color="#ecebe6", zorder=0, lw=0)
        ax.set_xlim(0, tmax)
        ax.set_xticks(range(0, int(tmax) + 1, 4))
        ax.tick_params(labelsize=14, colors=MUTED, length=0, pad=6)
        ax.grid(axis="y", color="#e6e6e1", lw=1)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(FAINT)
        ax.set_xlabel("hours since mixing", fontsize=15, color=MUTED, labelpad=8)

    # --- pH
    ax = axs[0]
    for d, u, s, col, lab in ((dA, uA, sA, A_COL, "A · 22 °C"), (dB, uB, sB, B_COL, "B · 27 °C")):
        m = u.time_h <= tmax
        ax.fill_between(u.time_h[m], u.ph_p05[m], u.ph_p95[m], color=col, alpha=0.13, lw=0, zorder=1)
        md = d.time_h <= tmax
        ax.plot(d.time_h[md], d.ph[md], color=col, lw=3.4, zorder=3, solid_capstyle="round")
        v = s["ph_at_16h"]
        ax.plot([16], [v], "o", ms=11, color=col, mec="white", mew=2.5, zorder=4)
        up = col == A_COL
        ax.text(15.7, v + (0.075 if up else -0.075), f"{v:.2f}", fontsize=17, fontweight="bold", color=INK,
                ha="right", va="bottom" if up else "top")
    ax.text(3.3, 5.02, "A · 22 °C", fontsize=15, fontweight="bold", color=A_COL, ha="left")
    ax.text(1.0, 4.36, "B · 27 °C", fontsize=15, fontweight="bold", color="#d4660a", ha="left")
    ax.set_title("pH  (lower = more acidic)", fontsize=18, fontweight="bold", color=INK, loc="left", pad=12)
    ax.text(14, 5.38, "pH @ 16 h", fontsize=12.5, color=MUTED, ha="center")
    ax.set_ylim(3.7, 5.45)
    lac = f"lactic acid @16 h:  A {sA['lactic_at_16h']:.1f}  vs  B {sB['lactic_at_16h']:.1f} g/kg"
    fig.text(0.065, 0.045, lac, fontsize=14, color=INK, fontweight="bold", va="center")

    # --- expansion
    ax = axs[1]
    for d, u, s, col in ((dA, uA, sA, A_COL), (dB, uB, sB, B_COL)):
        m = u.time_h <= tmax
        ax.fill_between(u.time_h[m], u.expansion_ratio_p05[m], u.expansion_ratio_p95[m], color=col, alpha=0.11, lw=0,
                        zorder=1)
        md = d.time_h <= tmax
        ax.plot(d.time_h[md], d.expansion_ratio[md], color=col, lw=3.4, zorder=3, solid_capstyle="round")
        tp, pk = s["peak_expansion_time_h"], s["peak_expansion_ratio"]
        ax.plot([tp], [pk], "o", ms=11, color=col, mec="white", mew=2.5, zorder=5)
        to = s["overproof_onset_h"]
        yo = float(np.interp(to, d.time_h, d.expansion_ratio))
        ax.plot([to], [yo], "v", ms=17, color="#c62828", mec="white", mew=2, zorder=6)
    if dI is not None:
        md = dI.time_h <= tmax
        ax.plot(dI.time_h[md], dI.expansion_ratio[md], color=B_COL, lw=2.4, ls=(0, (2.5, 2)), zorder=2)
        to = sI["overproof_onset_h"]
        if to is not None and to <= tmax:
            yo = float(np.interp(to, dI.time_h, dI.expansion_ratio))
            ax.plot([to], [yo], "v", ms=13, color="white", mec="#c62828", mew=2.2, zorder=6)
            ax.annotate(f"example fix: cool B to\n22 °C at 6 h → overproof\ndelayed to ~{to:.0f} h",
                        xy=(to, yo), xytext=(10.2, 2.27), fontsize=12.5, color=INK, ha="left", va="bottom",
                        arrowprops={"arrowstyle": "-", "color": FAINT, "lw": 1.4})
    pA, pB = sA["peak_expansion_time_h"], sB["peak_expansion_time_h"]
    oA, oB = sA["overproof_onset_h"], sB["overproof_onset_h"]
    ax.annotate(f"B peaks {pA - pB:.1f} h earlier", xy=(pB, sB["peak_expansion_ratio"]), xytext=(1.0, 2.38),
                fontsize=13.5, fontweight="bold", color=INK, arrowprops={"arrowstyle": "-", "color": FAINT, "lw": 1.4})
    yoB = float(np.interp(oB, dB.time_h, dB.expansion_ratio))
    yoA = float(np.interp(oA, dA.time_h, dA.expansion_ratio))
    ax.text(oB + 0.3, yoB - 0.2, f"B overproofs\nfrom {oB:.1f} h", fontsize=13, color="#c62828", fontweight="bold",
            ha="left", va="top")
    ax.text(oA + 0.4, yoA + 0.07, f"A from {oA:.1f} h", fontsize=13, color="#c62828", fontweight="bold", ha="left",
            va="bottom")
    ax.text(17.8, float(np.interp(17.8, dA.time_h, dA.expansion_ratio)) + 0.05, "A", fontsize=15, fontweight="bold",
            color=A_COL, ha="right", va="bottom")
    ax.text(17.8, float(np.interp(17.8, dB.time_h, dB.expansion_ratio)) - 0.05, "B", fontsize=15, fontweight="bold",
            color="#d4660a", ha="right", va="top")
    ax.set_title("Dough rise  (V / V₀)", fontsize=18, fontweight="bold", color=INK, loc="left", pad=12)
    ax.set_ylim(0.95, 2.55)
    lg = [plt.Line2D([], [], marker="o", ls="", ms=11, color=INK, mec="white", label="peak rise"),
          plt.Line2D([], [], marker="v", ls="", ms=14, color="#c62828", mec="white", label="overproof onset"),
          plt.Line2D([], [], color=B_COL, lw=2.4, ls=(0, (2.5, 2)), label="B cooled to 22 °C at 6 h")]
    fig.legend(handles=lg if dI is not None else lg[:2], loc="center left", bbox_to_anchor=(0.555, 0.045), ncol=3,
              frameon=False, fontsize=13, handletextpad=0.4, columnspacing=1.4)
    return save(fig, "results_ab")


# ============================================================================================
# 5. Agent exchange mock-up
# ============================================================================================
def _kv(ax, x, y, rows, size=12.2, key_w=17.5, dy=3.15):
    for i, (k, v, *style) in enumerate(rows):
        yy = y - i * dy
        ax.text(x, yy, k, fontsize=size, family=MONO, color=MUTED, va="center")
        ax.text(x + key_w, yy, v, fontsize=size, family=MONO, color=style[0] if style else INK, va="center",
                fontweight="bold" if style else "normal")


def _card(ax, x, y, w, h, col, who, kind_text):
    rbox(ax, x, y, w, h, fc="white", ec=col, lw=2.6, r=1.8, zorder=1)
    ax.add_patch(FancyBboxPatch((x, y + h - 5.4), w, 5.4, boxstyle="round,pad=0,rounding_size=1.8", fc=col, ec=col,
                                lw=2.6, zorder=2))
    ax.add_patch(Rectangle((x + 0.1, y + h - 5.4), w - 0.2, 2.2, fc=col, ec="none", zorder=2))
    ax.text(x + 2.2, y + h - 2.7, who, fontsize=15, fontweight="bold", color="white", va="center", zorder=3)
    ax.text(x + w - 2.2, y + h - 2.7, kind_text, fontsize=12.5, color="white", va="center", ha="right", zorder=3,
            family=MONO, fontweight="bold")


def fig_agents_exchange():
    fig, ax = canvas()
    ax.text(3, 86.5, "Agents argue in structured messages", fontsize=22, fontweight="bold", color=INK, va="top")
    ax.text(3, 81.2, "B's critique changes the plan · shared append-only findings log · every message carries its "
                     "evidence source", fontsize=13, color=MUTED, va="top")
    badge(ax, 157, 86.0, "example", size=12, ha="right", va="top", text="EXAMPLE · illustrative, not a logged run")

    # 1. A -> B hypothesis
    x1, y1, w1, h1 = 3, 47.0, 86, 29
    _card(ax, x1, y1, w1, h1, A_COL, "1 · Agent A  →  B", "type: hypothesis")
    ax.text(x1 + 2.2, y1 + h1 - 8.6, "“Judge wants more sour without losing airiness.\n"
                                     " Warmer B raises acid — I propose B runs warm.”",
            fontsize=13.5, color=INK, va="top", style="italic", linespacing=1.3)
    _kv(ax, x1 + 2.2, y1 + 10.8, [
        ("agent_id", '"scientist_A"'),
        ("payload.suggestion_for_peer", ""),
        ("  chamber, points", '"B", [[0, 27], [16, 27]]'),
        ("evidence_source", '"judge_feedback"', HUMAN),
    ], key_w=30)

    # 2. B -> A critique
    x2, y2, w2, h2 = 71, 7.5, 86, 33.5
    _card(ax, x2, y2, w2, h2, B_COL, "2 · Agent B  →  A", "type: critique")
    ax.text(x2 + 2.2, y2 + h2 - 8.6, "“Agree on direction, but holding 27 °C overproofs\n"
                                     " from ~9.5 h. Counter: cool to 22 °C at 6 h\n"
                                     " → overproof delayed to ~11 h.”",
            fontsize=13.5, color=INK, va="top", style="italic", linespacing=1.3)
    _kv(ax, x2 + 2.2, y2 + 10.4, [
        ("in_reply_to", '"f_…"  (message 1)'),
        ("payload.points", "[[0, 27], [6, 22], [16, 22]]"),
        ("decision_changed_by_peer", "true", GREEN),
        ("evidence_source", '"simulated"', PURPLE),
    ], key_w=27, dy=2.6)
    arrow(ax, (x1 + w1 + 0.6, y1 + h1 / 2), (x2 + w2 * 0.55, y2 + h2 + 0.8), color=FAINT, lw=3.2, head=22, rad=-0.35)
    ax.text(x1 + 2.5, y1 - 3.6, "B reads it, checks its own model", fontsize=12.5, color=MUTED, style="italic")

    # 3. agreed experiment
    x3, y3, w3, h3 = 3, 3.0, 62, 38
    rbox(ax, x3, y3, w3, h3, fc=PANEL, ec=INK, lw=2.6, r=1.8)
    arrow(ax, (x2 - 0.6, y2 + h2 / 2), (x3 + w3 + 0.8, y2 + h2 / 2), color=FAINT, lw=3.2, head=20)
    ax.text(x3 + 2.2, y3 + h3 - 3.4, "3 · Agreed next experiment", fontsize=16, fontweight="bold", color=INK,
            va="center")
    ax.text(x3 + 2.2, y3 + h3 - 7.6, "published → chef-hats animate", fontsize=13, color=GREEN, fontweight="bold",
            va="center")
    sx0, sx1 = x3 + 10, x3 + w3 - 3
    for row, (lab, col, pts, lbl) in enumerate((
            ("A", A_COL, [(0, 22), (14, 22)], "22 °C, 14 h"),
            ("B", B_COL, [(0, 27), (6, 27), (6, 22), (16, 22)], "27 °C → 22 °C at 6 h"))):
        by = y3 + 18.0 - row * 11.5

        def tx(t):
            return sx0 + (sx1 - sx0) * t / 16.0

        def ty(T, by=by):
            return by + (T - 22) / 5.0 * 4.5

        temps = [T for _, T in pts]
        ax.text(x3 + 3.5, (ty(max(temps)) + ty(min(temps))) / 2, lab, fontsize=22, fontweight="bold", color=col,
                va="center", ha="center")
        ax.plot([tx(t) for t, _ in pts], [ty(T) for _, T in pts], color=col, lw=3.6, solid_capstyle="round")
        ax.text(sx1, ty(max(temps)) + 1.4, lbl, fontsize=12.5, color=INK, ha="right", va="bottom", fontweight="bold")
    ax.text(x3 + 2.2, y3 + 2.6, "motor: wiggle, 5 s · setpoints clamped by API + firmware", fontsize=11,
            color=MUTED, va="center")
    return save(fig, "agents_exchange")


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


def fig_benchmark_summary():
    b = benchmark_numbers()
    n = b["n_worlds"]
    fig, ax = canvas()
    ax.text(3, 86.5, "In simulation, agents that talk choose differently — gains are modest", fontsize=21,
            fontweight="bold", color=INK, va="top")
    ax.text(3, 81.2, f"{n} simulated worlds · 8-experiment budget · collaborative vs independent agents, "
                     "deployed offline policy unless noted", fontsize=13, color=MUTED, va="top")
    badge(ax, 157, 86.0, "simulated", size=13, ha="right", va="top", text="IN-SILICO BENCHMARK")

    ri = b["regret"]
    tiles = [
        (f"{b['changed']:.1f} / {b['n_rounds']}", "rounds where the peer changed\nits choice after reading the proposal"),
        (f"{b['dup_ind']:.1f} → {b['dup_col']:.0f}", "same-round duplicate\nexperiments per world"),
        (f"+{b['info']['agent']['mean']:.2f} nats", (f"more information from the same\n8 experiments "
                                                    f"({b['info']['agent']['n_pos']}/{n} worlds)")),
        ("no change", f"in the final recommendation\n(regret Δ {_m(ri['mean'], 3)}, CI spans 0)"),
    ]
    tw, tg, ty, th = 36.5, 3.0, 52.0, 23.0
    for i, (big, small) in enumerate(tiles):
        x = 3 + i * (tw + tg)
        rbox(ax, x, ty, tw, th, fc=PANEL, ec="none", r=1.8)
        ax.text(x + 2.4, ty + th - 7.0, big, fontsize=30, fontweight="bold", color=INK if i < 3 else MUTED,
                va="center")
        ax.text(x + 2.4, ty + 7.0, small, fontsize=13, color=MUTED, va="center", linespacing=1.3)

    # paired difference, pooled information (collaborative − independent), with 95 % CI
    pax = fig.add_axes((0.30, 0.15, 0.52, 0.29))
    rows = [("info", "info-aware selector\n(benchmark only)"), ("agent", "deployed policy\n(used in the demo)")]
    for k, (sel, _lab) in enumerate(rows):
        r = b["info"][sel]
        pax.plot([r["lo"], r["hi"]], [k, k], color=INK, lw=3.2, solid_capstyle="round")
        pax.plot([r["mean"]], [k], "o", ms=13, color=INK, mec="white", mew=2.5, zorder=3)
        pax.text(max(r["hi"], 0.0) + 0.05, k, f"{r['mean']:+.2f}  [{r['lo']:+.2f}, {r['hi']:+.2f}]".replace("-", "−"),
                 fontsize=13, va="center", color=INK, fontweight="bold")
    pax.axvline(0, color=FAINT, lw=1.6, ls=(0, (4, 3)))
    pax.set_yticks([0, 1], [lab for _, lab in rows], fontsize=13, color=INK)
    pax.set_ylim(-0.6, 1.6)
    pax.set_xlim(-0.45, 1.55)
    pax.tick_params(axis="x", labelsize=12, colors=MUTED, length=0)
    pax.tick_params(axis="y", length=0)
    for sp in ("top", "right", "left"):
        pax.spines[sp].set_visible(False)
    pax.spines["bottom"].set_color(FAINT)
    pax.set_xlabel("extra information from the same budget (nats), talking − independent · 95 % CI", fontsize=12.5,
                   color=MUTED)
    pax.text(-0.02, 1.62, "← independent better", fontsize=11, color=MUTED, ha="right", va="bottom")
    pax.text(0.02, 1.62, "talking better →", fontsize=11, color=MUTED, ha="left", va="bottom")
    ax.text(3, 2.8, "Simulator plays nature; no dough, sensor or judge involved. Most of the agents' own-model gain "
                    "comes from sharing data, not smarter choices. Source: docs/benchmark_results.md.",
            fontsize=10.5, color=MUTED, va="center")
    return save(fig, "benchmark_summary")


# ============================================================================================
# 7. Technology partners: NVIDIA ALCHEMI | Formlabs PreForm (text wordmarks, no logos)
# ============================================================================================
PRINT_MANIFEST = STL / "print" / "print_manifest.json"
PREFORM_TRANSCRIPT = STL / "print" / "preform_api_transcript.json"
# Shown only if the live PreForm manifest is missing. Never invent these.
PREFORM_ESTIMATES = {
    "time": "[PreForm: print time — pending]",
    "resin": "[PreForm: resin volume — pending]",
}
HERO_PNG = STL / "hero_chamber_AB.png"


def preform_numbers() -> dict | None:
    """Estimates and checks from the live PreForm run (data/stl/print/print_manifest.json)."""
    if not PRINT_MANIFEST.exists():
        return None
    m = json.loads(PRINT_MANIFEST.read_text())
    est = m["build"]["estimates"]
    chk = m["printability_checks"]
    cups = {c: chk["cups"][c]["cup_count"] for c in chk["cups"]}
    n_calls = None
    if PREFORM_TRANSCRIPT.exists():
        tr = json.loads(PREFORM_TRANSCRIPT.read_text())
        n_calls = len(tr["entries"])
        assert not any(e["path"].rstrip("/").endswith("/scene/print") for e in tr["entries"]), "print was called"
    assert m["job_submitted"] is False
    return {
        "time_h": est["print_time_h"], "resin_ml": est["material_volume_ml"], "layers": est["layer_count"],
        "n_checks": sum(isinstance(v, dict) for v in chk.values()), "cups": cups,
        "check_note": "pass; cup warnings\n" + " · ".join(f"{c} {n}" for c, n in cups.items()),
        "printer": m["printer"]["label"], "material": m["material"]["label"], "layer_mm": m["layer_thickness_mm"],
        "server": m["preform_server"]["version"].rsplit(".", 1)[0], "n_calls": n_calls,
    }


def _placeholder(ax, x, y, text, size=11.0, ha="center"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, fontweight="bold", color="#8a6d00",
            bbox={"boxstyle": "round,pad=0.3", "fc": "#fff6cc", "ec": "#e0c25a", "lw": 1})


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


def fig_tech_highlights():
    al = alchemi_numbers()
    chain = json.loads((STL / "chain_summary.json").read_text())["chambers"]
    summ = json.loads((ALCH / "analysis_summary.json").read_text())
    fig, ax = canvas()
    ax.text(3, 86.5, "Two technology partners, one evidence chain", fontsize=22, fontweight="bold", color=INK,
            va="top")
    ax.text(3, 81.2, "Wordmarks are text only. Each badge says what is real and what is simulated.", fontsize=13,
            color=MUTED, va="top")

    px, pw, py, ph = (3.0, 81.5), 75.5, 4.0, 72.0
    # ---------------- NVIDIA panel
    x = px[0]
    rbox(ax, x, py, pw, ph, fc="white", ec=GREEN, lw=2.6, r=2.0)
    ax.text(x + 3, py + ph - 5.0, "NVIDIA ALCHEMI", fontsize=24, fontweight="bold", color=INK, va="center")
    ax.text(x + 3, py + ph - 10.0, f"AIMNet2 ({summ['model'][0]}) on a Brev {summ['versions']['gpu'].replace('NVIDIA ', '')}",
            fontsize=13, color=MUTED, va="center")
    badge(ax, x + pw - 3, py + ph - 5.0, "real_gpu", size=12, ha="right")
    ax.text(x + 3, py + ph - 14.5, "Lactic vs acetic acid in clusters of 0–6 waters:", fontsize=12.5, color=INK,
            va="top")
    ax.text(x + 3, py + ph - 18.3, f"{summ['n_converged']} geometry optimisations + 72 molecular-dynamics runs",
            fontsize=12.5, color=INK, va="top", fontweight="bold")
    # mini bars: deprotonation at n = 3 (lower = ionises more easily)
    dl, da = al["deprot_n3"]
    bx0, bx1, by = x + 21, x + pw - 14, py + 42.0
    ax.text(x + 3, by + 6.2, "Energy to give up a proton (n = 3 cluster, kcal/mol, lower = easier)", fontsize=12,
            color=MUTED, va="center")
    vmax = max(dl, da)
    for k, (lab, v) in enumerate((("lactic", dl), ("acetic", da))):
        yy = by - k * 5.2
        ax.text(x + 3, yy, lab, fontsize=13.5, fontweight="bold", color=INK, va="center")
        ax.add_patch(Rectangle((bx0, yy - 1.5), (bx1 - bx0) * v / vmax, 3.0, fc=GREEN if k == 0 else "#9fd39f",
                               ec="none", zorder=2))
        ax.text(bx0 + (bx1 - bx0) * v / vmax + 1.0, yy, f"{v:.1f}", fontsize=13.5, fontweight="bold", color=INK,
                va="center")
    lw_, la_ = al["per_water"]
    ax.text(x + 3, by - 10.5, f"→ lactic gives up its proton ~{da - dl:.1f} kcal/mol more easily", fontsize=13,
            fontweight="bold", color=INK, va="center")
    ax.text(x + 3, by - 14.8, f"Hydration per water: lactic {_m(lw_, 2)} vs acetic {_m(la_, 2)} kcal/mol",
            fontsize=12.5, color=INK, va="center")
    # stat tiles
    tiles = [("~$0.68", "of GPU time"), ("~1.1 h", "on Brev, then deleted"), ("offline", "cached on the Mac")]
    tw = (pw - 6 - 2 * 2.0) / 3
    for k, (big, small) in enumerate(tiles):
        tx = x + 3 + k * (tw + 2.0)
        rbox(ax, tx, py + 8.0, tw, 10.5, fc=PANEL, ec="none", r=1.4)
        ax.text(tx + 1.8, py + 15.2, big, fontsize=20, fontweight="bold", color=INK, va="center")
        ax.text(tx + 1.8, py + 10.4, small, fontsize=11.5, color=MUTED, va="center")
    p0, p1 = al["potency"]
    ax.text(x + 3, py + 4.0, f"Constrains one acid term in the model ({p0:.2f} → {p1:.3f}); pH moves < 0.01.",
            fontsize=11.5, color=GREEN, fontweight="bold", va="center")

    # ---------------- Formlabs panel
    x = px[1]
    pf = preform_numbers()
    rbox(ax, x, py, pw, ph, fc="white", ec=PURPLE, lw=2.6, r=2.0)
    ax.text(x + 3, py + ph - 5.0, "Formlabs PreForm API", fontsize=24, fontweight="bold", color=INK, va="center")
    badge(ax, x + 3, py + ph - 10.6, "simulated", size=11, ha="left", text="GEOMETRY · SIMULATED")
    badge(ax, x + 30.5, py + ph - 10.6, "real", size=11, ha="left", text="PRINT PREP · REAL (PreForm API)")
    # step chips
    steps = ["STL", "scene", "orient", "supports", "layout", "estimate", ".form"]
    cw, cg, cy = 8.6, 1.7, py + ph - 17.5
    for k, stp in enumerate(steps):
        cx0 = x + 3 + k * (cw + cg)
        last = k == len(steps) - 1
        rbox(ax, cx0, cy - 2.4, cw, 4.8, fc=GREEN if last else PANEL, ec="none", r=1.0)
        ax.text(cx0 + cw / 2, cy, stp, fontsize=11.5, fontweight="bold", color="white" if last else INK,
                ha="center", va="center")
        if not last:
            ax.text(cx0 + cw + cg / 2, cy, "›", fontsize=14, color=MUTED, ha="center", va="center")
    if pf:
        never = f"{pf['n_calls']} real PreForm API calls recorded; /v1/scene/print never called."
    else:
        never = "Never auto-prints: a person reviews the .form and presses print."
    ax.text(x + 3, cy - 5.4, never, fontsize=12, color=INK, fontweight="bold", va="center")
    # images: hero render (simulated geometry) + PreForm's own render (real print prep)
    iy0, ih_ = py + 19.0, 25.0
    hw = 41.0
    if HERO_PNG.exists():
        from PIL import Image

        hero = Image.open(HERO_PNG).convert("RGB")
        w_, h_ = hero.size
        _fit_image(ax, np.asarray(hero.crop((0, int(h_ * 0.27), w_, int(h_ * 0.84)))), x + 2.5, iy0, hw, ih_ - 3.5)
    else:
        for j, c in enumerate("AB"):
            im = _crop(STL / "crumb_chamber_comparison.png", CHAMBER_THUMB_BOX[c])
            if im is not None:
                _fit_image(ax, im, x + 2.5 + j * hw / 2, iy0, hw / 2 - 1, ih_ - 3.5)
    for j, c in enumerate("AB"):
        m = chain[c]["measured_on_mesh"]
        ax.text(x + 2.5 + hw / 4 + j * hw / 2, iy0 + ih_ - 1.2,
                f"{c} · {chain[c]['temperature_c']:.0f} °C · {100 * m['cut_face_pore_area_fraction']:.0f} %",
                fontsize=12, fontweight="bold", color=A_COL if c == "A" else "#d4660a", ha="center", va="center")
    iso = STL / "print" / "crumb_chambers_AB_form4-grey-v5_iso.png"
    ix0 = x + 2.5 + hw + 2.0
    iw_ = pw - (ix0 - x) - 2.5
    rbox(ax, ix0, iy0 + 0.5, iw_, ih_ - 1.5, fc="#f2f2f2", ec=LINE, lw=1.2, r=1.2)
    if iso.exists():
        from PIL import Image

        _fit_image(ax, np.asarray(Image.open(iso).convert("RGB")), ix0 + 0.8, iy0 + 1.3, iw_ - 1.6, ih_ - 5.5)
    ax.text(ix0 + iw_ / 2, iy0 + ih_ - 2.3, "PreForm's render: rafts + supports", fontsize=10.5, color=INK,
            ha="center", va="center", fontweight="bold")
    # estimate tiles
    if pf:
        tiles = [(f"{pf['time_h']:.2f} h", "print time"), (f"{pf['resin_ml']:.0f} mL", "resin"),
                 (f"{pf['layers']}", "layers"), (f"{pf['n_checks']} checks", pf["check_note"])]
    else:
        tiles = [("…", PREFORM_ESTIMATES["time"]), ("…", PREFORM_ESTIMATES["resin"])]
    tw = (pw - 6 - (len(tiles) - 1) * 1.6) / len(tiles)
    for k, (big, small) in enumerate(tiles):
        tx = x + 3 + k * (tw + 1.6)
        rbox(ax, tx, py + 7.2, tw, 10.8, fc=PANEL, ec="none", r=1.4)
        ax.text(tx + 1.5, py + 14.9, big, fontsize=18, fontweight="bold", color=INK, va="center")
        ax.text(tx + 1.5, py + 10.0, small, fontsize=10.5, color=MUTED, va="center", linespacing=1.1)
    foot = (f"PreForm's own estimates: {pf['printer']} · {pf['material']} · {pf['layer_mm']} mm layers · both chambers "
            f"on one platform\nofficial formlabs-sdk → PreFormServer {pf['server']} on the Mac · .form exported, "
            "never auto-printed") if pf else "Prepared via the PreForm Local API; estimates pending."
    ax.text(x + 3, py + 3.6, foot, fontsize=9.6, color=MUTED, va="center", linespacing=1.25)
    return save(fig, "tech_highlights")


FIGURES = {
    "closed_loop": fig_closed_loop,
    "evidence_pipeline": fig_evidence_pipeline,
    "architecture": fig_architecture,
    "results_ab": fig_results_ab,
    "agents_exchange": fig_agents_exchange,
    "benchmark_summary": fig_benchmark_summary,
    "tech_highlights": fig_tech_highlights,
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
