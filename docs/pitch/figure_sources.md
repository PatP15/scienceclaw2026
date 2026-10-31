# Pitch figures: sources and how to rebuild them

To rebuild everything, run `uv run python scripts/make_pitch_figures.py`. For one figure, add
`--only <name>`. The output is deterministic: two runs give byte-identical PNG and SVG files. Every
number on a figure is read from the data files at build time.

**Few-words rule.** The figures carry the slides:

- about 15 visible words per figure or fewer;
- names of 2 words or fewer;
- one number per tile;
- one-word badges (REAL / SIMULATED / EXAMPLE);
- no sentences, footers or sources, all of which live in `docs/pitch/slides.md`;
- text of at least 22 pt on the 16 × 9 in canvas, so at least ~37 px when a figure spans 1920 px.

The PNGs are 3200 × 1800. The SVGs keep text as outlines. The font is DejaVu Sans, which ships with
matplotlib.

| Figure | Slide | Visible text (words, incl. tick labels) | Data it reads |
|---|---|---|---|
| `closed_loop` | 1 | Bake · Taste · Parse · Compare · Debate · Publish · Ferment · Recalibrate, plus the centre "Taste → next experiment" (13). Ring colours: green physical, purple simulated, slate AI, crimson human | none (conceptual) |
| `evidence_pipeline` | 2 | NVIDIA ALCHEMI / Kinetics model / Crumb map / Printed specimens / Loaf photos, with badges REAL / SIMULATED ×3 / REAL (dashed = after bake), "acid terms only", and A / B (21) | `data/stl/crumb_chamber_comparison.png` (specimen crops) |
| `architecture` | 2 (backup) | optional · Brev GPU · Claude · Mac · no internet needed · Wi-Fi AP · Chamber A · iPad · Chamber B (18) | none |
| `tech_highlights` | 3 | NVIDIA ALCHEMI REAL + ΔpKa-vs-waters plot with "experiment" + "1,066 runs", "~$6"; Formlabs PreForm API REAL + SIMULATED (on the hero render) + "2.4 h", "239 mL", "Clear V5" (20) | `data/alchemi/analysis/c2_summary.json`, `data/alchemi/environment/report.md` (runs, cost), `data/stl/hero_chamber_AB.png`, `data/stl/print/clear_form4_build1_{manifest.json, iso.png}` |
| `benchmark_summary` | 4 | SIMULATED + "3.9 / 4 choices changed", "3.1 → 0 duplicate experiments", "+0.68 nats more information", "same final pick" (17) | `data/benchmarks/summary.json`, `benchmark_results.csv` |
| `results_ab` | 4 (backup) | SIMULATED, panel titles pH / Rise, line labels A / B, "B more acidic", "B overproofs ~3 h earlier", "hours" (17) | `data/predictions/chamber_{A,B}_{prediction,uncertainty}.csv`, `*_summary.json` |
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
  - REAL / PHYSICAL is green.
  - SIMULATED is purple.
  - EXAMPLE is grey.
  - AI is slate.
  - HUMAN is crimson.
  - A dashed, hollow badge means real but not yet collected.
