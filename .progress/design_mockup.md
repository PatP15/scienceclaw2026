# Checkpoint: product mockup (data/design/)

## Status: COMPLETE (not committed; pending orchestrator/user review)
- [x] context read (README, PLAN_MASTER §1/§5, SENSOR_WIRING, GPIO_MAP, hero/closed_loop figures)
- [x] design canvas: https://claude.ai/artifact/GxTEVat8DfByxUbL4xYFA3 (private) — artboards Main (hero, A/B tweak),
      Callouts (A/B tweak, live HTML labels), Context (phone tile, accent tweak), Specs
- [x] data/design/make_mockup.py -> sensor_module_mockup.{svg,png} (3200x2160), sensor_module_context.{svg,png},
      assets/*.svg (text-free canvas art; --assets-png rasterises), canvas/ mirror of .dc.html files
- [x] 3 self-critique rounds (overflow/clipping, label collisions, port strip, hat scale, header chip meaning,
      jar scale + rise band, pH 3.9 copy, box top/side shading)
- [x] data/design/README.md (concept, materials, real-vs-concept table, how to iterate)

## Resume
python3 data/design/make_mockup.py [--assets-png]   # headless Chrome render; cairosvg fallback
Canvas art change: re-upload assets PNGs to the artifact, repoint /_blob ids in canvas/*.dc.html.
