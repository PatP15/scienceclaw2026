# Pitch figures: sources and how to rebuild them

To rebuild everything, run `uv run python scripts/make_pitch_figures.py`. For one figure, add
`--only <name>`. The output is deterministic: two runs give byte-identical PNG and SVG files. Every
number on a figure is read from the data files at build time. The Brev counts, hours and cost are
parsed from `data/alchemi/environment/report.md`.

All figures are 16 × 9 in. The PNGs are 3200 × 1800 px, which is 2× a 1600 × 900 slide. The SVGs keep
text as outlines, so they render the same in Keynote, Google Slides or a browser. The font is DejaVu
Sans, which ships with matplotlib, so other machines produce the same output.

| Figure | Slide | What it shows | Data it reads |
|---|---|---|---|
| `architecture` | 2 (inset or backup) | The Mac hub connects over a local Wi-Fi AP to two ESP32 chambers and the iPad. Brev GPU and Claude sit in a dashed "internet · optional" band. Banner: "Demo needs no internet". | none (PLAN §2, §8) |
| `closed_loop` | 1 | An eight-step loop: Bake → Judge tastes → Parse → Compare → Agents debate → Publish (chef-hats) → Ferment + measure → Recalibrate. The judge node is larger and crimson. Each node has a HUMAN, AI AGENT, SIMULATED or PHYSICAL badge. | none (conceptual) |
| `agents_exchange` | backup / Q&A | A mock-up of hypothesis (A) → critique (B) → agreed schedules, using the real `Finding` field names. Badged **EXAMPLE · illustrative, not a logged run**. | none; B's critique quotes the simulated "cool at 6 h" result in `docs/scientific_assumptions.md` §5 |
| `evidence_pipeline` | 2 | Five stages: ALCHEMI (REAL·GPU, real kcal/mol numbers) → kinetics (SIMULATED) → morphology map (SIMULATED, assumed mapping) → chamber specimens A/B (SIMULATED, crops of the real renders) → loaf photos (REAL·AFTER BAKE, dashed because still pending). A bracket says it "constrains the acid chemistry (…) — does not drive the bread". The text in the parentheses is generated from `molecular_coupling_report.json`. Three footer lines give ALCHEMI provenance with the totals for both campaigns, the relative pKa (−3.9 ± 1.7 vs −0.9, cluster-phase), and the mild/sour baseline. | `data/alchemi/analysis/{association_energies.csv, deprotonation_energies.csv, analysis_summary.json}`, `data/predictions/{chamber_*_summary.json, molecular_coupling_report.json}`, `data/stl/{chain_summary.json, crumb_chamber_comparison.png, crumb_summary.json}` |
| `results_ab` | 4 (backup) | A two-panel A vs B plot: pH with 5–95 % bands and the 16 h values, and rise with peak and overproof markers plus the dashed "B cooled to 22 °C at 6 h" curve. Badged **SIMULATED**. | `data/predictions/chamber_{A,B}_{prediction,uncertainty}.csv`, `*_summary.json`. The intervention curve is re-simulated with `fermentai.fermentation.simulation`. |
| `benchmark_summary` | 4 | Four tiles: 3.9/4 rounds changed, 3.1 → 0 same-round duplicates, +0.68 nats (24/30 worlds), and "no change" in the final recommendation. Below them, a dot + 95 % CI plot of pooled information (talking − independent) for the deployed policy (+0.68) and the info-aware selector (−0.13). Badged **IN-SILICO BENCHMARK**. | `data/benchmarks/summary.json` (`well_specified`, `paired_final`), `data/benchmarks/benchmark_results.csv` (final round) |
| `tech_highlights` | 3 | Two panels with text wordmarks only, no logos. **NVIDIA ALCHEMI** (REAL · GPU) shows the totals across both campaigns (1,066 optimisations · 79 vibrational analyses · 80 MD runs) and an inset re-plot of relative pKa (lactic − acetic) against cluster size n = 0/3/6/10, with the −3.9 ± 1.7 estimate band and the −0.9 experimental line. Below that: the acetic dimer −15.3 kcal/mol vs literature −14…−16; tiles for ~10 h / ~$6 / offline; and a data-driven "model nudge only" line (potency, pKa shift, max pH Δ). **Formlabs PreForm API** has GEOMETRY · SIMULATED and PRINT PREP · REAL badges; the step chips; "426 real PreForm API calls recorded; /v1/scene/print never called"; the hero render next to PreForm's render with rafts; tiles for 1.36 h, 150 mL, 185 layers and 7 checks (cup warnings A 1 · B 3); and a footer for printer, material and SDK/server. | NVIDIA: `data/alchemi/analysis/{c2_summary.json, analysis_summary.json}` (ΔpKa by n = `thermo_cycles.dd_boltzmann_kcal` × the analysis's own 1/(RT ln 10)), counts, hours and cost parsed from `data/alchemi/environment/report.md`, and `data/predictions/molecular_coupling_report.json`. Formlabs: `data/stl/hero_chamber_AB.png` (cropped) and `data/stl/print/{print_manifest.json, preform_api_transcript.json, crumb_chambers_AB_form4-grey-v5_iso.png}`. |

## Visual rules

- Chamber A is `#1f77b4` (blue) and Chamber B is `#ff7f0e` (orange). The pair passes the dataviz
  palette validator for CVD separation. Orange has low contrast against white, so every orange mark
  also has a direct text label.
- Badges are defined once, in `BADGES` in the script:
  - REAL / PHYSICAL is green.
  - SIMULATED / IN-SILICO is purple.
  - REPLAY / EXAMPLE is grey.
  - AI AGENT is slate.
  - HUMAN is crimson.
  - A dashed, hollow badge means the evidence is real but not collected yet.
