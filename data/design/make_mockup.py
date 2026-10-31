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

_CREAM = {
    "edge": [(0, "#FAF4EA"), (0.5, "#E4D8C4"), (1, "#C9B89C")],
    "face": [(0, "#FCF8F1"), (0.55, "#F0E7D7"), (1, "#E0D3BC")],
    "cap": [(0, "#9A673F"), (0.55, "#774A2B"), (1, "#5B3720")],
    "grain": [("#B98557", 0.22), ("#3A2112", 0.28)], "grain_amp": 1.0, "groove_hi": 0.7,
    "side": [(0, "#A06C43"), (0.12, "#6E4428"), (0.163, "#5C381F"), (0.166, "#E7DCC8"), (0.5, "#D2C3A9"), (1, "#B2A084")],
    "bezel": [(0, "#4D4740"), (1, "#211E1B")], "ring_inner": "#26231F",
    "glass": [(0, "#3B4A5A"), (0.45, "#18202A"), (1, "#07090B")], "iris": "#4E6C86",
    "dbez": [(0, "#4A443E"), (1, "#25221F")], "dbez_stroke": "none",
    "lip": ("#FFFFFF", 0.7), "hole": "#8E7E68",
    "frost": [(0, "#FFFFFF"), (0.62, "#FBF8F1"), (0.85, "#EDE7DB"), (1, "#D9D1C3")], "frost_rim": 0.8,
    "cable": "#2B2825", "usb": "#EFE7DA", "usb_hi": 0.6, "bootc": [(0, "#FBF6EE"), (1, "#D3C6B0")], "cab_sh": 0.18,
    "sleeve": [(0, "#FFFCF5"), (0.45, "#EFE6D6"), (1, "#C9BBA2")], "sleeve_line": "#CDBFA8", "sleeve_flat": "#EFE6D6",
    "sh1": 0.30, "sh1_blur": 6.5, "sh2": 0.30, "osh": 0.22, "osh2": 0.2,
    "under": [(0, "#D9CCB5"), (0.5, "#E9DFCD"), (1, "#F3EBDD")], "engrave": "#9E8D74", "rim_hi": 0.6,
    "sidev": [(0, "#F1E8D8"), (1, "#D3C4AA")], "tether": "#D8CCB7", "capg": [(0, "#FFFCF5"), (1, "#D5C7AF")],
    "capline": "#CDBFA8", "captext": "#8F7F67",
    "backrim": [(0, "#EFE6D6"), (1, "#D4C5AC")], "plate": [(0, "#46413B"), (1, "#26231F")],
    "materials": [("Matte cream PC", [(0, "#FCF8F1"), (1, "#D9CBB2")]), ("Oiled walnut", [(0, "#A06C43"), (1, "#5B3720")]),
                  ("Charcoal + smoked glass", [(0, "#5A544D"), (1, "#1F1D1A")])],
}
_BLACK = dict(_CREAM)
_BLACK.update({
    "edge": [(0, "#585C62"), (0.35, "#2C2F33"), (1, "#101113")],
    "face": [(0, "#44474D"), (0.5, "#26282C"), (1, "#18191C")],
    "cap": [(0, "#6E4C34"), (0.55, "#503424"), (1, "#372317")],
    "grain": [("#C0926A", 0.16), ("#140B06", 0.38)], "groove_hi": 0.16,
    "side": [(0, "#7C573A"), (0.12, "#503424"), (0.163, "#3A2618"), (0.166, "#34373C"), (0.5, "#222428"), (1, "#141517")],
    "bezel": [(0, "#4A4E54"), (1, "#0B0C0D")], "ring_inner": "#0E0F11",
    "glass": [(0, "#2A333D"), (0.45, "#0E1216"), (1, "#020304")], "iris": "#3A4F62",
    "dbez": [(0, "#17181B"), (1, "#0A0B0C")], "dbez_stroke": "#4A4E54",
    "lip": ("#FFFFFF", 0.17), "hole": "#030304",
    "frost": [(0, "#EEEBE5"), (0.6, "#D3CFC7"), (0.85, "#ABA79F"), (1, "#86827B")], "frost_rim": 0.35,
    "cable": "#161719", "usb": "#1C1D20", "usb_hi": 0.3, "bootc": [(0, "#3E4146"), (1, "#141517")], "cab_sh": 0.11,
    "sleeve": [(0, "#50545A"), (0.45, "#2C2F33"), (1, "#16171A")], "sleeve_line": "#4E5258", "sleeve_flat": "#2A2C30",
    "sh1": 0.17, "sh1_blur": 8.5, "sh2": 0.20, "osh": 0.13, "osh2": 0.12,
    "under": [(0, "#141517"), (0.5, "#1F2124"), (1, "#2C2F33")], "engrave": "#9A9DA3", "rim_hi": 0.14,
    "sidev": [(0, "#34373C"), (1, "#16171A")], "tether": "#44474D", "capg": [(0, "#50545A"), (1, "#1C1D20")],
    "capline": "#5C6066", "captext": "#B4B7BC",
    "backrim": [(0, "#34373C"), (1, "#17181A")], "plate": [(0, "#4A4D52"), (1, "#222427")],
    "materials": [("Matte charcoal PC", [(0, "#55595F"), (1, "#141517")]), ("Dark walnut", [(0, "#7C573A"), (1, "#372317")]),
                  ("Smoked glass", [(0, "#6A7D90"), (0.5, "#1C2530"), (1, "#050607")])],
})
_ANOD = dict(_BLACK)
_ANOD.update({
    "cap": [(0, "#5E6268"), (0.55, "#3E4146"), (1, "#2A2C30")],
    "grain": [("#FFFFFF", 0.07), ("#000000", 0.16)], "grain_amp": 0.0,
    "side": [(0, "#686C72"), (0.12, "#3E4146"), (0.163, "#2C2F33"), (0.166, "#34373C"), (0.5, "#222428"), (1, "#141517")],
    "materials": [("Matte charcoal PC", [(0, "#55595F"), (1, "#141517")]), ("Anodised charcoal", [(0, "#6A6E74"), (1, "#2A2C30")]),
                  ("Smoked glass", [(0, "#6A7D90"), (0.5, "#1C2530"), (1, "#050607")])],
})
THEMES = {"cream": _CREAM, "black": _BLACK, "black_anodised": _ANOD}
T = THEMES["black"]


def set_theme(name):
    global T
    T = THEMES[name]


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
            yy = y + T["grain_amp"] * (amp * math.sin(k * 0.55 + i * 2.3 + seed) + 0.25 * math.sin(k * 1.9 + i))
            pts.append(f"{x:.2f},{yy:.2f}")
        col, op = T["grain"][i % 2]
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" '
                   f'stroke-width="{0.16 + 0.08 * (i % 3)}" opacity="{op}"/>')
    return "".join(out)


def camera_eye(d: Defs, cx, cy, ch_col, glow=True):
    o = []
    if glow:
        o.append(f'<circle cx="{cx}" cy="{cy}" r="23" fill="{d.rad("ringglow", [(0, ch_col, 0.0), (0.55, ch_col, 0.0), (0.66, ch_col, 0.22), (1, ch_col, 0)])}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy + 0.5}" r="15.6" fill="#000" opacity="0.18" filter="{d.blur(0.6)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="15.2" fill="{d.lin("bezel", T["bezel"])}"/>')
    # status ring (chamber colour) + bloom
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="{ch_col}" stroke-width="2.0" opacity="0.85" filter="{d.blur(0.7)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="{ch_col}" stroke-width="1.05"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="14.1" fill="none" stroke="#FFFFFF" stroke-width="0.3" opacity="0.55"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="13.1" fill="{T["ring_inner"]}"/>')
    # frosted ring light
    o.append(f'<circle cx="{cx}" cy="{cy}" r="12.3" fill="{d.rad("frost", T["frost"], cx=0.42, cy=0.38, r=0.62)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="12.3" fill="none" stroke="#FFFFFF" stroke-width="0.35" opacity="{T["frost_rim"]}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="8.2" fill="#1B1A19"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="8.2" fill="none" stroke="#000" stroke-width="0.4" opacity="0.35"/>')
    # lens
    o.append(f'<circle cx="{cx}" cy="{cy}" r="6.4" fill="{d.rad("glass", T["glass"], cx=0.45, cy=0.42, r=0.6)}"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="3.9" fill="none" stroke="{T["iris"]}" stroke-width="0.55" opacity="0.75"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="2.1" fill="#040506"/>')
    o.append(f'<ellipse cx="{cx - 2.3}" cy="{cy - 2.6}" rx="1.7" ry="0.95" transform="rotate(-38 {cx - 2.3} {cy - 2.6})" fill="#FFFFFF" opacity="0.78"/>')
    o.append(f'<circle cx="{cx + 2.4}" cy="{cy + 2.3}" r="0.55" fill="#FFFFFF" opacity="0.35"/>')
    return "".join(o)


def eink(d: Defs, x, y, ch):
    temp, ph, rise = READ[ch]
    o = [rrect(x, y + 0.5, 57, 23, 3.4, fill="#000", opacity="0.16", filter=d.blur(0.5)),
         rrect(x, y, 57, 23, 3.4, fill=d.lin("dbez", T["dbez"]), stroke=T["dbez_stroke"], stroke_width="0.3"),
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
            o.append(f'<circle cx="{x:.2f}" cy="{y + 0.22:.2f}" r="0.62" fill="{T["lip"][0]}" opacity="{T["lip"][1]}"/>')
            o.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.62" fill="{T["hole"]}"/>')
    return "".join(o)


def front_face(d: Defs, ch):
    col = C[ch]
    cl = d.clip("faceclip", rrect(0.9, 0.9, W_MM - 1.8, H_MM - 1.8, R_MM - 0.9))
    o = [rrect(0, 0, W_MM, H_MM, R_MM, fill=d.lin("edge", T["edge"])),
         rrect(0.9, 0.9, W_MM - 1.8, H_MM - 1.8, R_MM - 0.9,
               fill=d.rad("face", T["face"], cx=0.26, cy=0.14, r=0.95)),
         f'<g clip-path="{cl}">',
         rrect(0, 0, W_MM, CAP_MM, 0, fill=d.lin("wal", T["cap"])),
         wood_grain(d, 0, 0.6, W_MM, CAP_MM - 1.2),
         rrect(0, 0, W_MM, 1.6, 0, fill="#FFFFFF", opacity="0.10"),
         f'<line x1="0" y1="{CAP_MM}" x2="{W_MM}" y2="{CAP_MM}" stroke="#2E1A0E" stroke-width="0.45" opacity="0.65"/>',
         f'<line x1="0" y1="{CAP_MM + 0.45}" x2="{W_MM}" y2="{CAP_MM + 0.45}" stroke="#FFFFFF" stroke-width="0.35" opacity="{T["groove_hi"]}"/>',
         "</g>",
         camera_eye(d, 30, 46.5, col),
         eink(d, 53.5, 21.5, ch),
         grille(d, 56.2, 51.5)]
    return "".join(o)


def hat(d: Defs, ch, motion=True, puck_ry=2.6, k=1.18):
    return f'<g transform="scale({k})">{_hat(d, ch, motion, puck_ry)}</g>'


def _hat(d: Defs, ch, motion, puck_ry, soft=False):
    col = C[ch]
    pk0, pk1 = (("#B9654A", [(0, "#D98263"), (1, "#B05C40")]) if soft else ("#1B1917", [(0, "#55504A"), (1, "#2A2724")]))
    hg = d.rad("hat", [(0, "#FFFFFF"), (0.5, "#F6F1E8"), (1, "#D8CDBB")], cx=-3.5, cy=-22, r=17, user=True)
    o = [f'<ellipse cx="0" cy="0.4" rx="9.2" ry="{puck_ry + 0.4}" fill="{pk0}"/>',
         f'<ellipse cx="0" cy="-0.9" rx="8.6" ry="{puck_ry}" fill="{d.lin("puck" + ("s" if soft else ""), pk1)}"/>',
         f'<rect x="-6.6" y="-11" width="13.2" height="10" fill="{d.lin("band", [(0, "#DCD2C2"), (0.3, "#FFFDF8"), (0.75, "#E6DDCF"), (1, "#C9BDA9")], 0, 0, 1, 0)}"/>',
         f'<ellipse cx="0" cy="-1" rx="6.6" ry="{max(puck_ry * 0.7, 0.5)}" fill="{d.lin("band2", [(0, "#DCD2C2"), (0.3, "#FFFDF8"), (0.75, "#E6DDCF"), (1, "#C9BDA9")], 0, 0, 1, 0)}"/>']
    for x in (-3.6, 0, 3.6):
        o.append(f'<line x1="{x}" y1="-10.5" x2="{x}" y2="-1.5" stroke="#C7BBA7" stroke-width="0.3" opacity="0.7"/>')
    o.append(f'<ellipse cx="0" cy="-11" rx="7.3" ry="1.9" fill="#E3DACB"/>')
    puffs = ((-6.0, -14.8, 5.4), (6.0, -14.8, 5.4), (-3.2, -19.6, 5.6), (3.4, -19.9, 5.6), (0, -15.6, 6.2))
    for (x, y, r) in puffs:
        o.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#D3C5AF" stroke-width="0.7"/>')
    for (x, y, r) in puffs:
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
                 f'stroke-linecap="round" opacity="{T["cab_sh"]}" filter="{d.blur(1.6)}"/>')
    o.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{w}" stroke-linecap="round"/>')
    o.append(f'<path d="{path}" transform="translate(-{w * 0.18:.2f} -{w * 0.12:.2f})" fill="none" stroke="{hi}" '
             f'stroke-width="{w * 0.28:.2f}" stroke-linecap="round" opacity="{hi_op}"/>')
    return "".join(o)


def probe(d: Defs, x, y, ang, ch, needle=36, capped=False):
    """Starter probe: strain relief, cream food-safe silicone sleeve, stainless needle. Local +x = tip."""
    col = C[ch]
    st = d.lin("steel", [(0, "#F4F4F1"), (0.35, "#C9CBCA"), (0.7, "#8C9090"), (1, "#C2C4C3")])
    o = [f'<g transform="translate({x} {y}) rotate({ang})">',
         f'<g transform="translate(2.4 3.2)" opacity="0.2" filter="{d.blur(1.4)}"><rect x="0" y="-2.9" width="{17 + needle}" height="5.8" rx="2.8" fill="#3A2A1A"/></g>',
         '<path d="M 0 -1.3 L 4 -2.4 L 4 2.4 L 0 1.3 Z" fill="#2B2825"/>',
         rrect(3.6, -2.9, 14.5, 5.8, 2.8, fill=d.lin("sleeve", T["sleeve"])),
         rrect(6.0, -2.95, 1.6, 5.9, 0.4, fill=col),
         f'<line x1="11" y1="-2.4" x2="11" y2="2.4" stroke="{T["sleeve_line"]}" stroke-width="0.25"/>',
         f'<line x1="13" y1="-2.4" x2="13" y2="2.4" stroke="{T["sleeve_line"]}" stroke-width="0.25"/>',
         (rrect(18.1, -1.5, needle, 3.0, 1.5, fill=st) + rrect(18.1 + needle - 6, -2.6, 11, 5.2, 2.6, fill=d.lin("pcap", [(0, "#FFFCF5"), (1, "#E3D3BB")]))
          if capped else f'<path d="M 18.1 -1.55 L {18.1 + needle - 3} -1.2 L {18.1 + needle} 0 L {18.1 + needle - 3} 1.2 L 18.1 1.55 Z" fill="{st}"/>'),
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
         rrect(x + 6.4, y - 3.1, 8.2, 6.2, 2.8, fill="#3A302B"),
         f'<line x1="{x + 2.3}" y1="{y - 3.6}" x2="{x + 2.3}" y2="{y + 3.6}" stroke="#6E7272" stroke-width="0.3"/>',
         f'<line x1="{x + 4.3}" y1="{y - 3.6}" x2="{x + 4.3}" y2="{y + 3.6}" stroke="#6E7272" stroke-width="0.3"/>']
    return "".join(o)


def module_hero(d: Defs, ch, with_cables=True):
    """Oblique module in mm: front face at (0,0)-(120,80), back face offset (DX, DY)."""
    o = []
    # wall shadows (back face is on the wall)
    o.append(rrect(DX + 6, DY + 10, W_MM, H_MM, R_MM, fill="#4A3826", opacity=T["sh1"], filter=d.blur(T["sh1_blur"])))
    o.append(rrect(DX + 1.6, DY + 2.6, W_MM, H_MM, R_MM, fill="#3A2A1A", opacity=T["sh2"], filter=d.blur(2.2)))
    if with_cables:
        o.append(cable(d, "M 120 46 C 150 46 158 60 156 95 S 150 150 152 190", 2.4, T["cable"]))
        o.append(cable(d, "M 72 76 C 72 108 86 128 100 190", 3.4, T["cable"]))
        o.append(cable(d, "M 101 76 C 101 104 114 126 124 190", 2.3, T["usb"], hi="#FFFFFF", hi_op=T["usb_hi"]))
        o.append(cable(d, "M 16 76 C 16 98 8 102 -1 107", 2.0, T["cable"]))
        # plug boots under the bottom edge
        o.append(rrect(68.6, 78, 6.8, 9, 1.4, fill=d.lin("boot", [(0, "#45403A"), (1, "#22201D")], 0, 0, 1, 0)))
        o.append(rrect(98.4, 78, 5.2, 7.5, 1.4, fill=d.lin("bootc", T["bootc"], 0, 0, 1, 0)))
        o.append(rrect(14.0, 78, 4.0, 6.0, 1.2, fill=d.lin("boot", [(0, "#45403A"), (1, "#22201D")], 0, 0, 1, 0)))
        o.append(probe(d, -1.5, 107.5, 124, ch))
    # extrusion
    side = d.lin("side", T["side"])
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
    o = [rrect(2, 6, W_MM, H_MM, R_MM, fill="#4A3826", opacity=T["osh"], filter=d.blur(5)),
         front_face(d, ch),
         f'<g transform="translate(90 0.2)">{hat(d, ch, motion=False, puck_ry=1.1)}</g>']
    return "".join(o)


def module_bottom(d: Defs):
    """Underside, back edge at top (y=0), front edge at bottom (y=35)."""
    jack = lambda x, r: (f'<circle cx="{x}" cy="17.5" r="{r + 0.8}" fill="#2B2825"/>'
                         f'<circle cx="{x}" cy="17.5" r="{r}" fill="{d.lin("jm", [(0, "#E6E7E4"), (1, "#7E8282")])}"/>'
                         f'<circle cx="{x}" cy="17.5" r="{r * 0.72}" fill="#0E0F10"/>')
    eng = lambda x, s: text(x, 27.2, s, 2.3, T["engrave"], 700, "middle", 0.25)
    o = [rrect(1.5, 3, W_MM, 35, 11, fill="#4A3826", opacity=T["osh2"], filter=d.blur(3)),
         rrect(0, 0, W_MM, 35, 11, fill=d.lin("under", T["under"])),
         rrect(10, 0, W_MM - 20, 3.0, 1.2, fill="#2E2B27", opacity="0.85"),
         rrect(0.6, 0.6, W_MM - 1.2, 33.8, 10.4, fill="none", stroke="#FFFFFF", stroke_width="0.35", opacity=T["rim_hi"]),
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
    o = [f'<path d="{shape}" transform="translate(2 4)" fill="#4A3826" opacity="{T["osh2"]}" filter="{d.blur(3)}"/>',
         f'<path d="{shape}" fill="{d.lin("sidev", T["sidev"], 0, 0, 1, 0)}"/>',
         f'<g clip-path="{cl}">',
         rrect(0, 0, 35, CAP_MM, 0, fill=d.lin("wal", T["cap"])),
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
         f'<path d="M 16 49 C 13 54 13 58 16 61.5" fill="none" stroke="{T["tether"]}" stroke-width="0.9" stroke-linecap="round"/>',
         f'<circle cx="16" cy="66.2" r="4.9" fill="#000" opacity="0.15" filter="{d.blur(0.6)}"/>',
         f'<circle cx="16" cy="65.8" r="4.7" fill="{d.rad("capg", T["capg"], cx=0.4, cy=0.35, r=0.7)}"/>',
         f'<circle cx="16" cy="65.8" r="3.2" fill="none" stroke="{T["capline"]}" stroke-width="0.35"/>',
         text(16, 66.7, "0 mV", 1.9, T["captext"], 700, "middle", 0.1)]
    return "".join(o)


def module_back(d: Defs):
    o = [rrect(1.5, 4, W_MM, H_MM, R_MM, fill="#4A3826", opacity=T["osh2"], filter=d.blur(3.5)),
         rrect(0, 0, W_MM, H_MM, R_MM, fill=d.lin("backrim", T["backrim"])),
         rrect(7, 7, W_MM - 14, H_MM - 14, R_MM - 7, fill=d.lin("plate", T["plate"]))]
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
    def lead(pts):
        pp = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        return (f'<polyline points="{pp}" fill="none" stroke="#FFFFFF" stroke-width="5" stroke-linejoin="round" opacity="0.75"/>'
                f'<polyline points="{pp}" fill="none" stroke="{C["lead"]}" stroke-width="1.7" stroke-linejoin="round"/>')
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
def materials_row(d: Defs, x, y):
    o, cx = [], x
    for i, (label, stops) in enumerate(T["materials"]):
        o.append(f'<circle cx="{cx + 16}" cy="{y + 2}" r="16" fill="#000" opacity="0.12" filter="{d.blur(2)}"/>')
        o.append(f'<circle cx="{cx + 16}" cy="{y}" r="16" fill="{d.rad(f"mat{i}", stops, cx=0.35, cy=0.3, r=0.8)}"/>')
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
         + text(48, 70, f"Mounted on the inner wall · chamber {ch}", 22, C["ink2"], 600, ls=0.3)
         + materials_row(d, 1432 - sum(82 + len(m[0]) * 10.2 for m in T["materials"]) + 40, 62) + "</g>",
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


def proofing_box(d: Defs, ch, ox=110, oy=150, char=False):
    """Countertop proofing box with glass door; module on the back wall, jar on a wire shelf."""
    col = C[ch]
    bw, bh, br = 900, 760, 34
    bdx, bdy = 70, -48
    o = [rrect(ox + bdx * 0.4 + 20, oy + 40, bw, bh, br, fill="#4A3826", opacity="0.17", filter=d.blur(34)),
         rrect(ox - 10, oy + bh - 6, bw + bdx + 20, 26, 13, fill="#3A2A1A", opacity="0.15", filter=d.blur(12))]
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
    if char:   # the character stands on the shelf next to the jar; its tail probe goes into the dough
        ms = 2.15
        mx, my = ix + 150, sy - (80 + LEG_H) * ms
        o.append(f'<ellipse cx="{mx + 60 * ms}" cy="{sy + 2}" rx="{70 * ms}" ry="10" fill="#2A1C10" opacity="0.22" filter="{d.blur(5)}"/>')
        o.append(cable(d, f"M {mx + 100 * ms} {sy - 3} L {jx - 14} {sy - 3} C {jx - 30} {sy - 3} {jx - 26} {jy + 20} {jx - 20} {jy - 20} C {jx - 12} {jy - 70} {jx + 70} {jy - 90} {jx + 70} {jy - 40}", 4.5, CH["cable"]))
    else:
        o.append(cable(d, f"M {mx + 16 * ms} {my + 78 * ms} C {mx + 16 * ms} {my + 200} {jx + 70} {jy - 80} {jx + 70} {jy + 40}", 4.5, T["cable"]))
    o.append(rrect(jx + 66, jy - 30, 9, 190, 4, fill=d.lin("steelv", [(0, "#F4F4F1"), (0.5, "#B9BBBB"), (1, "#8C9090")], 0, 0, 1, 0)))
    o.append(rrect(jx + 62, jy - 70, 17, 46, 7, fill=CH["cream"] if char else T["sleeve_flat"]))
    o.append(rrect(jx + 62, jy - 60, 17, 5, 1, fill=col))
    if char:
        o.append(f'<g transform="translate({mx} {my}) scale({ms})">{char_front(d, ch, tail=False, cables=False)}</g>')
    else:
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


def context_doc(ch="B", char=False):
    d = Defs("c")
    title = "In context · a little buddy for the box you already have" if char else "In context · drops into the box you already have"
    o = [rrect(0, 0, CTX_W, CTX_H, 36, fill=d.lin("ctxbg", [(0, "#F4EEE4"), (1, "#E8DFD0")])),
         proofing_box(d, ch, char=char),
         phone(d, ch, 1150, 170),
         text(70, 86, title, 30, C["ink"], 600)]
    return svg_doc(CTX_W, CTX_H, d, "".join(o), bg=C["page"])


def context_asset(ch="B", char=False):
    d = Defs("c")
    body = rrect(0, 0, 1100, CTX_H, 0, fill="none") + proofing_box(d, ch, char=char)
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


VAR_W, VAR_H = 1640, 760
VARIANTS = [("black", "Dark walnut cap", "warm, ties to the crust palette; RF-transparent"),
            ("black_anodised", "Anodised charcoal cap", "monolithic and pro-kitchen; antenna needs a slot")]


def variants_body(labels=True):
    """Cap options: two front views side by side (each with its own Defs, so gradients don't collide)."""
    defs, o = [], [rrect(0, 0, VAR_W, VAR_H, 36, fill=C["card"])]
    for i, (theme, title, sub) in enumerate(VARIANTS):
        set_theme(theme)
        d = Defs(f"v{i}")
        x0 = 140 + i * 780
        o.append(f'<g transform="translate({x0} 220) scale(5)">{module_front_ortho(d, "B")}</g>')
        if labels:
            o.append(text(x0, 680, title, 28, C["ink"], 600))
            o.append(text(x0, 716, sub, 21, C["ink2"]))
        defs.append(d.xml())
    set_theme("black")
    if labels:
        o.append(text(60, 84, "Cap options", 34, C["ink"], 600))
    return "".join(defs), "".join(o)


def variants_doc(labels=True):
    defs, body = variants_body(labels)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{VAR_W}" height="{VAR_H}" viewBox="0 0 {VAR_W} {VAR_H}">'
            f'{defs}<rect width="{VAR_W}" height="{VAR_H}" fill="{C["page"]}"/>{body}</svg>')



# =========================================================================== v3: the character
# The Node as a little Clawd-style critter wearing the chef hat. Same 120 x 80 x 35 mm body, same ports.
CH = {"eye": "#2B2522", "cream": "#FBF3E6", "stitch": "#D8BE9A", "blush": "#FFBFB4", "cable": "#7A655A",
      "foot": [(0, "#D9805F"), (1, "#B05C40")]}
BODY_R, LEG_H = 24, 6.5
LEGS = [(10, 22), (26, 38), (82, 94), (98, 110)]
EYE_L, EYE_R = (42, 30), (78, 30)
CLAY_SIDE = [(0, "#F4AC8C"), (0.08, "#EE9F7E"), (0.5, "#D98263"), (1, "#BC674C")]


def qpt(p0, p1, p2, t):
    return tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t ** 2 * c for a, b, c in zip(p0, p1, p2))


def sparkles(cx, cy):
    return (f'<circle cx="{cx - 2.4}" cy="{cy - 4.8}" r="2.0" fill="#FFFFFF" opacity="0.95"/>'
            f'<circle cx="{cx + 2.4}" cy="{cy + 6.2}" r="0.9" fill="#FFFFFF" opacity="0.6"/>')


def char_eyes(d: Defs, ch, wink=False):
    col = C[ch]
    sock = d.lin("sock", [(0, "#44392F"), (1, "#1C1715")])
    cx, cy = EYE_L                      # camera eye: smoked lens + soft ring light
    o = [rrect(cx - 7.5, cy - 11, 15, 22, 7.5, fill=sock),
         f'<circle cx="{cx}" cy="{cy + 1}" r="6.1" fill="none" stroke="#F4E9DA" stroke-width="1.2" opacity="0.75"/>',
         f'<circle cx="{cx}" cy="{cy + 1}" r="4.9" fill="{d.rad("cglass", [(0, "#3B4A5A"), (0.5, "#141A21"), (1, "#050607")], cx=0.45, cy=0.42, r=0.6)}"/>',
         f'<circle cx="{cx}" cy="{cy + 1}" r="2.9" fill="none" stroke="#4E6C86" stroke-width="0.45" opacity="0.8"/>',
         f'<circle cx="{cx}" cy="{cy + 1}" r="1.5" fill="#030405"/>', sparkles(cx, cy)]
    cx, cy = EYE_R                      # status eye: LED ring in chamber colour, winks while thinking
    if wink:
        pth = f"M {cx - 7} {cy + 3.5} Q {cx} {cy - 7.5} {cx + 7} {cy + 3.5}"
        o += [f'<path d="{pth}" fill="none" stroke="{col}" stroke-width="4.5" stroke-linecap="round" opacity="0.6" filter="{d.blur(1.2)}"/>',
              f'<path d="{pth}" fill="none" stroke="{CH["eye"]}" stroke-width="4.6" stroke-linecap="round"/>',
              f'<path d="{pth}" fill="none" stroke="{col}" stroke-width="2.2" stroke-linecap="round"/>']
    else:
        o += [rrect(cx - 7.5, cy - 11, 15, 22, 7.5, fill=sock),
              rrect(cx - 5.2, cy - 8.7, 10.4, 17.4, 5.2, fill=col, opacity="0.18"),
              rrect(cx - 5.2, cy - 8.7, 10.4, 17.4, 5.2, fill="none", stroke=col, stroke_width="2.6", opacity="0.75", filter=d.blur(0.8)),
              rrect(cx - 5.2, cy - 8.7, 10.4, 17.4, 5.2, fill="none", stroke=col, stroke_width="1.4"),
              sparkles(cx, cy)]
    return "".join(o)


def char_face(d: Defs, ch, wink=False):
    temp, ph, rise = READ[ch]
    cl = d.clip("cbody", rrect(0.9, 0.9, 118.2, 78.2, BODY_R - 0.9))
    apron = "M 31 84 L 33.4 55 Q 33.9 50 39 50 L 81 50 Q 86.1 50 86.6 55 L 89 84 Z"
    stitch = "M 33.3 84 L 35.5 56 Q 35.9 52 39.6 52 L 80.4 52 Q 84.1 52 84.5 56 L 86.7 84"
    o = [rrect(0, 0, 120, 80, BODY_R, fill=d.lin("cedge", [(0, "#FBC9B0"), (0.5, "#E99474"), (1, "#C26A4C")])),
         rrect(0.9, 0.9, 118.2, 78.2, BODY_R - 0.9, fill=d.rad("cface", [(0, "#F9C0A4"), (0.5, "#EC9877"), (1, "#D67D5C")], cx=0.26, cy=0.14, r=0.95)),
         f'<g clip-path="{cl}">',
         f'<ellipse cx="38" cy="9" rx="30" ry="4.5" fill="#FFFFFF" opacity="0.22" filter="{d.blur(2)}"/>',
         f'<path d="{apron}" transform="translate(0 0.8)" fill="#8A4A32" opacity="0.18" filter="{d.blur(0.8)}"/>',
         f'<path d="{apron}" fill="{d.lin("apron", [(0, "#FFFAF1"), (1, "#F1E3CD")])}"/>',
         f'<path d="{stitch}" fill="none" stroke="{CH["stitch"]}" stroke-width="0.4" stroke-dasharray="1.1 0.9" stroke-linecap="round"/>',
         "</g>",
         # e-ink pocket on the apron
         rrect(39.5, 55, 41, 19.5, 4, fill="#F7EFE3", stroke="#D9C3A3", stroke_width="0.5"),
         rrect(41.3, 56.8, 37.4, 15.9, 2.6, fill=d.lin("cpaper", [(0, "#F3F0E9"), (1, "#E2DED4")])),
         text(43.4, 69.3, temp.split(".")[0] + "°", 10.5, "#2B2522", 600, ls=-0.3),
         text(61.8, 63.4, ph, 3.2, "#3A3632", 600),
         text(61.8, 69.6, rise, 3.2, "#3A3632", 600),
         f'<circle cx="76.4" cy="59.3" r="0.95" fill="{C[ch]}"/>']
    for x in (24.5, 95.5):           # blush
        o.append(f'<ellipse cx="{x}" cy="42.5" rx="7" ry="3.6" fill="{CH["blush"]}" opacity="0.8" filter="{d.blur(0.6)}"/>')
    o.append(char_eyes(d, ch, wink))
    for k in range(8):               # smile = air vent (BME680 behind it)
        x, y = qpt((51.5, 43.4), (60, 50.2), (68.5, 43.4), k / 7)
        o.append(f'<circle cx="{x:.2f}" cy="{y + 0.3:.2f}" r="0.85" fill="#FFFFFF" opacity="0.35"/>')
        o.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.85" fill="{CH["eye"]}"/>')
    return "".join(o)


def ex(d: Defs, x, y, w, h, r, d0, d1, side, front, n=28):
    """Small extrusion from depth d0..d1 mm (oblique)."""
    o = []
    for i in range(n, -1, -1):
        t = (d0 + (d1 - d0) * i / n) / 35
        o.append(rrect(x + DX * t, y + DY * t, w, h, r, fill=side if i else front))
    return "".join(o)


def tail(d: Defs, ch, ortho=True):
    """Starter-probe cable curled like a tail; the probe is the tail tip."""
    pth = "M 16 82 C 4 92 -16 93 -18 80 C -19 72 -15 66 -19 60"
    return cable(d, pth, 2.2, CH["cable"], hi_op=0.3) + probe(d, -19.2, 60.5, -100, ch, needle=16, capped=True)


def char_front(d: Defs, ch, wink=False, tail_on=True, tail=True, cables=True, hat_motion=False):
    col = C[ch]
    o = [f'<ellipse cx="60" cy="{80 + LEG_H + 1}" rx="66" ry="4" fill="#6B4A35" opacity="0.2" filter="{d.blur(2.5)}"/>']
    if tail and tail_on:
        o.append(globals()["tail"](d, ch))
    arm = d.lin("carm", [(0, "#F4AC8C"), (1, "#C46C4D")])
    o.append(rrect(-8.5, 37, 13, 15, 5.5, fill=arm))
    o.append(rrect(115.5, 37, 13, 15, 5.5, fill=arm))
    for x0, x1 in LEGS:
        o.append(rrect(x0, 72, x1 - x0, 8 + LEG_H, 4, fill=d.lin("cfoot", CH["foot"])))
    if cables:
        o.append(cable(d, "M 141 44.5 C 150 45 152 60 150 72 S 150 88 156 92", 2.2, CH["cable"], hi_op=0.3))
        o.append(bnc_side_plug(d, 128.4, 44.5))
    o.append(char_face(d, ch, wink))
    o.append(f'<g transform="translate(60 0.8) rotate(-6) scale(1.55)">{_hat(d, ch, hat_motion, 1.1, soft=True)}</g>')
    return "".join(o)


def char_hero(d: Defs, ch, wink=False):
    col = C[ch]
    side = d.lin("cside", CLAY_SIDE)
    arm_side = d.lin("carmside", [(0, "#F0A585"), (1, "#B9654A")])
    arm_front = d.lin("carm", [(0, "#F4AC8C"), (1, "#C46C4D")])
    foot = d.lin("cfoot", CH["foot"])
    foot_side = d.lin("cfootside", [(0, "#C46C4D"), (1, "#9C4F36")])
    o = [f'<ellipse cx="{60 + DX * 0.45}" cy="{80 + LEG_H + DY * 0.45}" rx="76" ry="12" fill="#6B4A35" opacity="0.15" filter="{d.blur(5)}"/>']
    for x0, x1 in LEGS:   # contact shadows
        o.append(f'<ellipse cx="{(x0 + x1) / 2 + 1.5}" cy="{80 + LEG_H - 0.4}" rx="{(x1 - x0) / 2 + 2}" ry="1.6" fill="#4A3020" opacity="0.28" filter="{d.blur(0.8)}"/>')
    o.append(cable(d, "M 96 84 C 104 92 134 93 190 97", 2.6, CH["cable"], hi_op=0.3))      # USB-C + Peltier, bundled
    o.append(tail(d, ch))
    o.append(ex(d, -8.5, 37, 13, 15, 5.5, 10, 25, arm_side, arm_front))                    # left arm (behind)
    for x0, x1 in LEGS:                                                                     # back legs, then front legs
        o.append(ex(d, x0, 72, x1 - x0, 8 + LEG_H, 4, 21, 32, foot_side, foot))
    for x0, x1 in LEGS:
        o.append(ex(d, x0, 72, x1 - x0, 8 + LEG_H, 4, 3, 14, foot_side, foot))
    for i in range(56, 0, -1):                                                              # body
        t = i / 56
        o.append(rrect(DX * t, DY * t, 120, 80, BODY_R, fill=side))
    o.append(f'<path d="M {BODY_R} 0 L {BODY_R + DX} {DY} L {120 - BODY_R + DX} {DY} L {120 - BODY_R} 0 Z" fill="#FFFFFF" opacity="0.10"/>')
    o.append(ex(d, 115.5, 37, 13, 15, 5.5, 10, 25, arm_side, arm_front))                   # right arm + pH plug
    bx, by = 128.5 + DX * 0.5, 44.5 + DY * 0.5
    o.append(bnc_side_plug(d, bx, by))
    o.append(cable(d, f"M {bx + 13.5} {by} C {bx + 24} {by} {bx + 26} {by + 18} {bx + 24} {by + 32} S {bx + 30} 92 190 96", 2.2, CH["cable"], hi_op=0.3))
    o.append(char_face(d, ch, wink))
    hx, hy = 60 + DX * 0.5, DY * 0.5 + 0.8
    o.append(f'<ellipse cx="{hx + 5}" cy="{hy + 0.8}" rx="15" ry="3.4" fill="#7A3E26" opacity="0.35" filter="{d.blur(1.3)}"/>')
    o.append(f'<g transform="translate({hx} {hy}) rotate(-6) scale(1.55)">{_hat(d, ch, True, 2.6, soft=True)}</g>')
    return "".join(o)


CHERO_S, CHERO_OX, CHERO_OY = 6.2, 330, 640


def char_hero_body(d: Defs, ch, wink=False, bg=True):
    o = []
    if bg:
        o.append(rrect(0, 0, HERO_W, HERO_H, 36, fill=d.lin("cwall", [(0, "#F8F1E7"), (0.62, "#F1E6D6"), (1, "#E8D9C4")])))
        o.append(rrect(0, 0, HERO_W, HERO_H, 36, fill=d.rad("cwallglow", [(0, "#FFFFFF", 0.6), (1, "#FFFFFF", 0)], cx=0.45, cy=0.35, r=0.6)))
    cl = d.clip("cherocard", rrect(0, 0, HERO_W, HERO_H, 36))
    o.append(f'<g clip-path="{cl}"><g transform="translate({CHERO_OX} {CHERO_OY}) scale({CHERO_S})">{char_hero(d, ch, wink)}</g>')
    # "hi!" bubble
    o.append(f'<g filter="{d.blur(9)}" opacity="0.16"><rect x="168" y="372" width="210" height="122" rx="61" fill="#6B4A35"/></g>')
    o.append('<path d="M 300 470 Q 330 515 372 540 Q 342 505 338 470 Z" fill="#FFFFFF"/>')
    o.append(rrect(160, 360, 210, 122, 61, fill="#FFFFFF"))
    o.append(text(265, 440, "hi!", 64, "#2B2522", 700, "middle", 0.5))
    o.append("</g>")
    return "".join(o)


def char_bottom(d: Defs):
    """Underside: ports in a row along the back half, the four feet along the front half."""
    jack = lambda x, r: (f'<circle cx="{x}" cy="11" r="{r + 0.9}" fill="#8A4A32"/>'
                         f'<circle cx="{x}" cy="11" r="{r}" fill="{d.lin("jm", [(0, "#E6E7E4"), (1, "#7E8282")])}"/>'
                         f'<circle cx="{x}" cy="11" r="{r * 0.72}" fill="#1E1917"/>')
    eng = lambda x, s: text(x, 18.6, s, 2.2, "#FFF1E6", 700, "middle", 0.25, 'opacity="0.9"')
    o = [rrect(1.5, 3, 120, 35, 14, fill="#6B4A35", opacity="0.14", filter=d.blur(3)),
         rrect(0, 0, 120, 35, 14, fill=d.lin("cunder", [(0, "#C9704F"), (1, "#E0906F")])),
         rrect(0.6, 0.6, 118.8, 33.8, 13.4, fill="none", stroke="#FFFFFF", stroke_width="0.35", opacity="0.35"),
         jack(28, 2.0), eng(28, "T1"), jack(39, 2.0), eng(39, "T2"), jack(50, 1.6), eng(50, "DOOR"),
         rrect(62, 8, 15, 6, 1.6, fill="#3A2A22"), rrect(63, 9, 13, 4, 1.0, fill="#1A1412")]
    for k in range(6):
        o.append(rrect(64.0 + k * 2.0, 10.2, 0.9, 1.6, 0.2, fill="#C9A85A"))
    o.append(eng(69.5, "PELTIER · FAN"))
    o.append(rrect(86.4, 9.3, 9.2, 3.4, 1.7, fill="#1E1917", stroke="#A9ABAA", stroke_width="0.5"))
    o.append(rrect(88.5, 10.6, 5.0, 0.8, 0.3, fill="#4A4A4A"))
    o.append(eng(91, "USB-C"))
    for x0, x1 in LEGS:
        o.append(rrect(x0, 21.5, x1 - x0, 10.5, 4, fill=d.lin("cfoot", CH["foot"])))
        o.append(rrect(x0 + 2, 23.5, x1 - x0 - 4, 6.5, 3, fill="#8A4A32", opacity="0.55"))
    return "".join(o)


def char_back(d: Defs, ch):
    arm = d.lin("carm", [(0, "#F4AC8C"), (1, "#C46C4D")])
    o = [f'<ellipse cx="60" cy="{80 + LEG_H + 1}" rx="64" ry="4" fill="#6B4A35" opacity="0.18" filter="{d.blur(2.5)}"/>',
         rrect(-8.5, 37, 13, 15, 5.5, fill=arm), rrect(115.5, 37, 13, 15, 5.5, fill=arm)]
    for x0, x1 in LEGS:
        o.append(rrect(x0, 72, x1 - x0, 8 + LEG_H, 4, fill=d.lin("cfoot", CH["foot"])))
    o += [rrect(0, 0, 120, 80, BODY_R, fill=d.lin("cback", [(0, "#F0A485"), (1, "#CF7656")])),
          rrect(14, 14, 92, 52, 14, fill=d.lin("cplate", [(0, "#FBF3E6"), (1, "#EADBC4")]))]
    for (x, y) in ((27, 26), (93, 26), (27, 54), (93, 54)):
        o.append(f'<circle cx="{x}" cy="{y}" r="5.4" fill="{d.rad("cmag", [(0, "#E3DCD2"), (1, "#A69C90")], cx=0.4, cy=0.35, r=0.7)}"/>')
    for x in (50, 70):
        o.append(f'<path d="M {x} 30 L {x} 38" stroke="#8A6A55" stroke-width="2.2" stroke-linecap="round"/>')
        o.append(f'<circle cx="{x}" cy="40" r="2.5" fill="#8A6A55"/>')
    o.append('<path d="M 60 59 C 57 55.5 53.6 57.4 55.2 60.4 L 60 64.5 L 64.8 60.4 C 66.4 57.4 63 55.5 60 59 Z" fill="#E9A089"/>')
    o.append(f'<g transform="translate(60 0.8) rotate(6) scale(1.55)">{_hat(d, ch, False, 1.1, soft=True)}</g>')
    return "".join(o)


# ---- friendly callout board (<= 8 labels)
CFS, CFX, CFY = 5.0, 380, 300
CLEFT = [("Chef hat", ["bobs when its agent starts", "a new experiment (N20 motor)"], (44, -24), 150),
         ("Camera eye", ["watches the dough rise", "through the jar"], (34.6, 30), 390),
         ("Smile vent", ["sniffs the air: temperature,", "humidity, gas (BME680)"], (52, 44), 560)]
CRIGHT = [("Status eye", ["glows blue for A, orange for B;", "winks while it's thinking"], (85.4, 25), 330),
          ("pH hand", ["BNC port for the pH probe,", "with a tethered 0 mV cap"], (133, 44.5), 490),
          ("Apron badge", ["e-ink temperature · pH · rise,", "readable with the light off"], (80.5, 64), 650)]
CUS, CUX, CUY = 6.6, 60, 950          # underside
CBS, CBX, CBY = 2.8, 930, 840         # back (CBY = hat top)
CUNDER = ("Ports underneath, between the feet", ["T1 starter probe (the tail) · T2 aux · DOOR", "PELTIER / fan · USB-C 20 W"])
CBACK = ("Magnetic back", ["4 magnets + 2 keyholes: sticks to a wall,", "or stands on its four rubber feet"])


def cfpx(x, y):
    return CFX + CFS * x, CFY + CFS * y


def char_callout_body(d: Defs, ch, labels=True):
    o = [rrect(0, 0, CAL_W, CAL_H, 36, fill=C["card"]),
         f'<g transform="translate({CFX} {CFY}) scale({CFS})">{char_front(d, ch, tail=False)}</g>',
         f'<g transform="translate({CUX} {CUY}) scale({CUS})">{char_bottom(d)}</g>',
         f'<g transform="translate({CBX + 8.5 * CBS} {CBY + 42 * CBS}) scale({CBS})">{char_back(d, ch)}</g>']

    def lead(pts):
        pp = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        return (f'<polyline points="{pp}" fill="none" stroke="#FFFFFF" stroke-width="5" stroke-linejoin="round" stroke-linecap="round" opacity="0.8"/>'
                f'<polyline points="{pp}" fill="none" stroke="{C["lead"]}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>')

    def dot(x, y):
        return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="#FFFFFF" opacity="0.92"/>'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.4" fill="{C[ch]}"/>')
    for _, _, (tx, ty), ly in CLEFT:
        px, py = cfpx(tx, ty)
        o.append(lead([(330, ly), (352, ly), (px, py)]) + dot(px, py))
    for _, _, (tx, ty), ly in CRIGHT:
        px, py = cfpx(tx, ty)
        o.append(lead([(1150, ly), (1128, ly), (px, py)]) + dot(px, py))
    if labels:
        o.append(char_callout_text(ch))
    return "".join(o)


def char_callout_text(ch):
    o = [text(40, 64, "Say hello to the Node", 34, C["ink"], 600),
         text(40, 100, "every sensor has a job on its face · 120 × 80 × 35 mm body", 20, C["ink3"])]
    for t, s, _, ly in CLEFT:
        o.append(label_block(318, ly - 12, t, s, "end"))
    for t, s, _, ly in CRIGHT:
        o.append(label_block(1162, ly - 12, t, s, "start"))
    t, s = CUNDER
    o.append(label_block(CUX, CUY + 35 * CUS + 64, t, s, "start"))
    t, s = CBACK
    o.append(label_block(CBX, CUY + 35 * CUS + 64, t, s, "start"))
    return "".join(o)


CHAR_MATERIALS = [("Soft-touch terracotta", [(0, "#F6B394"), (1, "#C96F50")]), ("Cream apron", [(0, "#FFFAF1"), (1, "#E6D3B6")]),
                  ("Smoked-glass eyes", [(0, "#6A7D90"), (0.5, "#1C2530"), (1, "#050607")])]


def char_materials_row(d: Defs, x, y):
    o, cx = [], x
    for i, (label, stops) in enumerate(CHAR_MATERIALS):
        o.append(f'<circle cx="{cx + 16}" cy="{y + 2}" r="16" fill="#000" opacity="0.10" filter="{d.blur(2)}"/>')
        o.append(f'<circle cx="{cx + 16}" cy="{y}" r="16" fill="{d.rad(f"cmat{i}", stops, cx=0.35, cy=0.3, r=0.8)}"/>')
        o.append(text(cx + 42, y + 7, label, 20, C["ink2"], 500))
        cx += 42 + len(label) * 10.2 + 40
    return "".join(o)


def composite_char(ch="B"):
    d = Defs("q")
    mw = sum(82 + len(m[0]) * 10.2 for m in CHAR_MATERIALS) - 40
    o = [text(80, 112, "FermentAI Node", 64, C["ink"], 600, ls=-0.5),
         text(82, 160, "A friendly little sensor buddy for any proofing box, restaurant cabinet or bakery retarder", 27, C["ink2"]),
         rrect(2722, 76, 398, 52, 26, fill="none", stroke="#B08A5E", stroke_width="2"),
         text(2921, 110, "CONCEPT MOCKUP", 20, "#8A6338", 700, "middle", 2.4),
         text(2536, 109, "EYE COLOUR", 17, C["ink3"], 700, "end", 2), f'<circle cx="2560" cy="102" r="9" fill="{C["A"]}"/>', text(2576, 110, "A", 22, C["ink2"], 600),
         f'<circle cx="2628" cy="102" r="9" fill="{C["B"]}"/>', text(2644, 110, "B", 22, C["ink2"], 600),
         f'<g transform="translate(80 210)">{char_hero_body(d, ch, wink=True)}'
         + text(48, 70, f"Says hi from inside the proofing box · chamber {ch}", 22, C["ink2"], 600, ls=0.3)
         + char_materials_row(d, HERO_W - 48 - mw, 62) + "</g>",
         f'<g transform="translate(1640 210)">{char_callout_body(d, ch)}</g>']
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
    o.append(text(80, 2086, "Character styling after the Clawd mascot. Readings are illustrative. See data/design/README.md for real-vs-concept.",
                  21, C["ink3"]))
    return svg_doc(COMP_W, COMP_H, d, "".join(o), bg=C["page"])


MOOD_W, MOOD_H = 1640, 760
MOODS = [("A", False, "Watching · chamber A", "status eye glows blue"),
         ("B", False, "Watching · chamber B", "status eye glows orange"),
         ("B", True, "Thinking", "the status eye winks")]


def moods_doc(labels=True):
    defs, o = [], [rrect(0, 0, MOOD_W, MOOD_H, 36, fill=C["card"])]
    for i, (ch, wink, title, sub) in enumerate(MOODS):
        d = Defs(f"md{i}")
        x0 = 90 + i * 520
        o.append(f'<g transform="translate({x0 + 8.5 * 3.4} {150 + 42 * 3.4}) scale(3.4)">{char_front(d, ch, wink, tail=False, cables=False, hat_motion=wink)}</g>')
        if labels:
            o.append(text(x0 + 20, 676, title, 28, C["ink"], 600))
            o.append(text(x0 + 20, 712, sub, 21, C["ink2"]))
        defs.append(d.xml())
    if labels:
        o.insert(1, text(60, 84, "Moods", 34, C["ink"], 600))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{MOOD_W}" height="{MOOD_H}" viewBox="0 0 {MOOD_W} {MOOD_H}">'
            f'{"".join(defs)}<rect width="{MOOD_W}" height="{MOOD_H}" fill="{C["page"]}"/>{"".join(o)}</svg>')


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    outs = {}
    # earlier colourways, kept for comparison
    for theme, suffix in (("cream", "_cream"), ("black", "_black")):
        set_theme(theme)
        outs[HERE / f"sensor_module_mockup{suffix}.svg"] = (composite("B"), COMP_W, COMP_H)
        outs[HERE / f"sensor_module_context{suffix}.svg"] = (context_doc("B"), CTX_W, CTX_H)
    outs[HERE / "sensor_module_cap_variants_black.svg"] = (variants_doc(), VAR_W, VAR_H)
    set_theme("black")
    # current: the character
    outs[HERE / "sensor_module_mockup.svg"] = (composite_char("B"), COMP_W, COMP_H)
    outs[HERE / "sensor_module_context.svg"] = (context_doc("B", char=True), CTX_W, CTX_H)
    outs[HERE / "sensor_module_moods.svg"] = (moods_doc(), MOOD_W, MOOD_H)
    for ch in "AB":
        for mood, wink in (("open", False), ("wink", True)):
            d = Defs(f"h{ch}{mood}")
            outs[ASSETS / f"hero_{ch}_{mood}.svg"] = (svg_doc(HERO_W, HERO_H, d, char_hero_body(d, ch, wink)), HERO_W, HERO_H)
        d = Defs(f"k{ch}")
        outs[ASSETS / f"callouts_{ch}.svg"] = (svg_doc(CAL_W, CAL_H, d, char_callout_body(d, ch, labels=False)), CAL_W, CAL_H)
    outs[ASSETS / "context_box_B.svg"] = (context_asset("B", char=True), 1100, CTX_H)
    outs[ASSETS / "moods.svg"] = (moods_doc(labels=False), MOOD_W, MOOD_H)
    eng = None
    for p, (s, w, h) in outs.items():
        p.write_text(s)
        if p.parent == HERE or "--assets-png" in sys.argv:
            eng = render_png(p, p.with_suffix(".png"), w, h)
    lab = {"left": [(t, s, ly) for t, s, _, ly in CLEFT], "right": [(t, s, ly) for t, s, _, ly in CRIGHT],
           "captions_y": CUY + 35 * CUS + 64}
    (ASSETS / "callout_labels.json").write_text(json.dumps(lab, indent=1, ensure_ascii=False))
    print(f"rendered with {eng}:", *[str(p.relative_to(HERE)) for p in outs], sep="\n  ")


if __name__ == "__main__":
    main()
