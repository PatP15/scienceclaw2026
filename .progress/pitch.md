# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE — investor framing (4 slides) + 7 figures
- [x] scripts/make_pitch_figures.py — 7 figures (PNG 3200x1800 + SVG); numbers read from data files;
      byte-identical reruns; ruff clean; no matplotlib warnings
- [x] tech_highlights (new): NVIDIA ALCHEMI | Formlabs PreForm panels, text wordmarks only
- [x] docs/pitch/slides.md — investor deck: 401 spoken words = 2:52 @140 wpm
- [x] docs/demo_script.md — investor intro; ~274 spoken words
- [x] docs/pitch/figure_sources.md

## Pending placeholders
- [ALCHEMI: relative pKa — pending] — slide 3 notes only (GPU campaign 2); not on any figure
- [ASK — team to fill] on slide 4 (bullet + notes)
- Formlabs: DONE — tech_highlights reads data/stl/print/print_manifest.json + transcript (live PreForm run)

## Next
- When GPU campaign 2 lands: fill the relative-pKa clause in slide 3 notes (keep total <= 3:00).
- Rehearse demo against the committed dashboard build.

## Resume
uv run python scripts/make_pitch_figures.py [--only tech_highlights ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w
