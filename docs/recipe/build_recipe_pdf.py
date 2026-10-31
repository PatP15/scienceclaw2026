"""Build the printable FermentAI sourdough recipe PDF.

Every number is read from the repo at build time:
  configs/experiments.yaml                      recipe, chamber plan, starter checklist
  data/predictions/chamber_{A,B}_summary.json   predicted timeline per chamber (simulated)
  data/predictions/comparison_summary.json      paired A/B comparison (simulated)
  data/stl/chain_summary.json                   crumb numbers measured on the simulated mesh
  docs/scientific_assumptions.md (sec. 5)       B cooling intervention example (quoted, see B_COOL_*)
  README.md / docs/context-revised-hackathon-plan.md   baking workflow (method text)

Run from the repo root:
  uv run --with reportlab python docs/recipe/build_recipe_pdf.py
Output: docs/recipe/FermentAI_sourdough_recipe.pdf (+ downscaled images in docs/recipe/img/).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "recipe"
IMG_DIR = OUT_DIR / "img"
PDF_PATH = OUT_DIR / "FermentAI_sourdough_recipe.pdf"

# Quoted from docs/scientific_assumptions.md, section 5 "Overproofing risk at 27 degC":
# "B at 27 degC, cooled to 22 degC at 6 h, gives pH 4.01 at 16 h and delays overproof onset to about
#  11 h. B cooled to 18 degC at 4 h gives pH 4.25 at 16 h and no overproof signal within 16 h."
B_COOL_MAIN = {"to_c": 22, "at_h": 6, "ph16": 4.01, "overproof_h": 11}
B_COOL_ALT = {"to_c": 18, "at_h": 4, "ph16": 4.25}

# ---------------------------------------------------------------- palette / styles
INK = colors.HexColor("#2b2118")
CRUST = colors.HexColor("#8a5a2b")
CREAM = colors.HexColor("#f8f5ef")
LINE = colors.HexColor("#d9cdbb")
A_COL = colors.HexColor("#3d6f8e")
B_COL = colors.HexColor("#b5562b")
WARN_BG = colors.HexColor("#fdf1e4")
WARN_EDGE = colors.HexColor("#c9732f")
SIM_BG = colors.HexColor("#eef2f5")

ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.6, leading=12.6,
                      textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.2, leading=10.4)
TINY = ParagraphStyle("tiny", parent=BODY, fontSize=7.4, leading=9.2, textColor=colors.HexColor("#5a4e42"))
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK,
                    spaceAfter=2)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=10.5, leading=14, textColor=CRUST, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=CRUST,
                    spaceBefore=8, spaceAfter=4)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceAfter=2)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.8, leading=11)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
CELL_W = ParagraphStyle("cellw", parent=CELL_B, textColor=colors.white, alignment=TA_CENTER)
CAP = ParagraphStyle("cap", parent=SMALL, alignment=TA_CENTER, textColor=colors.HexColor("#5a4e42"))


def P(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text.replace(" degC", " °C"), style)


def load() -> dict:
    cfg = yaml.safe_load((ROOT / "configs" / "experiments.yaml").read_text())
    pred = {c: json.loads((ROOT / "data" / "predictions" / f"chamber_{c}_summary.json").read_text())
            for c in "AB"}
    comp = json.loads((ROOT / "data" / "predictions" / "comparison_summary.json").read_text())
    chain = json.loads((ROOT / "data" / "stl" / "chain_summary.json").read_text())
    return {"cfg": cfg, "pred": pred, "comp": comp, "chain": chain}


def prep_images() -> dict[str, Path]:
    """Copy the hero renders into docs/recipe/img/ at print resolution (RGB, 1600 px wide)."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for key in ("A", "B", "AB"):
        src = ROOT / "data" / "stl" / f"hero_chamber_{key}.png"
        dst = IMG_DIR / f"hero_chamber_{key}.jpg"
        im = Image.open(src)
        bg = Image.new("RGB", im.size, (0xF8, 0xF5, 0xEF))
        bg.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
        bg.thumbnail((1600, 1600), Image.LANCZOS)
        bg.save(dst, "JPEG", quality=88, optimize=True)
        out[key] = dst
    return out


def boxed(flowables, bg, edge, width) -> Table:
    t = Table([[flowables]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1.2, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def grid_style(header_bg=CRUST, zebra=True, nrows=0) -> list:
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if zebra:
        for r in range(2, nrows, 2):
            st.append(("BACKGROUND", (0, r), (-1, r), CREAM))
    return st


def on_page(canvas, doc):
    w, h = letter
    canvas.saveState()
    # header band
    canvas.setFillColor(INK)
    canvas.rect(0, h - 0.52 * inch, w, 0.52 * inch, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(0.6 * inch, h - 0.34 * inch, "FermentAI")
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#e9d9c2"))
    canvas.drawString(0.6 * inch + 78, h - 0.335 * inch, "Sourdough  |  two-chamber experiment, trial_001")
    canvas.drawRightString(w - 0.6 * inch, h - 0.335 * inch, f"Page {doc.page}")
    # footer
    canvas.setStrokeColor(LINE)
    canvas.line(0.6 * inch, 0.55 * inch, w - 0.6 * inch, 0.55 * inch)
    canvas.setFillColor(colors.HexColor("#6b5d50"))
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(w / 2, 0.38 * inch,
                             "Prepared by FermentAI — ScienceClaw 2026 — predictions are simulated")
    canvas.restoreState()


def f1(x: float) -> str:
    return f"{x + 1e-9:.1f}"  # round half up (12.85 -> 12.9), matching the docs


def build() -> Path:
    d = load()
    cfg, pred, comp, chain = d["cfg"], d["pred"], d["comp"], d["chain"]
    rec = cfg["recipe"]["per_loaf"]
    der = cfg["recipe"]["derived"]
    ch = cfg["chambers"]
    win = cfg["window"]["observation_window_h"]
    tA = ch["A"]["temperature_schedule"]["breakpoints"][0][1]
    tB = ch["B"]["temperature_schedule"]["breakpoints"][0][1]
    A, B = pred["A"], pred["B"]
    imgs = prep_images()

    doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.78 * inch, bottomMargin=0.75 * inch,
                            title="FermentAI Sourdough - the recipe behind the predicted slices",
                            author="FermentAI (ScienceClaw 2026)",
                            subject="Two-chamber sourdough recipe with simulated predictions")
    W = doc.width
    story: list = []

    # ============================================================ PAGE 1
    story += [
        P("FermentAI Sourdough", H1),
        P("The recipe behind the predicted slices: two identical mini-loaves, two fermentation chambers", SUB),
        P(f"Mix one dough twice. Ferment one at <b>{tA:.0f} °C</b> (Chamber A) and the other at "
          f"<b>{tB:.0f} °C</b> (Chamber B), then bake, slice and taste them blind. FermentAI's model "
          "predicts how each will rise and sour, and what its crumb might look like. This sheet tells you what "
          "to do, what the model expects and when, and where to write down what really happened.", BODY),
        Spacer(1, 8),
    ]

    # Ingredient table + derived numbers side by side
    ing_rows = [
        [P("Ingredient (per loaf)", CELL_W), P("Weight", CELL_W)],
        [P("Bread flour", CELL), P(f"{rec['bread_flour_g']:.0f} g", CELL)],
        [P("Water", CELL), P(f"{rec['water_g']:.0f} g", CELL)],
        [P(f"Ripe starter ({rec['starter_hydration_percent']:.0f} % hydration)", CELL),
         P(f"{rec['starter_g']:.0f} g", CELL)],
        [P("Salt", CELL), P(f"{rec['salt_g']:.1f} g", CELL)],
        [P("<b>Total dough</b>", CELL), P(f"<b>~{der['total_dough_g']:.0f} g</b>", CELL)],
    ]
    ing = Table(ing_rows, colWidths=[2.25 * inch, 0.95 * inch])
    ing.setStyle(TableStyle(grid_style(nrows=len(ing_rows))))
    der_rows = [
        [P("Baker's math", CELL_W), P("", CELL_W)],
        [P("Total flour (incl. starter)", CELL), P(f"{der['total_flour_g']:.0f} g", CELL)],
        [P("Total water (incl. starter)", CELL), P(f"{der['total_water_g']:.0f} g", CELL)],
        [P("Hydration", CELL), P(f"{der['baker_hydration_percent']:.1f} %", CELL)],
        [P("Salt, % of flour", CELL), P(f"{der['salt_percent_of_flour']:.1f} %", CELL)],
        [P("Starter, % of dough", CELL), P(f"{der['starter_inoculation_percent_of_dough']:.1f} %", CELL)],
    ]
    dert = Table(der_rows, colWidths=[2.15 * inch, 0.85 * inch])
    dert.setStyle(TableStyle(grid_style(nrows=len(der_rows)) + [("SPAN", (0, 0), (1, 0))]))
    pair = Table([[ing, dert]], colWidths=[W * 0.52, W * 0.48])
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [P("Ingredients: make this twice (one dough per chamber)", H2), pair,
              Spacer(1, 3),
              P("Both doughs must be identical: same flour, hydration, starter, feeding history, inoculation and "
                "mixing. The only planned difference is the chamber temperature.", SMALL)]

    # Chamber plan side by side
    def chamber_cell(cid, col):
        c = ch[cid]
        hdr = Table([[P(f"Chamber {cid}: {tA if cid == 'A' else tB:.0f} °C", CELL_W)]], colWidths=[W / 2 - 8])
        hdr.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)]))
        return [hdr, Spacer(1, 3),
                P(f"<b>{c['name'].split(' - ')[1].capitalize()}.</b> {c['hypothesis']}", SMALL), Spacer(1, 2),
                P(f"<i>Caveat:</i> {c['caveat']}", TINY)]
    plan = Table([[chamber_cell("A", A_COL), chamber_cell("B", B_COL)]], colWidths=[W / 2, W / 2])
    plan.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story += [P("The two chambers", H2), plan,
              Spacer(1, 2),
              P(f"Planned observation window: {win[0]:.0f}–{win[1]:.0f} h, covering fermentation and proofing "
                "as appropriate. Both setpoints are <b>hypotheses</b>, not guaranteed flavour outcomes.", SMALL)]

    # Safety / caveats
    starter_age = cfg["starter_readiness_checklist"]["starter_age_days"]
    caveats = [
        P("<b>Before you bake: safety and caveats</b>", H3),
        P(f"• <b>Young starter.</b> The starter is only {starter_age.split(' ')[0]} days old "
          f"({starter_age.split('(')[1].rstrip(')')}). Do not start until it passes the readiness "
          "checklist on page 2.", SMALL),
        P("• <b>One experimental pH probe.</b> There is a single probe and permanent immersion is "
          "experimental. Take readings from a separate, non-edible sample if the probe is not food-contact "
          "certified; calibrate and clean it before trusting readings in thick dough. pH will not be "
          "continuous for both chambers.", SMALL),
        P("• <b>Printed crumb specimens are display-only.</b> The 3D-printed resin slices are never "
          "food-safe: keep them away from bread, dough and starter.", SMALL),
        P("• <b>Temperatures are hypotheses.</b> Warmer is expected to be more acidic, but flavour is not "
          "guaranteed, and a long cool ferment can still turn sharply sour.", SMALL),
        P("• <b>Every timing and crumb number in this sheet is a model prediction, not a measurement.</b> "
          "Judge the dough by eye and touch; a human makes the bake-readiness call.", SMALL),
    ]
    story += [Spacer(1, 8), boxed(caveats, WARN_BG, WARN_EDGE, W)]

    # ============================================================ PAGE 2: starter + method
    story.append(PageBreak())
    chk = cfg["starter_readiness_checklist"]
    def checkbox() -> Table:
        box = Table([[""]], colWidths=[9], rowHeights=[9])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.9, INK)]))
        return box

    crit_rows = [[checkbox(), P(c["description"], CELL)] for c in chk["criteria"]]
    crit = Table(crit_rows, colWidths=[0.3 * inch, W - 0.3 * inch])
    crit.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    cont = chk["if_not_ready"]["contingency"]
    story += [
        P("Step 0: is the starter ready?", H2),
        P("Tick every box before mixing. Check over at least two consecutive feeds.", SMALL), Spacer(1, 3),
        crit, Spacer(1, 5),
        boxed([P(f"<b>If it is not ready — {cont['label']}</b>", SMALL),
               P(f"Why it matters: {chk['if_not_ready']['risk']}", TINY), Spacer(1, 2)]
              + [P(f"{i}. {o}", SMALL) for i, o in enumerate(cont["options"], 1)],
              WARN_BG, WARN_EDGE, W),
    ]

    steps = [
        ("Feed the starter.", "Feed the mother starter and confirm it doubles within about 6–8 h before "
         "relying on it."),
        ("Mix two identical doughs.", f"For each dough combine {rec['bread_flour_g']:.0f} g flour, "
         f"{rec['water_g']:.0f} g water, {rec['starter_g']:.0f} g ripe starter and {rec['salt_g']:.1f} g salt. "
         "Same ingredients, same order, same mixing for both. Label them A and B."),
        ("Rest.", "Cover and rest 20–30 min."),
        ("Fold.", "Develop the gluten with several sets of folds over the first 1–2 h."),
        ("Into the chambers.", f"Put each dough in its own covered, food-safe vessel: A into the "
         f"{tA:.0f} °C chamber, B into the {tB:.0f} °C chamber. Where helpful, put a small "
         "representative sample in view of the side camera for rise measurement. Note the clock time: this is "
         "<b>hour 0</b> on page 3 and in the bake log."),
        ("Log as you go.", "Record chamber temperature, rise and periodic pH (single probe, separate non-edible "
         "sample) in the bake log on page 5. Note every time a chamber door is opened."),
        ("Watch the dough, not the clock.", "Use the page 3 table as a guide only. End bulk fermentation, or "
         "lower the temperature, when the rise plateaus or starts to fall. Do not hold the setpoint blindly; "
         "Chamber B is the one most at risk of overproofing."),
        ("Shape and proof.", "Shape each loaf and proof it when it is ready (domed, airy and jiggly, springs "
         "back slowly), not by elapsed time alone."),
        ("Bake.", "About 450 °F / 232 °C: covered for 18–20 min, then uncovered for roughly "
         "15–20 min. Adjust for the mini-loaf size and your oven. Keep the loaves labelled."),
        ("Cool, slice, photograph, taste.", "Cool fully before slicing. Slice each loaf through the middle, "
         "photograph the cut face next to the predicted slice (page 4), then hold a blind tasting of sourness, "
         "texture and aroma."),
    ]
    rows = [[P(f"<b>{i}</b>", ParagraphStyle("n", parent=CELL_B, fontSize=11, textColor=CRUST)),
             P(f"<b>{t}</b> {b}", CELL)] for i, (t, b) in enumerate(steps, 1)]
    meth = Table(rows, colWidths=[0.4 * inch, W - 0.4 * inch])
    meth.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [P("Method", H2), meth]

    # ============================================================ PAGE 3: what to expect
    story.append(PageBreak())
    story += [
        P("What to expect, and when to act", H2),
        boxed([P("<b>Model prediction, not a measurement.</b> Times are hours after the doughs enter their "
                 f"chambers (hour 0), from a {A['n_uncertainty_samples']}-sample simulation (provenance: "
                 f"<i>{A['provenance']}</i>). Real dough, flour and a young starter can run faster or slower: "
                 "act on what you see.", SMALL)], SIM_BG, A_COL, W),
        Spacer(1, 6),
    ]
    hdrA = P(f"Chamber A — {tA:.0f} °C", CELL_W)
    hdrB = P(f"Chamber B — {tB:.0f} °C", CELL_W)

    def la(p, t):
        return f"{p[f'lactic_at_{t}h']:.1f} / {p[f'acetic_at_{t}h']:.2f}"

    exp_rows = [
        [P("Predicted milestone", CELL_W), hdrA, hdrB],
        [P("Dough has doubled (2×)", CELL), P(f"~{f1(A['time_to_double_h'])} h", CELL),
         P(f"~{f1(B['time_to_double_h'])} h", CELL)],
        [P("Peak rise", CELL), P(f"~{A['peak_expansion_ratio']:.2f}× at ~{f1(A['peak_expansion_time_h'])} h", CELL),
         P(f"~{B['peak_expansion_ratio']:.2f}× at ~{f1(B['peak_expansion_time_h'])} h", CELL)],
        [P("Overproof risk begins (5 % of the rise lost)", CELL), P(f"~{f1(A['overproof_onset_h'])} h", CELL),
         P(f"<b>~{f1(B['overproof_onset_h'])} h</b>", CELL)],
        [P("pH at 12 h / 16 h", CELL), P(f"{A['ph_at_12h']:.2f} / {A['ph_at_16h']:.2f}", CELL),
         P(f"{B['ph_at_12h']:.2f} / {B['ph_at_16h']:.2f}", CELL)],
        [P("Rise at 12 h / 16 h", CELL),
         P(f"{A['expansion_at_12h']:.2f}× / {A['expansion_at_16h']:.2f}×", CELL),
         P(f"{B['expansion_at_12h']:.2f}× / {B['expansion_at_16h']:.2f}×", CELL)],
        [P("Lactic / acetic acid at 12 h (g/kg)", CELL), P(la(A, 12), CELL), P(la(B, 12), CELL)],
        [P("Lactic / acetic acid at 16 h (g/kg)", CELL), P(la(A, 16), CELL), P(la(B, 16), CELL)],
    ]
    exp = Table(exp_rows, colWidths=[W * 0.36, W * 0.32, W * 0.32])
    st = grid_style(nrows=len(exp_rows))
    st += [("BACKGROUND", (1, 0), (1, 0), A_COL), ("BACKGROUND", (2, 0), (2, 0), B_COL)]
    exp.setStyle(TableStyle(st))
    story += [exp, Spacer(1, 4),
              P(f"Paired comparison over the same {comp['n_samples']} parameter draws: B is more acidic at 12 h in "
                f"{comp['fraction_samples_B_lower_ph_at_12h'] * 100:.0f} % of draws (median pH difference "
                f"{comp['ph_12h_B_minus_A_median']:+.2f}, 5–95 % range "
                f"{comp['ph_12h_B_minus_A_p05_p95'][0]:+.2f} to {comp['ph_12h_B_minus_A_p05_p95'][1]:+.2f}) and "
                f"peaks earlier in {comp['fraction_samples_B_peaks_earlier'] * 100:.0f} % (median "
                f"{abs(comp['peak_time_B_minus_A_median_h']):.1f} h earlier). The direction is robust in the "
                "model; the size of the difference is not.", TINY)]

    def act_col(cid, col, items):
        hdr = Table([[P(f"Chamber {cid}: what to do", CELL_W)]], colWidths=[W / 2 - 8])
        hdr.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)]))
        return [hdr, Spacer(1, 3)] + [P(f"• {s}", SMALL) for s in items]

    a_items = [
        f"Start checking rise closely from ~{f1(A['time_to_double_h'] - 1)} h; expect it to double around "
        f"~{f1(A['time_to_double_h'])} h.",
        f"Peak predicted around ~{f1(A['peak_expansion_time_h'])} h. When the rise plateaus, end bulk and "
        "shape.",
        f"<b>Shape before ~{f1(A['overproof_onset_h'])} h</b>: after that the model predicts the dough starts "
        f"losing rise (down to {A['expansion_at_16h']:.2f}× by 16 h).",
        "Holding 22 °C needs no planned temperature change.",
    ]
    b_items = [
        f"Start checking from ~{f1(B['time_to_double_h'] - 1)} h; expect it to double around "
        f"~{f1(B['time_to_double_h'])} h and peak at ~{f1(B['peak_expansion_time_h'])} h.",
        f"<b>Holding {tB:.0f} °C for the full {win[0]:.0f}–{win[1]:.0f} h window is predicted to "
        f"overproof B</b> (rise lost from ~{f1(B['overproof_onset_h'])} h; {B['expansion_at_16h']:.2f}× "
        "by 16 h).",
        f"<b>Recommended:</b> cool B to {B_COOL_MAIN['to_c']} °C at ~{B_COOL_MAIN['at_h']} h. Model: "
        f"overproof delayed to ~{B_COOL_MAIN['overproof_h']} h, pH {B_COOL_MAIN['ph16']:.2f} at 16 h.",
        f"Gentler option: cool to {B_COOL_ALT['to_c']} °C at ~{B_COOL_ALT['at_h']} h (model: pH "
        f"{B_COOL_ALT['ph16']:.2f} at 16 h, no overproof signal within 16 h), at the cost of less acidity.",
        "Or end bulk and shape as soon as the rise peaks. Log any change in the bake log.",
    ]
    acts = Table([[act_col("A", A_COL, a_items), act_col("B", B_COL, b_items)]], colWidths=[W / 2, W / 2])
    acts.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story += [Spacer(1, 8), acts, Spacer(1, 6),
              P("Source of the cooling interventions: docs/scientific_assumptions.md, section 5 (model "
                "examples). The chamber plan in configs/experiments.yaml holds the setpoints constant; any change "
                "is a human or agent decision made while watching the real rise. Model predictions, not "
                "measurements.", TINY)]

    # ============================================================ PAGE 4: expected crumb
    story.append(PageBreak())
    mA = chain["chambers"]["A"]["measured_on_mesh"]
    mB = chain["chambers"]["B"]["measured_on_mesh"]
    story += [
        P("The predicted slices", H2),
        boxed([P("<b>Simulated hypothesis — compare with your real slice photo.</b> These crumbs were "
                 "generated from the fermentation simulation through documented, assumed mapping coefficients. "
                 "Fermentation kinetics do not uniquely determine crumb structure.", SMALL)],
              SIM_BG, A_COL, W),
        Spacer(1, 6),
    ]
    ab_h = W * 1800 / 3200
    story += [RLImage(str(imgs["AB"]), width=W, height=ab_h),
              P("Predicted cut faces, Chamber A (left) and Chamber B (right). Rendered from the simulated meshes.",
                CAP), Spacer(1, 6)]
    half = W / 2 - 6
    sh = half * 1800 / 3200

    def crumb_cell(cid, m, col):
        hdr = Table([[P(f"Chamber {cid}", CELL_W)]], colWidths=[half])
        hdr.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)]))
        return [hdr, RLImage(str(imgs[cid]), width=half, height=sh),
                P(f"Cut-face porosity <b>{m['cut_face_pore_area_fraction'] * 100:.0f} %</b> · "
                  f"<b>{m['cut_face_pore_count']}</b> pores on the cut face "
                  f"({m['cut_face_pore_density_per_cm2']:.2f} per cm<super>2</super>)", CAP)]
    crumbs = Table([[crumb_cell("A", mA, A_COL), crumb_cell("B", mB, B_COL)]], colWidths=[W / 2, W / 2])
    crumbs.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [crumbs, Spacer(1, 6),
              P(f"What the model expects: A, the cooler and slower dough, keeps more pores and a higher cut-face "
                f"porosity; B, fermented warmer and closer to overproof, ends with fewer pores "
                f"({mB['cut_face_pore_count']} vs {mA['cut_face_pore_count']}) and a lower porosity. "
                "Numbers are measured on the simulated mesh (data/stl/chain_summary.json).", SMALL),
              Spacer(1, 4),
              P("<b>How to compare:</b> slice each cooled loaf through the middle, photograph the cut face flat-on "
                "with even light and a ruler in frame, label it A or B, and set it next to the matching image "
                "above. Note anything that differs (open vs tight crumb, big tunnels, dense gummy layer).", SMALL)]

    # ============================================================ PAGE 5: bake log
    story.append(PageBreak())
    story += [P("Bake log", H2),
              P("Hour 0 = doughs into chambers. Log at least every hour, plus at every fold, temperature change, "
                "shaping and bake.", SMALL), Spacer(1, 4),
              P("Hour 0 clock time: ________________ &nbsp;&nbsp; Date: ________________ &nbsp;&nbsp; "
                "Baker: ________________", SMALL), Spacer(1, 5)]
    cols = ["Clock\ntime", "Hour", "Chamber\n°C", "Starter /\ndough °C", "Rise /\nheight", "pH",
            "Door\nopen?", "Notes"]
    cw = [0.6, 0.45, 0.68, 0.78, 0.65, 0.45, 0.55]
    cw = [c * inch for c in cw]
    cw.append(W - sum(cw))

    def log_table(cid, col, nrows):
        hdr_style = ParagraphStyle("lh", parent=CELL_W, fontSize=7.8, leading=9)
        rows = [[P(f"Chamber {cid} — {tA if cid == 'A' else tB:.0f} °C start", CELL_W)] + [""] * 7,
                [P(c.replace("\n", "<br/>"), hdr_style) for c in cols]]
        rows += [[""] * 8 for _ in range(nrows)]
        t = Table(rows, colWidths=cw, rowHeights=[16, 22] + [17] * nrows)
        t.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 1), col),
            ("GRID", (0, 1), (-1, -1), 0.5, LINE), ("BOX", (0, 0), (-1, -1), 0.8, col),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ] + [("BACKGROUND", (0, r), (-1, r), CREAM) for r in range(3, nrows + 2, 2)]))
        return t

    story += [log_table("A", A_COL, 9), Spacer(1, 7), log_table("B", B_COL, 9), Spacer(1, 8)]
    def ruled(label, n=2):
        rows = [[P(label, SMALL)]] + [[""] for _ in range(n)]
        t = Table(rows, colWidths=[W - 20], rowHeights=[14] + [20] * n)
        t.setStyle(TableStyle([("LINEBELOW", (0, 1), (-1, -1), 0.6, colors.HexColor("#9c8a76")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        return t

    taste = [
        P("<b>Blind tasting</b> — type these into the dashboard's <i>\"What did the bread taste like?\"</i> "
          "panel (free text, plus optional notes per loaf).", SMALL),
        ruled("<b>Loaf A:</b> sourness / texture / aroma"),
        ruled("<b>Loaf B:</b> sourness / texture / aroma"),
        Spacer(1, 4),
        ruled("<b>Which was more sour? &nbsp;A &nbsp;/ &nbsp;B</b> &nbsp;&nbsp;&nbsp; Judge: ____________________ "
              "&nbsp;&nbsp;&nbsp; What would you change next time?", 1),
    ]
    story.append(KeepTogether(boxed(taste, CREAM, CRUST, W)))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return PDF_PATH


if __name__ == "__main__":
    print(build())
