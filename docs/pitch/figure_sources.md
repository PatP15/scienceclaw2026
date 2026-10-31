# Pitch figures: sources and how to rebuild them

To rebuild everything, run `uv run python scripts/make_pitch_figures.py`. For one figure, add
`--only <name>`. The output is deterministic: two runs give byte-identical PNG and SVG files.

All figures are 16 × 9 in. The PNGs are 3200 × 1800 px, which is 2× a 1600 × 900 slide. The SVGs keep
text as outlines, so they render the same in Keynote, Google Slides or a browser. The font is DejaVu
Sans, which ships with matplotlib, so other machines produce the same output.

| Figure | Slide | What it shows | Data it reads |
|---|---|---|---|
| `data/figures/architecture.png` / `.svg` | 1 | The Mac hub connects over a local Wi-Fi AP to two ESP32 chambers and the iPad. Brev GPU and Claude sit in a dashed "internet · optional" band. Banner: "Demo needs no internet". | none; the diagram is drawn from the facts in PLAN §2 and §8 |
| `data/figures/closed_loop.png` / `.svg` | 2 | An eight-step loop: Bake → Judge tastes → Parse → Compare → Agents debate → Publish (chef-hats) → Ferment + measure → Recalibrate. The judge node is larger and crimson. Each node has a HUMAN, AI AGENT, SIMULATED or PHYSICAL badge. | none (conceptual) |
| `data/figures/agents_exchange.png` / `.svg` | 2 (backup) | A mock-up of hypothesis (A) → critique (B) → agreed schedules, using the real field names from `src/fermentai/agents/communication.py`. Badged **EXAMPLE · illustrative, not a logged run**. | none; B's critique quotes the simulated "cool at 6 h" result documented in `docs/scientific_assumptions.md` §5 |
| `data/figures/evidence_pipeline.png` / `.svg` | 3 | Five stages: ALCHEMI (REAL · GPU) → kinetics (SIMULATED) → morphology map (SIMULATED, assumed mapping) → STL specimen (SIMULATED) → loaf photos (REAL · AFTER BAKE, dashed because still pending). A bracket says ALCHEMI informs only the acid-chemistry terms. | `data/predictions/chamber_{A,B}_summary.json`, `data/stl/crumb_summary.json`, and the oblique-view crop of `data/stl/crumb_{mild,sour}.png` |
| `data/figures/results_ab.png` / `.svg` | 4 | A two-panel A vs B plot: pH with 5–95 % bands and the 16 h values, and dough rise with peak and overproof-onset markers plus the dashed "B cooled to 22 °C at 6 h" curve. Badged **SIMULATED**. | `data/predictions/chamber_{A,B}_prediction.csv`, `*_uncertainty.csv`, `*_summary.json`. The intervention curve is re-simulated with `fermentai.fermentation.simulation.simulate` at nominal parameters. |

## Visual rules

- Chamber A is `#1f77b4` (blue) and Chamber B is `#ff7f0e` (orange). The pair passes the dataviz
  palette validator for CVD separation. Orange has low contrast against white, so every orange mark
  also has a direct text label.
- Badges are defined once, in `BADGES` in the script:
  - REAL / PHYSICAL is green `#2ca02c`.
  - SIMULATED is purple `#9467bd`.
  - REPLAY / EXAMPLE is grey `#7f7f7f`.
  - AI AGENT is slate `#37474f`.
  - HUMAN is crimson `#c2185b`.
  - A dashed, hollow badge means the evidence is real but not collected yet.
- Yellow boxes are placeholders for GPU numbers that are still pending.

## Pending placeholders

Edit `ALCHEMI_PLACEHOLDERS` at the top of `scripts/make_pitch_figures.py`, then re-run. Change the
same strings in `docs/pitch/slides.md` (slide 3) and `docs/demo_script.md` (the 1:00 row).

- `[ALCHEMI: ΔE_assoc lactic vs acetic — pending]`
- `[ALCHEMI: ΔE_deprot lactic vs acetic — pending]`
