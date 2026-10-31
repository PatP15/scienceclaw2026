# Pitch / figures checkpoint

Owner scope: docs/pitch/, docs/demo_script.md, scripts/make_pitch_figures.py, data/figures/, this file.

## Status: COMPLETE — few-words figures (7) + investor deck
- [x] scripts/make_pitch_figures.py — few-words rule (<= ~15-21 visible words incl. ticks, text >= 22 pt,
      one-word badges, no sentences/footers); byte-identical reruns; ruff clean
- [x] docs/pitch/slides.md — short on-slide bullets; notes 401 words = 2:52 @140 wpm; tech_highlights = 3 panels (NVIDIA acid scale | model | Formlabs); numbers table
- [x] docs/pitch/deck/slides/*.html — on-slide text matches slides.md; asides = notes; data-file attrs
      (re-upload all four images before republishing; partners img src is a repo path)
- [x] docs/pitch/figure_sources.md, docs/demo_script.md

- [x] badge vocabulary MEASURED (green, dashed=pending) / COMPUTED (teal #00897b) / MODELED (purple) everywhere;
      no cost on any figure; new architecture (agents-focused) + dataflow.png (Q&A only)

## Pending placeholders
- none (relative pKa filled from c2_summary.json; ask filled on slide 4)

## Next
- molecular_coupling_report.json was being regenerated (uncommitted) at last run; figures read it live — re-run
  make_pitch_figures.py after the fermentation agent commits and re-check the slide-3 numbers table row.
- Rehearse demo against the committed dashboard build.

## Resume
uv run python scripts/make_pitch_figures.py [--only tech_highlights ...]
sed -n '/^> /p' docs/pitch/slides.md | sed 's/^> //' | wc -w
