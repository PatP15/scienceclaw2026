# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE — investor framing (4 slides) + 7 figures
- [x] scripts/make_pitch_figures.py — 7 figures (PNG 3200x1800 + SVG); numbers read from data files;
      byte-identical reruns; ruff clean; no matplotlib warnings
- [x] tech_highlights (new): NVIDIA ALCHEMI | Formlabs PreForm panels, text wordmarks only
- [x] docs/pitch/slides.md — investor deck: 391 spoken words = 2:48 @140 wpm
- [x] docs/demo_script.md — investor intro; ~274 spoken words
- [x] docs/pitch/figure_sources.md

## Pending placeholders
- PREFORM_ESTIMATES in scripts/make_pitch_figures.py + slide 3 bullet: print time / resin volume
- [ASK — team to fill] on slide 4 (bullet + notes)
- data/stl/hero_chamber_AB.png: picked up automatically by tech_highlights when it exists

## Next
- When the Formlabs agent reports estimates (.progress/formlabs.md / docs/formlabs_workflow.md): fill
  PREFORM_ESTIMATES, re-run, update slide 3.
- Rehearse demo against the committed dashboard build.

## Resume
uv run python scripts/make_pitch_figures.py [--only tech_highlights ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w
