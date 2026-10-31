# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE — investor framing (4 slides) + 7 figures
- [x] scripts/make_pitch_figures.py — 7 figures (PNG 3200x1800 + SVG); numbers read from data files;
      byte-identical reruns; ruff clean; no matplotlib warnings
- [x] tech_highlights (new): NVIDIA ALCHEMI | Formlabs PreForm panels, text wordmarks only
- [x] docs/pitch/slides.md — investor deck: 405 spoken words = 2:54 @140 wpm
- [x] docs/demo_script.md — investor intro; ~274 spoken words
- [x] docs/pitch/figure_sources.md

## Pending placeholders
- none (relative pKa filled from c2_summary.json; ask filled on slide 4)

## Next
- molecular_coupling_report.json was being regenerated (uncommitted) at last run; figures read it live — re-run
  make_pitch_figures.py after the fermentation agent commits and re-check the slide-3 numbers table row.
- Rehearse demo against the committed dashboard build.

## Resume
uv run python scripts/make_pitch_figures.py [--only tech_highlights ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w
