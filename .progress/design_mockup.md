# Checkpoint: product mockup (data/design/)

## Status: COMPLETE — v3 cute character delivered (not committed)
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

## v2 black colourway
- THEMES dict in make_mockup.py (cream / black / black_anodised); main renders black (+assets) and cream (*_cream)
- canvas v6: all 4 artboards repointed to black assets + new CapOptions artboard (dark walnut chosen)
- critique: fixed f-string bug (tether), toned ring light to smoked silver, grille lip, swatch, softer shadows, leader halos

## v3 cute character (current)
- Node = Clawd-style terracotta critter in the chef hat: camera eye + status eye (wink), smile vent, apron e-ink,
  pH hand (BNC), tail probe, 4 feet, magnetic back; same ports underneath, 120x80x35 body
- main PNGs = character; v1 cream + v2 black kept as *_cream / *_black (re-rendered by the same script)
- canvas v7: Main (chamber + expression tweaks), Callouts (8 labels), Context, Specs, Moods (replaced CapOptions)
- critique rounds: label/tail collision, back-view overlap, black hat puck -> clay, capped probe tip,
  softer cables, rounded plug relief, neat context cable, smooth leg/arm extrusions
