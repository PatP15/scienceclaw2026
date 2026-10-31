# Deck PDF export (2026-10-31)

Command: `uv run --with pypdf python scripts/export_deck_pdf.py` (~13 s; headless Google Chrome, no new deps).

Outputs
- docs/pitch/FermentAI_pitch.pdf: 4 pages, 1440x810 pt (= 1920x1080 px, 16:9)
- docs/pitch/FermentAI_pitch_notes.pdf: 4 pages, 840x1080 pt (1120x1440 px): slide at half size + speaker notes from <aside>
- docs/pitch/deck/export/: per-slide HTML + single-page PDFs (temp)

Figures: blobs swapped for repo files: cover closed_loop.png, pipeline architecture.png, partners
tech_highlights.png, traction benchmark_summary.png. No slide has a data-file attribute (the README
says they do, and lists pipeline as evidence_pipeline.png; figure_sources.md and the slide alt text
both point to architecture.png, so the README is stale).

Verified by rasterising with pymupdf: all pages 1 page per slide, images present, Rubik + IBM Plex
Sans loaded from Google Fonts (embedded as Type3 by Skia), nothing clipped.

Known source issue: on partners the 64px headline wraps to 2 lines, so the fixed 810px figure
would overflow the 1080 canvas by ~31px. The export adds `img {min-height:0; flex-shrink:1}`, so the
figure shrinks to fit (aspect kept). The live Slides artifact likely still clips it; shorten the
headline or the figure height in slides/partners.html to fix the source.
