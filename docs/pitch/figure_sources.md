# Pitch figures: sources and how to rebuild them

To rebuild everything, run `uv run python scripts/make_pitch_figures.py`. For one figure, add
`--only <name>`. The output is deterministic: two runs give byte-identical PNG and SVG files. Every
number on a figure is read from the data files at build time.

**Few-words rule.** The figures carry the slides:

- about 15 visible words per figure or fewer;
- names of 2 words or fewer;
- one number per tile;
- one-word badges (MEASURED / COMPUTED / MODELED / EXAMPLE);
- no sentences, footers or sources, all of which live in `docs/pitch/slides.md`;
- text of at least 22 pt on the 16 × 9 in canvas, so at least ~37 px when a figure spans 1920 px.

The PNGs are 3200 × 1800. The SVGs keep text as outlines. The font is DejaVu Sans, which ships with
matplotlib.

| Figure | Slide | Visible text (words, incl. tick labels) | Data it reads |
|---|---|---|---|
| `closed_loop` | 1 | Bake · Taste · Parse · Compare · Debate · Publish · Ferment · Recalibrate, plus the centre "Taste → next experiment" (13). Ring colours: dashed green = physical steps (MEASURED, pending), purple = MODELED, slate = AI agent, crimson = human | none (conceptual) |
| `evidence_pipeline` | 2 (backup, Q&A) | NVIDIA ALCHEMI / Kinetics model / Crumb map / Printed specimens / Loaf photos, with badges COMPUTED / MODELED ×3 / COMPUTED (dashed = after bake), "acid terms only", and A / B (21) | `data/stl/crumb_chamber_comparison.png` (specimen crops) |
| `architecture` | 2 | A four-panel brigade strip, 4 arrows, no crossings. (1) Guest tastes: the guest says "more sour, still airy" to Sous Chef A, who takes notes (the chat). (2) Head Chef → line cooks: A hands the note up; the Head Chef's "goal" arrow goes down to a grid of line-cook pans, "17 simulations". (3) Sous chefs argue at the pass: result tickets on the pass counter between A and B; tailed bubbles "32 °C, then cool" and "overproof: 24 °C"; "at the pass · on the Mac"; Pantry jars ALCHEMI · COMPUTED and forecasts · MODELED. (4) Agreed trial → stations: a "trial 002" ticket → Station A / B (dashed green, lock), MEASURED · pending. About 56 tokens as counted | "17" = `len(fermentai.agents.schedules.LIBRARY)` (read at build time); trial 002 is from `data/demos/demo_04_master_plan_judge_script.json` |
| `scale` | not in deck (roadmap / Q&A) | "one kitchen → many kitchens": one larger kitchen tile (sous chefs A/B at a local pass, line cooks, two locked stations) and three identical smaller tiles, all hanging from one bar labelled "shared pass · replicated findings log". Above the bar: an "executive chef" chip (merges model updates) and a "pantry · ALCHEMI cache" chip (teal). 14 words | none (concept) |
| `dataflow` | 2 (backup, Q&A; not in deck) | Left chain: NVIDIA ALCHEMI [COMPUTED] → "ΔpKa, hydration, H-bond" → Fermentation model [MODELED] (side: "recipe, schedule, starter →") → "pH, CO₂, rise, overproof" → Morphology map [MODELED] → "porosity, pore size, clustering" → Crumb generator [MODELED] → "STL" → PreForm API [COMPUTED] → ".form" → Form 4 print (dashed). Right, the physical loop (dashed green, MEASURED · pending): Judges taste → "free text" → Agents A/B → "new schedule" → model; Chambers A/B → "temp, RH, gas, height" → recalibrate the model; Bake → Photo analysis → "porosity, pore size" ↔ compare with the morphology map (58 words, of which 9 are badge words) | none |
| `tech_highlights` | 3 | Three panels, about 25 words each or fewer. **NVIDIA ALCHEMI · COMPUTED**, "which acid is stronger?": a pKa scale (◄ stronger acid · pKa · weaker acid ►, ticks 0–6) with experimental lactic and acetic as solid dots, GPU lactic (acetic exp + ΔpKa, n = 3/6/10) as hollow dots labelled "GPU, more water →", a "sourdough pH" band at 3.9–4.3, and tiles "1,066 runs" and "~$6". **Fermentation model · MODELED**: recipe, schedule, starter and acid terms (green arrow from NVIDIA) → ODE kinetics (yeast, bacteria, sugar, acids) → pH, lactic/acetic, CO₂ and rise, each with tiny A/B curves, plus overproof with A/B markers. **Formlabs PreForm API · COMPUTED**: hero render (MODELED), the PreForm Clear V5 render, and tiles "2.4 h", "239 mL", "Clear V5" | `data/alchemi/analysis/c2_summary.json` (ΔpKa by n), experimental pKa 3.86 / 4.76 (`c2_summary.relative_pka_estimate.experiment_pka`), `data/alchemi/environment/report.md`; `data/predictions/chamber_{A,B}_prediction.csv`, `*_summary.json`; `data/stl/hero_chamber_AB.png`, `data/stl/print/clear_form4_build1_{manifest.json, iso.png}` |
| `benchmark_summary` | 4 | MODELED + "3.9 / 4 choices changed", "3.1 → 0 duplicate experiments", "+0.68 nats more information", "same final pick" (17) | `data/benchmarks/summary.json`, `benchmark_results.csv` |
| `results_ab` | 4 (backup) | MODELED, panel titles pH / Rise, line labels A / B, "B more acidic", "B overproofs ~3 h earlier", "hours" (17) | `data/predictions/chamber_{A,B}_{prediction,uncertainty}.csv`, `*_summary.json` |
| `agents_exchange` | Q&A | EXAMPLE, "A proposes" / "B critiques" / "Agreed", "27 °C", "27 → 22 °C", A / B, with mini schedules (13) | none (illustrative; numbers from `docs/scientific_assumptions.md` §5) |

## What the figures no longer say

These are in `docs/pitch/slides.md`, either in the speaker notes or in the numbers table:

- **ALCHEMI:** −3.9 ± 1.7 vs −0.9, the dimer −15.3 kcal/mol, 79 vibrational analyses, 80 MD runs,
  ~10 GPU-hours, and the model nudge (potency 0.10 → 0.127, pKa ±0.025, pH Δ ≤ 0.013).
- **PreForm:** 800 API calls and no print call, cups A 1 / B 2, and that the Clear V5 plate includes
  the two chef hats.
- **Simulation:** the pH values 4.13 / 3.90, lactic 4.7 / 6.5 g/kg, the cooled-B intervention, and
  the info-aware selector's −0.13.
- **Specimens:** the porosity and pore counts, and the mild/sour baseline.
- **Safety:** live/offline modes and setpoint clamping.

## Visual rules

- Chamber A is `#1f77b4` (blue) and Chamber B is `#ff7f0e` (orange), with direct text labels on every
  orange mark.
- Badges are defined once, in `BADGES` in the script:
  - MEASURED is green (physical world). It is drawn dashed or hollow as "MEASURED · pending" while nothing is measured.
  - COMPUTED is teal `#00897b` (a calculation that ran, with logs: ALCHEMI, PreForm).
  - MODELED is purple (our own model: forecasts, crumb geometry, benchmark).
  - EXAMPLE is grey.
  - AI is slate.
  - HUMAN is crimson.
