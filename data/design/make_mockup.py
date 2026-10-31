#!/usr/bin/env python3
"""FermentAI Node: product-concept mockup renderer.

Builds the retrofit sensor module as layered vector art (oblique extrusion, gradients,
Gaussian-blur shadows) and writes:

  data/design/sensor_module_mockup.svg / .png   hero + callouts + spec strip (3200 px wide)
  data/design/sensor_module_context.svg / .png  in-context vignette (proofing box + phone tile)
  data/design/assets/*.svg                      text-free illustrations used by the design canvas

PNG rendering uses headless Google Chrome when present (full SVG filter support), else
cairosvg (`uv run --with cairosvg python data/design/make_mockup.py`).

All geometry of the module is in millimetres (120 x 80 x 35 mm), scaled per view.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE / "assets"
FONT = "Avenir Next, Figtree, Helvetica Neue, Arial, sans-serif"

C = {
    "page": "#F7F3EC", "card": "#FCFAF6", "ink": "#1F1B16", "ink2": "#5B5248", "ink3": "#7C7165",
    "rule": "#E6DDCF", "lead": "#8C8174",
    "walnut": "#6E4428", "char": "#2B2825", "steel": "#B9BBBB",
    "A": "#1f77b4", "B": "#ff7f0e",
}
W_MM, H_MM, R_MM, CAP_MM = 120, 80, 18, 13          # body footprint, corner radius, walnut cap
DX, DY = 13.0, -9.5                                  # oblique depth vector for 35 mm
READ = {"A": ("22.0", "pH 4.4", "rise 1.4×"), "B": ("27.0", "pH 3.9", "rise 1.8×")}


# --------------------------------------------------------------------------- defs helper
class Defs:
    def __init__(self, prefix: str):
        self.p, self.items, self.seen = prefix, [], set()

    def _add(self, key, xml):
        if key not in self.seen:
            self.seen.add(key)
            self.items.append(xml)
        return f"url(#{key})"

    @staticmethod
    def _stops(stops):
        out = []
        for s in stops:
            o, c, a = (s + (1,))[:3]
            op = "" if a == 1 else f' stop-opacity="{a}"'
            out.append(f'<stop offset="{o}" stop-color="{c}"{op}/>')
        return "".join(out)

    def lin(self, name, stops, x1=0, y1=0, x2=0, y2=1, user=False):
        k = f"{self.p}-{name}"
        u = ' gradientUnits="userSpaceOnUse"' if user else ""
        return self._add(k, f'<linearGradient id="{k}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{u}>'
                            f"{self._stops(stops)}</linearGradient>")

    def rad(self, name, stops, cx=0.5, cy=0.5, r=0.5, fx=None, fy=None, user=False):
        k = f"{self.p}-{name}"
        u = ' gradientUnits="userSpaceOnUse"' if user else ""
        f = f' fx="{fx}" fy="{fy}"' if fx is not None else ""
        return self._add(k, f'<radialGradient id="{k}" cx="{cx}" cy="{cy}" r="{r}"{f}{u}>'
                            f"{self._stops(stops)}</radialGradient>")

    def blur(self, sd):
        k = f"{self.p}-blur{str(sd).replace('.', '_')}"
        self._add(k, f'<filter id="{k}" x="-60%" y="-60%" width="220%" height="220%">'
                     f'<feGaussianBlur stdDeviation="{sd}"/></filter>')
        return f"url(#{k})"

    def clip(self, name, inner):
        k = f"{self.p}-{name}"
        self._add(k, f'<clipPath id="{k}">{inner}</clipPath>')
        return f"url(#{k})"

    def xml(self):
        return "<defs>" + "".join(self.items) + "</defs>"


def rrect(x, y, w, h, r, **a):
    attrs = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in a.items())
    return f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{r}" {attrs}/>'


def text(x, y, s, size, fill=C["ink"], weight=400, anchor="start", ls=0, extra=""):
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" letter-spacing="{ls}" {extra}>{s}</text>')


# --------------------------------------------------------------------------- module parts (mm)
def wood_grain(d: Defs, x0, y0, w, h, seed=1):
    """Soft, deterministic walnut grain lines inside a box."""
    out = []
    for i in range(9):
        y = y0 + h * (i + 0.5) / 9
        amp = 0.35 + 0.25 * math.sin(seed * 3.1 + i * 1.7)
        pts = []
        for k in range(25):
            x = x0 + w * k / 24
            yy = y + amp * math.sin(k * 0.55 + i * 2.3 + seed) + 0.25 * math.sin(k * 1.9 + i)
            pts.append(f"{x:.2f},{yy:.2f}")
        col, op = ("#3A2112", 0.28) if i % 2 else ("#B98557", 0.22)
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" '
                   f'stroke-width="{0.16 + 0.08 * (i % 3)}" opacity="{op}"/>')
    return "".join(out)


def camera_eye(d: Defs, cx, cy, ch_col, glow=True):
    o = []
    if glow:
        o.append(f'<circle cx="{cx}" cy="{cy}" r="23" fill="{d.rad("ringglow", [(0, ch_col, 0.0), (0.55, ch_col, 0.0), (0.66, ch_col, 0.22), (1, ch_col, 0)])}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy + 0.5}" r="15.6" fill="#000" opacity="0.18" filter="{d.blur(0.6)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="15.2" fill="{d.lin("bezel", [(0, "#4D4740"), (1, "#211E1B")])}"/>')
    # status ring (chamber colour) + bloom
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="{ch_col}" stroke-width="2.0" opacity="0.85" filter="{d.blur(0.7)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="{ch_col}" stroke-width="1.05"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="#FFFFFF" stroke-width="0.3" opacity="0.55"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="13.1" fill="#26231F"/>')
    # frosted ring light
    o.append(f'<circle cx="{cx}" cy="{cy}" r="12.3" fill="{d.rad("frost", [(0, "#FFFFFF"), (0.62, "#FBF8F1"), (0.85, "#EDE7DB"), (1, "#D9D1C3")], cx=0.42, cy=0.38, r=0.62)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="12.3" fill="none" stroke="#FFFFFF" stroke-width="0.35" opacity="0.8"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="8.2" fill="#1B1A19"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="8.2" fill="none" stroke="#000" stroke-width="0.4" opacity="0.35"/>')
    # lens
    o.append(f'<circle cx="{cx}" cy="{cy}" r="6.4" fill="{d.rad("glass", [(0, "#3B4A5A"), (0.45, "#18202A"), (1, "#07090B")], cx=0.45, cy=0.42, r=0.6)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="3.9" fill="none" stroke="#4E6C86" stroke-width="0.55" opacity="0.75"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="2.1" fill="#040506"/>')
    o.append(f'<ellipse cx="{cx - 2.3}" cy="{cy - 2.6}" rx="1.7" ry="0.95" transform="rotate(-38 {cx - 2.3} {cy - 2.6})" fill="#FFFFFF" opacity="0.78"/>')
    o.append(f'<circle cx="{cx + 2.4}" cy="{cy + 2.3}" r="0.55" fill="#FFFFFF" opacity="0.35"/>')
    return "".join(o)


def eink(d: Defs, x, y, ch):
    temp, ph, rise = READ[ch]
    o = [rrect(x, y + 0.5, 57, 23, 3.4, fill="#000", opacity="0.16", filter=d.blur(0.5)),
         rrect(x, y, 57, 23, 3.4, fill=d.lin("dbez", [(0, "#4A443E"), (1, "#25221F")])),
         rrect(x + 1.7, y + 1.7, 53.6, 19.6, 1.6, fill=d.lin("paper", [(0, "#EDEAE2"), (1, "#DEDAD0")])),
         rrect(x + 1.7, y + 1.7, 53.6, 3, 1.6, fill=d.lin("pshade", [(0, "#000", 0.13), (1, "#000", 0)])),
         text(x + 4.0, y + 14.6, temp, 10.5, "#24211F", 500, ls=-0.3),
         text(x + 25.6, y + 9.3, "°C", 3.8, "#24211F", 500),
         text(x + 4.1, y + 19.4, ph, 3.1, "#3A3632", 600, ls=0.1),
         text(x + 35.0, y + 7.0, f"CHAMBER {ch}", 2.25, "#4A4540", 700, ls=0.32)]
    # rise sparkline (logistic)
    pts = []
    for k in range(31):
        t = k / 30
        v = 1 / (1 + math.exp(-9 * (t - 0.45)))
        pts.append(f"{x + 35 + 17.5 * t:.2f},{y + 14.6 - 5.4 * v:.2f}")
    o.append(f'<line x1="{x + 35}" y1="{y + 14.8}" x2="{x + 52.5}" y2="{y + 14.8}" stroke="#3A3632" stroke-width="0.22" stroke-dasharray="0.6 0.6" opacity="0.6"/>')
    o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#24211F" stroke-width="0.55" stroke-linecap="round"/>')
    o.append(f'<circle cx="{x + 52.5}" cy="{y + 9.3}" r="0.75" fill="#24211F"/>')
    o.append(text(x + 35.0, y + 19.4, rise, 3.1, "#3A3632", 600, ls=0.1))
    return "".join(o)


def grille(d: Defs, x0, y0, cols=19, rows=4, pitch=2.8):
    o = []
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * pitch + (pitch / 2 if r % 2 else 0), y0 + r * pitch
            o.append(f'<circle cx="{x:.2f}" cy="{y + 0.22:.2f}" r="0.62" fill="#FFFFFF" opacity="0.7"/>')
            o.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.62" fill="#8E7E68"/>')
    return "".join(o)


def front_face(d: Defs, ch):
    col = C[ch]
    cl = d.clip("faceclip", rrect(0.9, 0.9, W_MM - 1.8, H_MM - 1.8, R_MM - 0.9))
    o = [rrect(0, 0, W_MM, H_MM, R_MM, fill=d.lin("edge", [(0, "#FAF4EA"), (0.5, "#E4D8C4"), (1, "#C9B89C")])),
         rrect(0.9, 0.9, W_MM - 1.8, H_MM - 1.8, R_MM - 0.9,
               fill=d.rad("face", [(0, "#FCF8F1"), (0.55, "#F0E7D7"), (1, "#E0D3BC")], cx=0.28, cy=0.18, r=0.95)),
         f'<g clip-path="{cl}">',
         rrect(0, 0, W_MM, CAP_MM, 0, fill=d.lin("wal", [(0, "#9A673F"), (0.55, "#774A2B"), (1, "#5B3720")])),
         wood_grain(d, 0, 0.6, W_MM, CAP_MM - 1.2),
         rrect(0, 0, W_MM, 1.6, 0, fill="#FFFFFF", opacity="0.10"),
         f'<line x1="0" y1="{CAP_MM}" x2="{W_MM}" y2="{CAP_MM}" stroke="#2E1A0E" stroke-width="0.45" opacity="0.65"/>',
         f'<line x1="0" y1="{CAP_MM + 0.45}" x2="{W_MM}" y2="{CAP_MM + 0.45}" stroke="#FFFFFF" stroke-width="0.35" opacity="0.7"/>',
         "</g>",
         camera_eye(d, 30, 46.5, col),
         eink(d, 53.5, 21.5, ch),
         grille(d, 56.2, 51.5)]
    return "".join(o)


def hat(d: Defs, ch, motion=True, puck_ry=2.6, k=1.18):
    return f'<g transform="scale({k})">{_hat(d, ch, motion, puck_ry)}</g>'


def _hat(d: Defs, ch, motion, puck_ry):
    col = C[ch]
    hg = d.rad("hat", [(0, "#FFFFFF"), (0.5, "#F6F1E8"), (1, "#D8CDBB")], cx=-3.5, cy=-22, r=17, user=True)
    o = [f'<ellipse cx="0" cy="0.4" rx="9.2" ry="{puck_ry + 0.4}" fill="#1B1917"/>',
         f'<ellipse cx="0" cy="-0.9" rx="8.6" ry="{puck_ry}" fill="{d.lin("puck", [(0, "#55504A"), (1, "#2A2724")])}"/>',
         f'<rect x="-6.6" y="-11" width="13.2" height="10" fill="{d.lin("band", [(0, "#DCD2C2"), (0.3, "#FFFDF8"), (0.75, "#E6DDCF"), (1, "#C9BDA9")], 0, 0, 1, 0)}"/>',
         f'<ellipse cx="0" cy="-1" rx="6.6" ry="{max(puck_ry * 0.7, 0.5)}" fill="{d.lin("band2", [(0, "#DCD2C2"), (0.3, "#FFFDF8"), (0.75, "#E6DDCF"), (1, "#C9BDA9")], 0, 0, 1, 0)}"/>']
    for x in (-3.6, 0, 3.6):
        o.append(f'<line x1="{x}" y1="-10.5" x2="{x}" y2="-1.5" stroke="#C7BBA7" stroke-width="0.3" opacity="0.7"/>')
    o.append(f'<ellipse cx="0" cy="-11" rx="7.3" ry="1.9" fill="#E3DACB"/>')
    for (x, y, r) in ((-6.0, -14.8, 5.4), (6.0, -14.8, 5.4), (-3.2, -19.6, 5.6), (3.4, -19.9, 5.6), (0, -15.6, 6.2)):
        o.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{hg}"/>')
    o.append('<path d="M -2.4 -12.2 Q -1.2 -16.5 -2.6 -20.3 M 2.6 -12.2 Q 1.4 -16.7 2.8 -20.6 M -6 -20 Q 0 -18.2 6.4 -20.3" '
             'fill="none" stroke="#CFC4B2" stroke-width="0.35" opacity="0.75" stroke-linecap="round"/>')
    if motion:
        for s in (-1, 1):
            o.append(f'<path d="M {s * 12.5} -22 Q {s * 14.8} -17 {s * 12.5} -12" fill="none" stroke="{col}" stroke-width="0.7" stroke-linecap="round" opacity="0.85"/>')
            o.append(f'<path d="M {s * 15.6} -23.5 Q {s * 18.6} -17 {s * 15.6} -10.5" fill="none" stroke="{col}" stroke-width="0.55" stroke-linecap="round" opacity="0.45"/>')
    return "".join(o)


def cable(d: Defs, path, w, col, hi="#FFFFFF", hi_op=0.28, shadow=True):
    o = []
    if shadow:
        o.append(f'<path d="{path}" transform="translate(2.6 3.4)" fill="none" stroke="#3A2A1A" stroke-width="{w}" '
                 f'stroke-linecap="round" opacity="0.18" filter="{d.blur(1.6)}"/>')
    o.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{w}" stroke-linecap="round"/>')
    o.append(f'<path d="{path}" transform="translate(-{w * 0.18:.2f} -{w * 0.12:.2f})" fill="none" stroke="{hi}" '
             f'stroke-width="{w * 0.28:.2f}" stroke-linecap="round" opacity="{hi_op}"/>')
    return "".join(o)


def probe(d: Defs, x, y, ang, ch, needle=36):
    """Starter probe: strain relief, cream food-safe silicone sleeve, stainless needle. Local +x = tip."""
    col = C[ch]
    st = d.lin("steel", [(0, "#F4F4F1"), (0.35, "#C9CBCA"), (0.7, "#8C9090"), (1, "#C2C4C3")])
    o = [f'<g transform="translate({x} {y}) rotate({ang})">',
         f'<g transform="translate(2.4 3.2)" opacity="0.2" filter="{d.blur(1.4)}"><rect x="0" y="-2.9" width="{17 + needle}" height="5.8" rx="2.8" fill="#3A2A1A"/></g>',
         '<path d="M 0 -1.3 L 4 -2.4 L 4 2.4 L 0 1.3 Z" fill="#2B2825"/>',
         rrect(3.6, -2.9, 14.5, 5.8, 2.8, fill=d.lin("sleeve", [(0, "#FFFCF5"), (0.45, "#EFE6D6"), (1, "#C9BBA2")])),
         rrect(6.0, -2.95, 1.6, 5.9, 0.4, fill=col),
         f'<line x1="11" y1="-2.4" x2="11" y2="2.4" stroke="#CDBFA8" stroke-width="0.25"/>',
         f'<line x1="13" y1="-2.4" x2="13" y2="2.4" stroke="#CDBFA8" stroke-width="0.25"/>',
         f'<path d="M 18.1 -1.55 L {18.1 + needle - 3} -1.2 L {18.1 + needle} 0 L {18.1 + needle - 3} 1.2 L 18.1 1.55 Z" fill="{st}"/>',
         f'<line x1="18.4" y1="-0.75" x2="{18.1 + needle - 3}" y2="-0.6" stroke="#FFFFFF" stroke-width="0.35" opacity="0.7"/>',
         "</g>"]
    return "".join(o)


def bnc_side_plug(d: Defs, x, y):
    """BNC socket on the side wall, seen obliquely, with the pH cable plug attached (+x)."""
    m = d.lin("bncm", [(0, "#F1F1EE"), (0.4, "#BFC1C0"), (0.75, "#7E8282"), (1, "#A9ABAA")])
    o = [f'<ellipse cx="{x}" cy="{y}" rx="2.3" ry="5.0" fill="#6F6C66"/>',
         f'<ellipse cx="{x + 0.3}" cy="{y}" rx="1.9" ry="4.3" fill="{m}"/>',
         rrect(x + 0.4, y - 3.6, 6.4, 7.2, 0.6, fill=m),
         f'<ellipse cx="{x + 6.8}" cy="{y}" rx="1.4" ry="3.6" fill="#9A9C9B"/>',
         f'<path d="M {x + 6.8} {y - 3.4} L {x + 13.5} {y - 1.7} L {x + 13.5} {y + 1.7} L {x + 6.8} {y + 3.4} Z" fill="#2B2825"/>',
         f'<line x1="{x + 2.3}" y1="{y - 3.6}" x2="{x + 2.3}" y2="{y + 3.6}" stroke="#6E7272" stroke-width="0.3"/>',
         f'<line x1="{x + 4.3}" y1="{y - 3.6}" x2="{x + 4.3}" y2="{y + 3.6}" stroke="#6E7272" stroke-width="0.3"/>']
    return "".join(o)


def module_hero(d: Defs, ch, with_cables=True):
    """Oblique module in mm: front face at (0,0)-(120,80), back face offset (DX, DY)."""
    o = []
    # wall shadows (back face is on the wall)
    o.append(rrect(DX + 6, DY + 10, W_MM, H_MM, R_MM, fill="#4A3826", opacity="0.30", filter=d.blur(6.5)))
    o.append(rrect(DX + 1.6, DY + 2.6, W_MM, H_MM, R_MM, fill="#3A2A1A", opacity="0.30", filter=d.blur(1.8)))
    if with_cables:
        o.append(cable(d, "M 120 46 C 150 46 158 60 156 95 S 150 150 152 190", 2.4, "#2B2825"))
        o.append(cable(d, "M 72 76 C 72 108 86 128 100 190", 3.4, "#2B2825"))
        o.append(cable(d, "M 101 76 C 101 104 114 126 124 190", 2.3, "#EFE7DA", hi="#FFFFFF", hi_op=0.6))
        o.append(cable(d, "M 16 76 C 16 98 8 102 -1 107", 2.0, "#2B2825"))
        # plug boots under the bottom edge
        o.append(rrect(68.6, 78, 6.8, 9, 1.4, fill=d.lin("boot", [(0, "#45403A"), (1, "#22201D")], 0, 0, 1, 0)))
        o.append(rrect(98.4, 78, 5.2, 7.5, 1.4, fill=d.lin("bootc", [(0, "#FBF6EE"), (1, "#D3C6B0")], 0, 0, 1, 0)))
        o.append(rrect(14.0, 78, 4.0, 6.0, 1.2, fill=d.lin("boot", [(0, "#45403A"), (1, "#22201D")], 0, 0, 1, 0)))
        o.append(probe(d, -1.5, 107.5, 124, ch))
    # extrusion
    side = d.lin("side", [(0, "#A06C43"), (0.12, "#6E4428"), (0.163, "#5C381F"), (0.166, "#E7DCC8"),
                          (0.5, "#D2C3A9"), (1, "#B2A084")])
    n = 56
    for i in range(n, 0, -1):
        t = i / n
        o.append(rrect(DX * t, DY * t, W_MM, H_MM, R_MM, fill=side))
    # top-face sheen on walnut
    o.append(f'<path d="M {R_MM} 0 L {R_MM + DX} {DY} L {W_MM - R_MM + DX} {DY} L {W_MM - R_MM} 0 Z" fill="#FFFFFF" opacity="0.07"/>')
    if with_cables:
        o.append(bnc_side_plug(d, 126.6, 44.5))
    o.append(front_face(d, ch))
    # chef hat on the walnut top face
    hx, hy = 92 + DX * 0.5, DY * 0.5 + 0.6
    o.append(f'<ellipse cx="{hx + 6}" cy="{hy + 0.6}" rx="11" ry="2.6" fill="#1E120A" opacity="0.4" filter="{d.blur(1.2)}"/>')
    o.append(f'<g transform="translate({hx} {hy})">{hat(d, ch)}</g>')
    return "".join(o)


# --------------------------------------------------------------------------- orthographic views (mm)
def module_front_ortho(d: Defs, ch):
    o = [rrect(2, 6, W_MM, H_MM, R_MM, fill="#4A3826", opacity="0.22", filter=d.blur(4)),
         front_face(d, ch),
         f'<g transform="translate(90 0.2)">{hat(d, ch, motion=False, puck_ry=1.1)}</g>']
    return "".join(o)


def module_bottom(d: Defs):
    """Underside, back edge at top (y=0), front edge at bottom (y=35)."""
    jack = lambda x, r: (f'<circle cx="{x}" cy="17.5" r="{r + 0.8}" fill="#2B2825"/>'
                         f'<circle cx="{x}" cy="17.5" r="{r}" fill="{d.lin("jm", [(0, "#E6E7E4"), (1, "#7E8282")])}"/>'
                         f'<circle cx="{x}" cy="17.5" r="{r * 0.72}" fill="#0E0F10"/>')
    eng = lambda x, s: text(x, 27.2, s, 2.3, "#9E8D74", 700, "middle", 0.25)
    o = [rrect(1.5, 3, W_MM, 35, 11, fill="#4A3826", opacity="0.2", filter=d.blur(2.5)),
         rrect(0, 0, W_MM, 35, 11, fill=d.lin("under", [(0, "#D9CCB5"), (0.5, "#E9DFCD"), (1, "#F3EBDD")])),
         rrect(10, 0, W_MM - 20, 3.0, 1.2, fill="#2E2B27", opacity="0.85"),
         rrect(0.6, 0.6, W_MM - 1.2, 33.8, 10.4, fill="none", stroke="#FFFFFF", stroke_width="0.35", opacity="0.6"),
         jack(14, 2.2), eng(14, "T1"), jack(28, 2.2), eng(28, "T2"), jack(42, 1.7), eng(42, "DOOR"),
         rrect(61, 14.3, 18, 6.4, 1.2, fill="#2B2825"), rrect(62, 15.3, 16, 4.4, 0.8, fill="#121212"),
         rrect(66, 12.6, 8, 1.9, 0.6, fill="#2B2825")]
    for k in range(6):
        o.append(rrect(63.2 + k * 2.55, 16.6, 1.0, 1.8, 0.2, fill="#C9A85A"))
    o.append(eng(70, "PELTIER · FAN"))
    o.append(rrect(95.4, 15.8, 9.2, 3.4, 1.7, fill="#1A1A1C", stroke="#A9ABAA", stroke_width="0.5"))
    o.append(rrect(97.5, 17.1, 5.0, 0.8, 0.3, fill="#4A4A4A"))
    o.append(eng(100, "USB-C"))
    return "".join(o)


def module_side(d: Defs):
    """Right side: front face at x=0, wall/back at x=35."""
    shape = "M 9 0 L 31 0 Q 35 0 35 4 L 35 76 Q 35 80 31 80 L 9 80 Q 0 80 0 71 L 0 9 Q 0 0 9 0 Z"
    cl = d.clip("sideclip", f'<path d="{shape}"/>')
    m = d.rad("bncf", [(0, "#F4F4F1"), (0.6, "#BFC1C0"), (1, "#808484")], cx=0.4, cy=0.35, r=0.7)
    o = [f'<path d="{shape}" transform="translate(2 4)" fill="#4A3826" opacity="0.22" filter="{d.blur(3)}"/>',
         f'<path d="{shape}" fill="{d.lin("sidev", [(0, "#F1E8D8"), (1, "#D3C4AA")], 0, 0, 1, 0)}"/>',
         f'<g clip-path="{cl}">',
         rrect(0, 0, 35, CAP_MM, 0, fill=d.lin("wal", [(0, "#9A673F"), (0.55, "#774A2B"), (1, "#5B3720")])),
         wood_grain(d, 0, 0.6, 35, CAP_MM - 1.2, seed=4),
         f'<line x1="0" y1="{CAP_MM}" x2="35" y2="{CAP_MM}" stroke="#2E1A0E" stroke-width="0.45" opacity="0.65"/>',
         rrect(31.6, 0, 3.4, 80, 0, fill="#2E2B27"),
         "</g>",
         f'<circle cx="16" cy="44" r="5.4" fill="#000" opacity="0.18" filter="{d.blur(0.6)}"/>',
         f'<circle cx="16" cy="43.6" r="5.3" fill="{m}"/>',
         f'<circle cx="16" cy="43.6" r="4.0" fill="#C6C8C7" stroke="#8C9090" stroke-width="0.3"/>',
         f'<circle cx="16" cy="43.6" r="2.7" fill="#141516"/>',
         f'<circle cx="16" cy="43.6" r="0.7" fill="#D9B45A"/>',
         rrect(10.2, 43.1, 1.4, 1.0, 0.3, fill="#8C9090"), rrect(20.4, 43.1, 1.4, 1.0, 0.3, fill="#8C9090"),
         # tethered shorting / calibration cap
         '<path d="M 16 49 C 13 54 13 58 16 61.5" fill="none" stroke="#D8CCB7" stroke-width="0.9" stroke-linecap="round"/>',
         f'<circle cx="16" cy="66.2" r="4.9" fill="#000" opacity="0.15" filter="{d.blur(0.6)}"/>',
         f'<circle cx="16" cy="65.8" r="4.7" fill="{d.rad("capg", [(0, "#FFFCF5"), (1, "#D5C7AF")], cx=0.4, cy=0.35, r=0.7)}"/>',
         f'<circle cx="16" cy="65.8" r="3.2" fill="none" stroke="#CDBFA8" stroke-width="0.35"/>',
         text(16, 66.7, "0 mV", 1.9, "#8F7F67", 700, "middle", 0.1)]
    return "".join(o)


def module_back(d: Defs):
    o = [rrect(1.5, 4, W_MM, H_MM, R_MM, fill="#4A3826", opacity="0.2", filter=d.blur(3)),
         rrect(0, 0, W_MM, H_MM, R_MM, fill=d.lin("backrim", [(0, "#EFE6D6"), (1, "#D4C5AC")])),
         rrect(7, 7, W_MM - 14, H_MM - 14, R_MM - 7, fill=d.lin("plate", [(0, "#46413B"), (1, "#26231F")]))]
    for (x, y) in ((22, 22), (98, 22), (22, 58), (98, 58)):
        o.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{d.rad("mag", [(0, "#8A857D"), (1, "#3A3631")], cx=0.4, cy=0.35, r=0.7)}"/>')
        o.append(f'<circle cx="{x}" cy="{y}" r="6" fill="none" stroke="#1A1816" stroke-width="0.5"/>')
    for x in (48, 72):
        o.append(f'<path d="M {x} 30 L {x} 40" stroke="#121110" stroke-width="2.4" stroke-linecap="round"/>')
        o.append(f'<circle cx="{x}" cy="42" r="2.8" fill="#121110"/>')
    o.append(rrect(46, 54, 28, 7, 3.5, fill="#1E1C19"))
    return "".join(o)


# --------------------------------------------------------------------------- hero panel (px)
HERO_W, HERO_H = 1480, 1500
HERO_S, HERO_OX, HERO_OY = 6.1, 300, 450


def hero_body(d: Defs, ch, bg=True):
    o = []
    if bg:
        o.append(rrect(0, 0, HERO_W, HERO_H, 36, fill=d.lin("wall", [(0, "#EFE8DC"), (1, "#E2D8C7")])))
        o.append(rrect(0, 0, HERO_W, HERO_H, 36, fill=d.rad("wallglow", [(0, "#FFFFFF", 0.55), (1, "#FFFFFF", 0)], cx=0.3, cy=0.25, r=0.7)))
    cl = d.clip("herocard", rrect(0, 0, HERO_W, HERO_H, 36))
    o.append(f'<g clip-path="{cl}"><g transform="translate({HERO_OX} {HERO_OY}) scale({HERO_S})">{module_hero(d, ch)}</g></g>')
    return "".join(o)


def svg_doc(w, h, defs: Defs, body, bg=None):
    b = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f"{defs.xml()}{b}{body}</svg>")


# --------------------------------------------------------------------------- callout panel (px)
CAL_W, CAL_H = 1480, 1500
FS, FX, FY = 5.4, 416, 262                 # front view scale/origin
BS, BX, BY = 5.4, 40, 830                  # bottom view
SS, SX, SY = 5.4, 860, 830                 # side view
KS, KX, KY = 2.3, 1150, 1214              # back view

LEFT_LABELS = [   # title, sub lines, target (mm on front face), leader y (px)
    ("Walnut cap · Wi-Fi window", ["ESP32-S3 and its 2.4 GHz antenna", "sit behind wood, not metal"], (12, 6.5), 236),
    ("Status ring", ["chamber colour; breathes while heating,", "pulses when the door opens"], (19.9, 36.4), 400),
    ("Camera · OV2640", ["watches the jar through its wall", "and tracks the rise"], (30, 46.5), 520),
    ("Diffused ring light", ["fixed white light, so exposure", "never drifts between frames"], (22.8, 53.7), 650),
]
RIGHT_LABELS = [
    ("Chef-hat automaton", ["N20 gear motor; bobs when its agent", "starts a new experiment"], (101.5, -15), 140),
    ("E-ink strip", ["temperature, pH and rise,", "readable with the light off"], (110.5, 30), 360),
    ("Air vent · BME680", ["air temperature, humidity,", "pressure, VOC gas resistance"], (107.5, 56), 560),
]
PORT_LEGEND = [
    ("T1", "Starter probe · DS18B20", "stainless, food-safe silicone sleeve, 1 m"),
    ("T2", "Aux probe · DS18B20", "heatsink, second jar or ambient air"),
    ("DOOR", "Door sensor", "reed pod + magnet tag, sticks to any door"),
    ("PELTIER", "Peltier / fan port", "drives an external BTS7960 pod, 2 fans, cut-off loop"),
    ("USB-C", "Power + service", "USB-C PD, 20 W"),
]
LEG_X, LEG_Y, LEG_DY = 40, 1088, 66
SIDE_LABELS = [
    ("pH port · BNC", ["any lab electrode, isolated", "interface board inside"], (16 + 5.3, 43.6), 975),
    ("Shorting cap", ["tethered BNC cap: 0 mV", "check before calibration"], (16 + 4.7, 65.8), 1085),
]
BACK_LABEL = ("Magnetic + screw mount", ["4 magnets, 2 keyholes, or VHB tape"])


def fpx(x, y):
    return FX + FS * x, FY + FS * y


def callout_body(d: Defs, ch, labels=True):
    o = [rrect(0, 0, CAL_W, CAL_H, 36, fill=C["card"])]
    # section captions (only drawn as text in the composite)
    o.append(f'<g transform="translate({FX} {FY}) scale({FS})">{module_front_ortho(d, ch)}</g>')
    o.append(f'<g transform="translate({BX} {BY}) scale({BS})">{module_bottom(d)}</g>')
    o.append(f'<g transform="translate({SX} {SY}) scale({SS})">{module_side(d)}</g>')
    o.append(f'<g transform="translate({KX} {KY}) scale({KS})">{module_back(d)}</g>')
    # leaders
    lead = lambda pts: (f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" '
                        f'stroke="{C["lead"]}" stroke-width="1.6" stroke-linejoin="round"/>')
    dot = lambda x, y: (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6.5" fill="#FFFFFF" opacity="0.9"/>'
                        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{C["ink"]}"/>')
    for _, _, (tx, ty), ly in LEFT_LABELS:
        px, py = fpx(tx, ty)
        o.append(lead([(372, ly), (396, ly), (px, py)]) + dot(px, py))
    for _, _, (tx, ty), ly in RIGHT_LABELS:
        px, py = fpx(tx, ty)
        o.append(lead([(1108, ly), (1084, ly), (px, py)]) + dot(px, py))
    for _, _, (tx, ty), ly in SIDE_LABELS:
        px, py = SX + SS * tx, SY + SS * ty
        o.append(lead([(1108, ly), (1084, ly), (px, py)]) + dot(px, py))
    if labels:
        o.append(callout_text(ch))
    return "".join(o)


def label_block(x, ly, title, subs, anchor):
    o = [text(x, ly + 8, title, 25, C["ink"], 600, anchor)]
    for i, s in enumerate(subs):
        o.append(text(x, ly + 36 + 25 * i, s, 19.5, C["ink2"], 400, anchor))
    return "".join(o)


def callout_text(ch):
    cap = lambda x, y, s: text(x, y, s, 17, C["ink3"], 700, "start", 2.2)
    o = [text(40, 64, "Every sensor, one module", 34, C["ink"], 600),
         text(40, 100, "front, underside, right side and back · 1 : 1 proportions", 20, C["ink3"]),
         cap(FX, 168, "FRONT"), cap(BX, BY - 26, "UNDERSIDE · PORTS"), cap(SX, SY - 26, "RIGHT SIDE"),
         cap(KX, KY - 26, "BACK")]
    for t, s, _, ly in LEFT_LABELS:
        o.append(label_block(360, ly - 12, t, s, "end"))
    for t, s, _, ly in RIGHT_LABELS:
        o.append(label_block(1120, ly - 12, t, s, "start"))
    for t, s, _, ly in SIDE_LABELS:
        o.append(label_block(1120, ly - 12, t, s, "start"))
    bt, bs = BACK_LABEL
    o.append(label_block(KX, KY + 80 * KS + 44, bt, bs, "start"))
    for i, (tag, t, s) in enumerate(PORT_LEGEND):
        y = LEG_Y + i * LEG_DY
        o.append(rrect(LEG_X, y - 21, 104, 30, 8, fill=C["char"]))
        o.append(text(LEG_X + 52, y, tag, 15, "#F4EEE4", 700, "middle", 1.4))
        o.append(text(LEG_X + 124, y, t, 23, C["ink"], 600))
        o.append(text(LEG_X + 124, y + 26, s, 19, C["ink2"]))
    return "".join(o)


# --------------------------------------------------------------------------- composite
COMP_W, COMP_H = 3200, 2160
SPECS = [("120 × 80 × 35 mm", "SIZE"), ("IP54", "SPLASH-RESISTANT"), ("5–40 °C", "OPERATING RANGE"),
         ("Wi-Fi 2.4 GHz", "ESP32-S3"), ("USB-C 20 W", "POWER"), ("1 m", "PROBE CABLES")]
MATERIALS = [("Matte cream PC", "mat_cream"), ("Oiled walnut", "mat_wal"), ("Charcoal + smoked glass", "mat_char")]


def materials_row(d: Defs, x, y):
    grads = {"mat_cream": [(0, "#FCF8F1"), (1, "#D9CBB2")], "mat_wal": [(0, "#A06C43"), (1, "#5B3720")],
             "mat_char": [(0, "#5A544D"), (1, "#1F1D1A")]}
    o, cx = [], x
    for label, key in MATERIALS:
        o.append(f'<circle cx="{cx + 16}" cy="{y + 2}" r="16" fill="#000" opacity="0.12" filter="{d.blur(2)}"/>')
        o.append(f'<circle cx="{cx + 16}" cy="{y}" r="16" fill="{d.rad(key, grads[key], cx=0.35, cy=0.3, r=0.8)}"/>')
        o.append(text(cx + 42, y + 7, label, 20, C["ink2"], 500))
        cx += 42 + len(label) * 10.2 + 40
    return "".join(o)


def composite(ch="B"):
    d = Defs("m")
    o = [text(80, 112, "FermentAI Node", 64, C["ink"], 600, ls=-0.5),
         text(82, 160, "A retrofit sensor + climate module for any proofing box, restaurant cabinet or bakery retarder",
              27, C["ink2"]),
         rrect(2722, 76, 398, 52, 26, fill="none", stroke="#B08A5E", stroke_width="2"),
         text(2921, 110, "CONCEPT MOCKUP", 20, "#8A6338", 700, "middle", 2.4),
         text(2536, 109, "RING COLOUR", 17, C["ink3"], 700, "end", 2), f'<circle cx="2560" cy="102" r="9" fill="{C["A"]}"/>', text(2576, 110, "A", 22, C["ink2"], 600),
         f'<circle cx="2628" cy="102" r="9" fill="{C["B"]}"/>', text(2644, 110, "B", 22, C["ink2"], 600),
         f'<g transform="translate(80 210)">{hero_body(d, ch)}'
         + text(48, 70, f"Mounted on the inner wall · chamber {ch}", 22, C["ink3"], 600, ls=0.3)
         + materials_row(d, 744, 62) + "</g>",
         f'<g transform="translate(1640 210)">{callout_body(d, ch)}</g>']
    # spec strip
    sy = 1760
    o.append(rrect(80, sy, 3040, 210, 30, fill=C["card"]))
    cw = 3040 / len(SPECS)
    for i, (v, lab) in enumerate(SPECS):
        cx = 80 + cw * i + cw / 2
        o.append(text(cx, sy + 102, v, 44, C["ink"], 600, "middle", -0.3))
        o.append(text(cx, sy + 146, lab, 17, C["ink3"], 700, "middle", 2.2))
        if i:
            o.append(f'<line x1="{80 + cw * i}" y1="{sy + 50}" x2="{80 + cw * i}" y2="{sy + 160}" stroke="{C["rule"]}" stroke-width="2"/>')
    o.append(text(80, 2050, "Concept rendering, not a shipping product. Today's prototype runs the same sensor set "
                  "(DS18B20, BME680, pH via interface board, reed switch, OV2640, BTS7960 Peltier, N20 hat) "
                  "on an ESP32-S3-CAM in a hand-built chamber.", 21, C["ink3"]))
    o.append(text(80, 2086, "Readings on the display are illustrative. See data/design/README.md for real-vs-concept.",
                  21, C["ink3"]))
    return svg_doc(COMP_W, COMP_H, d, "".join(o), bg=C["page"])


# --------------------------------------------------------------------------- context vignette
CTX_W, CTX_H = 1640, 1040


def proofing_box(d: Defs, ch, ox=110, oy=150):
    """Countertop proofing box with glass door; module on the back wall, jar on a wire shelf."""
    col = C[ch]
    bw, bh, br = 900, 760, 34
    bdx, bdy = 70, -48
    o = [rrect(ox + bdx * 0.4 + 20, oy + 40, bw, bh, br, fill="#4A3826", opacity="0.28", filter=d.blur(28)),
         rrect(ox - 10, oy + bh - 6, bw + bdx + 20, 26, 13, fill="#3A2A1A", opacity="0.25", filter=d.blur(10))]
    side = d.lin("boxside", [(0, "#F1EADF"), (1, "#C9BCA7")])
    for i in range(30, 0, -1):
        t = i / 30
        o.append(rrect(ox + bdx * t, oy + bdy * t, bw, bh, br, fill=side))
    o.append(f'<path d="M {ox + br} {oy} L {ox + br + bdx} {oy + bdy} L {ox + bw - br + bdx} {oy + bdy} L {ox + bw - br} {oy} Z" fill="#FFFFFF" opacity="0.35"/>')
    o.append(f'<path d="M {ox + bw} {oy + br} L {ox + bw + bdx} {oy + br + bdy} L {ox + bw + bdx} {oy + bh - br + bdy} L {ox + bw} {oy + bh - br} Z" fill="#3A2A1A" opacity="0.07"/>')
    o.append(rrect(ox, oy, bw, bh, br, fill=d.lin("boxfront", [(0, "#F8F3EA"), (1, "#E3D8C5")])))
    # interior
    ix, iy, iw, ih = ox + 60, oy + 60, bw - 120, bh - 150
    cl = d.clip("interior", rrect(ix, iy, iw, ih, 14))
    o.append(f'<g clip-path="{cl}">')
    o.append(rrect(ix, iy, iw, ih, 0, fill=d.lin("inner", [(0, "#D6CAB5"), (1, "#BDAE96")])))
    o.append(f'<path d="M {ix} {iy} L {ix + 70} {iy + 50} L {ix + iw - 70} {iy + 50} L {ix + iw} {iy} Z" fill="#000" opacity="0.07"/>')
    o.append(f'<path d="M {ix} {iy + ih} L {ix + 70} {iy + ih - 60} L {ix + iw - 70} {iy + ih - 60} L {ix + iw} {iy + ih} Z" fill="#FFFFFF" opacity="0.12"/>')
    o.append(f'<path d="M {ix} {iy} L {ix + 70} {iy + 50} L {ix + 70} {iy + ih - 60} L {ix} {iy + ih} Z" fill="#000" opacity="0.05"/>')
    o.append(f'<path d="M {ix + iw} {iy} L {ix + iw - 70} {iy + 50} L {ix + iw - 70} {iy + ih - 60} L {ix + iw} {iy + ih} Z" fill="#000" opacity="0.1"/>')
    # Peltier + fan on right inner wall
    px, py = ix + iw - 52, iy + 150
    o.append(rrect(px - 22, py, 44, 190, 6, fill="#8F9290"))
    for k in range(9):
        o.append(f'<line x1="{px - 20}" y1="{py + 12 + k * 20}" x2="{px + 20}" y2="{py + 12 + k * 20}" stroke="#5F6260" stroke-width="3"/>')
    # wire shelf + jar
    sy = iy + ih - 120
    for k in range(3):
        o.append(f'<line x1="{ix + 40}" y1="{sy + k * 9}" x2="{ix + iw - 40}" y2="{sy + k * 9}" stroke="#7D776F" stroke-width="2.4" opacity="0.8"/>')
    jx, jy, jw, jh = ix + 420, sy - 330, 220, 330
    o.append(f'<ellipse cx="{jx + jw / 2 + 14}" cy="{sy + 4}" rx="{jw / 2 + 14}" ry="12" fill="#2A1C10" opacity="0.3" filter="{d.blur(6)}"/>')
    o.append(rrect(jx, jy + 60, jw, jh - 60, 18, fill=d.lin("dough", [(0, "#F3E6CC"), (1, "#DCC49B")], 0, 0, 1, 0)))
    o.append(f'<path d="M {jx} {jy + 72} Q {jx + jw / 2} {jy + 36} {jx + jw} {jy + 72} Z" fill="#F6EBD4"/>')
    for (bx, by, r) in ((44, 170, 8), (110, 215, 6), (170, 140, 9), (74, 260, 7), (150, 280, 5), (36, 110, 5), (128, 105, 6), (186, 250, 6)):
        o.append(f'<circle cx="{jx + bx}" cy="{jy + by}" r="{r}" fill="#C9AE80" opacity="0.6"/>')
    o.append(rrect(jx - 3, jy + jh - 150, jw + 6, 8, 4, fill=col, opacity="0.9"))            # start-level band
    o.append(rrect(jx, jy, jw, jh, 18, fill=d.lin("jarglass", [(0, "#FFFFFF", 0.35), (0.2, "#FFFFFF", 0.05), (0.8, "#FFFFFF", 0.02), (1, "#FFFFFF", 0.25)], 0, 0, 1, 0), stroke="#FFFFFF", stroke_width="2.5", stroke_opacity="0.7"))
    o.append(rrect(jx + 14, jy + 14, 10, jh - 40, 5, fill="#FFFFFF", opacity="0.45"))
    o.append(rrect(jx - 6, jy - 12, jw + 12, 18, 6, fill="#E9E2D6", stroke="#CFC5B5", stroke_width="1.5"))
    # module on the back wall (front view, small) + probe cable into the jar
    ms = 2.3
    mx, my = ix + 120, iy + 110
    o.append(cable(d, f"M {mx + 16 * ms} {my + 78 * ms} C {mx + 16 * ms} {my + 200} {jx + 70} {jy - 80} {jx + 70} {jy + 40}", 4.5, "#2B2825"))
    o.append(rrect(jx + 66, jy - 30, 9, 190, 4, fill=d.lin("steelv", [(0, "#F4F4F1"), (0.5, "#B9BBBB"), (1, "#8C9090")], 0, 0, 1, 0)))
    o.append(rrect(jx + 62, jy - 70, 17, 46, 7, fill="#EFE6D6"))
    o.append(rrect(jx + 62, jy - 60, 17, 5, 1, fill=col))
    o.append(f'<g transform="translate({mx} {my}) scale({ms})">{module_front_ortho(d, ch)}</g>')
    o.append("</g>")
    # glass door + frame
    o.append(rrect(ix, iy, iw, ih, 14, fill="#BFD3DC", opacity="0.10"))
    o.append(f'<path d="M {ix + 90} {iy} L {ix + 260} {iy} L {ix + 40} {iy + ih} L {ix - 130} {iy + ih} Z" fill="#FFFFFF" opacity="0.16" clip-path="{cl}"/>')
    o.append(f'<path d="M {ix + 300} {iy} L {ix + 340} {iy} L {ix + 120} {iy + ih} L {ix + 80} {iy + ih} Z" fill="#FFFFFF" opacity="0.12" clip-path="{cl}"/>')
    o.append(rrect(ix - 8, iy - 8, iw + 16, ih + 16, 20, fill="none", stroke="#2E2B27", stroke_width="16"))
    o.append(rrect(ix + iw - 30, iy + ih / 2 - 70, 12, 140, 6, fill=d.lin("handle", [(0, "#8A5A36"), (1, "#5B3720")], 0, 0, 1, 0)))
    # control strip / feet
    o.append(rrect(ox + 60, oy + bh - 66, 220, 12, 6, fill="#D9CDB9"))
    o.append(f'<circle cx="{ox + bw - 90}" cy="{oy + bh - 60}" r="9" fill="{col}" opacity="0.9"/>')
    return "".join(o)


def phone(d: Defs, ch, x, y):
    col = C[ch]
    temp, ph, rise = READ[ch]
    w, h = 400, 800
    o = [rrect(x + 10, y + 26, w, h, 58, fill="#2A1C10", opacity="0.3", filter=d.blur(22)),
         rrect(x, y, w, h, 58, fill="#1F1D1B"),
         rrect(x + 14, y + 14, w - 28, h - 28, 46, fill=C["card"]),
         rrect(x + w / 2 - 50, y + 30, 100, 26, 13, fill="#1F1D1B"),
         f'<circle cx="{x + 52}" cy="{y + 112}" r="9" fill="{col}"/>',
         text(x + 72, y + 120, f"Chamber {ch}", 26, C["ink"], 600),
         text(x + w - 46, y + 120, "live", 18, C["ink3"], 600, "end", 1.2),
         text(x + 44, y + 228, temp.split(".")[0], 96, C["ink"], 500, ls=-2),
         text(x + 44 + 108, y + 186, "°C", 32, C["ink2"], 500),
         text(x + 44, y + 270, "holding setpoint", 20, C["ink3"]),
         f'<line x1="{x + 44}" y1="{y + 304}" x2="{x + w - 44}" y2="{y + 304}" stroke="{C["rule"]}" stroke-width="2"/>',
         text(x + 44, y + 360, ph.replace("pH ", ""), 50, C["ink"], 500),
         text(x + 44, y + 392, "pH", 18, C["ink3"], 700, ls=1.6),
         text(x + 214, y + 360, rise.replace("rise ", ""), 50, C["ink"], 500),
         text(x + 214, y + 392, "RISE", 18, C["ink3"], 700, ls=1.6)]
    # rise chart
    gx, gy, gw, gh = x + 44, y + 440, w - 88, 220
    o.append(rrect(gx - 14, gy - 16, gw + 28, gh + 56, 20, fill="#F3EDE3"))
    for k in range(4):
        yy = gy + gh * k / 3
        o.append(f'<line x1="{gx}" y1="{yy:.1f}" x2="{gx + gw}" y2="{yy:.1f}" stroke="#E2D8C8" stroke-width="1.5"/>')
    pts, area = [], [f"{gx},{gy + gh}"]
    for k in range(41):
        t = k / 40
        v = 1 / (1 + math.exp(-8 * (t - 0.42)))
        pts.append(f"{gx + gw * t:.1f},{gy + gh - gh * 0.85 * v:.1f}")
    area += pts + [f"{gx + gw},{gy + gh}"]
    o.append(f'<polygon points="{" ".join(area)}" fill="{col}" opacity="0.14"/>')
    o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="4.5" stroke-linecap="round"/>')
    o.append(f'<circle cx="{gx + gw}" cy="{float(pts[-1].split(",")[1]):.1f}" r="8" fill="{col}" stroke="#FFFFFF" stroke-width="3"/>')
    o.append(text(gx, gy + gh + 30, "0 h", 16, C["ink3"]))
    o.append(text(gx + gw, gy + gh + 30, "now", 16, C["ink3"], 400, "end"))
    o.append(text(x + 44, y + h - 70, f"Chamber {ch} · {temp.split('.')[0]} °C · {ph} · {rise}", 18, C["ink2"], 600))
    return "".join(o)


def context_doc(ch="B"):
    d = Defs("c")
    o = [rrect(0, 0, CTX_W, CTX_H, 36, fill=d.lin("ctxbg", [(0, "#F4EEE4"), (1, "#E8DFD0")])),
         proofing_box(d, ch),
         phone(d, ch, 1150, 170),
         text(70, 86, "In context · drops into the box you already have", 30, C["ink"], 600)]
    return svg_doc(CTX_W, CTX_H, d, "".join(o), bg=C["page"])


def context_asset(ch="B"):
    d = Defs("c")
    body = rrect(0, 0, 1100, CTX_H, 0, fill="none") + proofing_box(d, ch)
    return svg_doc(1100, CTX_H, d, body)


# --------------------------------------------------------------------------- rendering
def render_png(svg: pathlib.Path, png: pathlib.Path, w: int, h: int):
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(chrome):
        with tempfile.TemporaryDirectory() as prof:
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}",
                            f"--window-size={w},{h}", f"--screenshot={png}", "--default-background-color=FFFFFFFF",
                            svg.as_uri()], check=True, capture_output=True, timeout=120)
        return "chrome"
    import cairosvg  # noqa: PLC0415  (fallback; blur filters are ignored)
    cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=w, output_height=h)
    return "cairosvg"


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    outs = {
        HERE / "sensor_module_mockup.svg": (composite("B"), COMP_W, COMP_H),
        HERE / "sensor_module_context.svg": (context_doc("B"), CTX_W, CTX_H),
    }
    for ch in "AB":
        d = Defs(f"h{ch}")
        outs[ASSETS / f"hero_{ch}.svg"] = (svg_doc(HERO_W, HERO_H, d, hero_body(d, ch)), HERO_W, HERO_H)
        d = Defs(f"k{ch}")
        outs[ASSETS / f"callouts_{ch}.svg"] = (svg_doc(CAL_W, CAL_H, d, callout_body(d, ch, labels=False)), CAL_W, CAL_H)
    outs[ASSETS / "context_box_B.svg"] = (context_asset("B"), 1100, CTX_H)
    for p, (s, _, _) in outs.items():
        p.write_text(s)
    eng = render_png(HERE / "sensor_module_mockup.svg", HERE / "sensor_module_mockup.png", COMP_W, COMP_H)
    render_png(HERE / "sensor_module_context.svg", HERE / "sensor_module_context.png", CTX_W, CTX_H)
    if "--assets-png" in sys.argv:
        for p, (_, w, h) in outs.items():
            if p.parent == ASSETS:
                render_png(p, p.with_suffix(".png"), w, h)
    # label coordinates for the design canvas (px within the 1480 x 1440 callout board)
    lab = {"left": [(t, s, round(fpx(*tg)[1]), ly) for t, s, tg, ly in LEFT_LABELS],
           "right": [(t, s, ly) for t, s, _, ly in RIGHT_LABELS],
           "side": [(t, s, ly) for t, s, _, ly in SIDE_LABELS],
           "back_label_y": KY + 80 * KS + 44}
    (HERE / "assets" / "callout_labels.json").write_text(json.dumps(lab, indent=1, ensure_ascii=False))
    print(f"rendered with {eng}:", *[str(p.relative_to(HERE)) for p in outs], sep="\n  ")


if __name__ == "__main__":
    main()
