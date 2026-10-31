"""Glance-readable FermentAI dataflow: 5 boxes, 5 arrows, 13 words.

Molecules (COMPUTED) -> Dough model (MODELED) -> Crumb (MODELED) -> Print (COMPUTED)
and Print -> Bake & taste (MEASURED) -> back into Dough model.

Deterministic: no randomness, fixed fonts, SVG hash salt.
    uv run python scripts/make_dataflow_simple.py
Writes data/figures/dataflow_simple.png (3200 x 1800) and .svg.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "figures"
HERO = ROOT / "data" / "stl" / "hero_chamber_A.png"
PRINT = ROOT / "data" / "stl" / "print" / "crumb_chambers_AB_form4-grey-v5_iso.png"

# Palette shared with scripts/make_pitch_figures.py
TEAL = "#00897b"  # COMPUTED
PURPLE = "#9467bd"  # MODELED
GREEN = "#2ca02c"  # MEASURED
A_COL = "#1f77b4"
B_COL = "#ff7f0e"
INK = "#1d1d1b"
ARROW = "#3a3a36"
BG = "#ffffff"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "svg.fonttype": "path",
        "svg.hashsalt": "fermentai-dataflow-simple",
        "path.simplify": True,
        "figure.facecolor": BG,
        "savefig.facecolor": BG,
    }
)

W, H = 160.0, 90.0  # 1 unit = 0.1 in on a 16 x 9 in figure; 200 dpi -> 3200 x 1800
TITLE = 40  # pt; 40 pt @ 200 dpi = 111 px (= 67 px at 1920 wide)
BADGE = 27  # pt; 75 px (= 45 px at 1920 wide)


def rbox(ax, x, y, w, h, ec, ls="-", lw=5, fc="white", r=2.4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, ls=ls, zorder=2))


def badge(ax, cx, cy, text, color, hollow=False):
    ax.text(cx, cy, text, fontsize=BADGE, fontweight="bold", ha="center", va="center",
            color=color if hollow else "white", zorder=6,
            bbox={"boxstyle": "round,pad=0.35,rounding_size=0.8", "fc": "white" if hollow else color,
                  "ec": color, "lw": 3, "ls": (0, (4, 2)) if hollow else "-"})


def title(ax, cx, cy, text):
    ax.text(cx, cy, text, fontsize=TITLE, fontweight="bold", ha="center", va="center",
            color=INK, zorder=6, linespacing=1.0)


def place_image(ax, img, cx, cy, maxw, maxh):
    ih, iw = img.shape[:2]
    s = min(maxw / iw, maxh / ih)
    w, h = iw * s, ih * s
    ax.imshow(img, extent=(cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2), zorder=4, interpolation="lanczos")


def hero_crop():
    im = Image.open(HERO).convert("RGB").crop((380, 530, 1800, 1690))
    a = np.asarray(im).astype(float)
    dist = np.abs(a - np.array([248, 245, 239])).sum(axis=2)
    alpha = np.clip((dist - 6) / 20, 0, 1)  # key out the beige backdrop
    return np.dstack([a / 255, alpha])


def print_crop():
    im = Image.open(PRINT).convert("RGBA").crop((195, 40, 985, 725))
    return np.asarray(im).astype(float) / 255


def molecule(ax, cx, cy, s):
    """Small ball-and-stick glyph (lactic-acid-like): carbons dark, oxygens red."""
    atoms = {"c1": (-2.4, -0.6), "c2": (0.0, 0.6), "c3": (2.4, -0.6),
             "o1": (0.0, 3.2), "o2": (4.6, 0.6), "o3": (2.4, -3.2)}
    bonds = [("c1", "c2"), ("c2", "c3"), ("c2", "o1"), ("c3", "o2"), ("c3", "o3")]
    for a, b in bonds:
        (x0, y0), (x1, y1) = atoms[a], atoms[b]
        ax.plot([cx + x0 * s, cx + x1 * s], [cy + y0 * s, cy + y1 * s], color="#55554f", lw=9,
                solid_capstyle="round", zorder=4)
    for k, (x, y) in atoms.items():
        col, r = (TEAL, 0.85) if k.startswith("c") else ("#d84339", 0.72)
        ax.add_patch(Circle((cx + x * s, cy + y * s), r * s, fc=col, ec="white", lw=3, zorder=5))


def curves(ax, cx, cy, w, h):
    """Two rise curves (chamber A blue, chamber B orange) on a faint baseline."""
    x = np.linspace(0, 1, 200)
    ax.plot([cx - w / 2, cx + w / 2], [cy - h / 2, cy - h / 2], color="#c9c9c2", lw=4, zorder=4)
    ax.plot([cx - w / 2, cx - w / 2], [cy - h / 2, cy + h / 2], color="#c9c9c2", lw=4, zorder=4)
    for k, col in ((9.0, A_COL), (12.0, B_COL)):
        y = 1 / (1 + np.exp(-k * (x - (0.5 if col == A_COL else 0.38))))
        ax.plot(cx - w / 2 + 0.06 * w + 0.9 * w * x, cy - h / 2 + 0.08 * h + 0.84 * h * y, color=col, lw=8,
                solid_capstyle="round", zorder=5)


def loaf(ax, cx, cy, s):
    """Bread glyph: domed loaf with three score marks."""
    t = np.linspace(0, np.pi, 80)
    top = np.c_[cx + 4.2 * s * np.cos(t), cy + 2.6 * s * np.sin(t)]
    body = np.r_[top, [[cx - 4.6 * s, cy], [cx - 4.6 * s, cy - 1.6 * s], [cx + 4.6 * s, cy - 1.6 * s],
                       [cx + 4.6 * s, cy]]]
    ax.add_patch(Polygon(body[::-1], closed=True, fc="#c8823a", ec="#7a4a1c", lw=5, joinstyle="round", zorder=4))
    for dx in (-2.0, 0.0, 2.0):
        ax.plot([cx + (dx - 0.8) * s, cx + (dx + 0.8) * s], [cy + 0.6 * s, cy + 2.0 * s], color="#f3dcae", lw=7,
                solid_capstyle="round", zorder=5)


def arrow(ax, p0, p1, color=ARROW, rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>,head_length=1.0,head_width=0.7",
                                 connectionstyle=f"arc3,rad={rad}", mutation_scale=22, color=color, lw=7,
                                 ls=ls, shrinkA=0, shrinkB=0, zorder=3))


def main():
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.axis("off")

    # top row: four boxes, left to right
    bw, gap, y0, bh = 32.0, 8.0, 36.0, 50.0
    xs = [4.0 + i * (bw + gap) for i in range(4)]
    spec = [("NVIDIA\nALCHEMI", TEAL, "COMPUTED"), ("Dough\nmodel", PURPLE, "MODELED"),
            ("Crumb", PURPLE, "MODELED"), ("Print", TEAL, "COMPUTED")]
    for x, (name, col, tag) in zip(xs, spec):
        rbox(ax, x, y0, bw, bh, ec=col)
        title(ax, x + bw / 2, y0 + bh - (8.0 if "\n" in name else 6.0), name)
        badge(ax, x + bw / 2, y0 + 5.2, tag, col)
    mid = y0 + 21.5  # centre of the picture slot
    molecule(ax, xs[0] + bw / 2 - 1.0, mid, 2.4)
    curves(ax, xs[1] + bw / 2, mid, 22.0, 15.0)
    place_image(ax, hero_crop(), xs[2] + bw / 2, mid + 4.0, bw - 3.0, 27.0)
    place_image(ax, print_crop(), xs[3] + bw / 2, mid + 4.0, bw - 3.0, 27.0)

    for i in range(3):
        arrow(ax, (xs[i] + bw + 1.2, y0 + bh / 2), (xs[i + 1] - 1.2, y0 + bh / 2))

    # bottom: the physical loop
    bx, by, bbw, bbh = 84.0, 4.0, 72.0, 22.0
    rbox(ax, bx, by, bbw, bbh, ec=GREEN, ls=(0, (5, 3)))
    loaf(ax, bx + 11.0, by + bbh / 2 - 0.5, 1.75)
    ax.text(bx + 22.0, by + bbh / 2 + 3.6, "Bake & taste", fontsize=TITLE, fontweight="bold", ha="left",
            va="center", color=INK, zorder=6)
    badge(ax, bx + 34.5, by + bbh / 2 - 5.0, "MEASURED", GREEN, hollow=True)

    px = xs[3] + bw / 2
    arrow(ax, (px, y0 - 1.2), (px, by + bbh + 1.2))
    dx = xs[1] + bw / 2
    arrow(ax, (bx - 1.2, by + bbh / 2), (dx, y0 - 1.2), color=GREEN, rad=-0.35)

    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "dataflow_simple.png", dpi=200, metadata={"Software": None})
    fig.savefig(OUT / "dataflow_simple.svg", metadata={"Date": None, "Creator": None})
    plt.close(fig)
    print("wrote", OUT / "dataflow_simple.png", "and .svg")


if __name__ == "__main__":
    main()
