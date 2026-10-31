# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE (final pass with real ALCHEMI + chain + benchmark data)
- [x] scripts/make_pitch_figures.py — 6 figures (PNG 3200x1800 + SVG), every number read from data files;
      byte-identical reruns; ruff clean; no matplotlib warnings
- [x] evidence_pipeline: real ALCHEMI numbers (from CSVs), chamber_A/B specimens, mild/sour as footnote
- [x] benchmark_summary (new): in-silico tiles + paired-difference CI plot
- [x] docs/pitch/slides.md — 403 spoken words = 2:53 @140 wpm; numbers/sources table
- [x] docs/pitch/figure_sources.md
- [x] docs/demo_script.md — UI names, data-mode indicator, Online Agent/Offline Policy, demo_04 replay,
      `uv run python -m simulated_devices --chambers A B`; ~272 spoken words

## Next
- Rehearse demo against the committed dashboard (data-mode indicator / replay-from-API were uncommitted at
  writing time).
- If firmware is verified on hardware, update "compiled, hardware-unverified" in slides.md + demo_script.md.

## Resume
uv run python scripts/make_pitch_figures.py [--only evidence_pipeline benchmark_summary ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w
