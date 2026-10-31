# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE (pending GPU numbers)
- [x] scripts/make_pitch_figures.py — 5 figures, PNG 3200x1800 + SVG each; byte-identical reruns; ruff clean
- [x] data/figures/{closed_loop,evidence_pipeline,architecture,results_ab,agents_exchange}.{png,svg} — all viewed, overlaps fixed
- [x] docs/pitch/slides.md — 4 slides; notes 392 words = 2:48 @140 wpm
- [x] docs/pitch/figure_sources.md
- [x] docs/demo_script.md — 0:00–3:00 table, fallbacks, real-vs-simulated line (~270 spoken words)

## Next
- GPU numbers: fill ALCHEMI_PLACEHOLDERS in scripts/make_pitch_figures.py, re-run; replace the same
  "[ALCHEMI: … — pending]" strings in docs/pitch/slides.md (slide 3) and docs/demo_script.md (1:00 row).
- When src/fermentai/dashboard/static/js/taste.js lands: rehearse and fix step names at 1:50 of demo script.

## Resume
uv run python scripts/make_pitch_figures.py [--only results_ab ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w     # notes word count
