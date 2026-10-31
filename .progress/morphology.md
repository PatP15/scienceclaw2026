# Morphology-coupling progress (simulation-derived crumb specimens) — COMPLETE

## Done
- src/fermentai/geometry/morphology_map.py: MorphologyInputs (from_json / from_summary, safe
  fallbacks), kinetics_inputs (re-runs literature + ALCHEMI-informed ODE; literature reproduces the
  morphology JSON exactly), MAPPING_CONFIG (every coefficient has value/unit/source),
  map_to_crumb_params -> (CrumbParams, MappingReport), attribution (ALCHEMI vs literature deltas),
  generate_chamber_specimen, generate_chain, render_chamber_comparison.
- scripts/generate_stls.py: default = presets + chamber specimens; --from-simulation; --presets-only.
  mild/sour outputs regenerate byte-identical.
- data/stl/: crumb_chamber_{A,B}.stl, _validation.json, _mapping.json, crumb_chamber_comparison.png,
  chain_summary.json. Both validate (10/10 checks).
- docs/morphology_coupling.md (chain, numbers, mapping table, attribution, limitations, photo test).
- tests/test_geometry_morphology_map.py (monotonicity, bounds, determinism, fallbacks, validation,
  A != B, normals regression). `uv run pytest tests/test_geometry_*.py`: 37 passed (~60 s).
- Bug fix in crumb._clean (stale inverted face_normals after fix_normals when simplify falls back);
  documented in docs/morphology_coupling.md §8.

- Hero renders (pitch deck): src/fermentai/geometry/hero.py; `generate_stls.py --hero` ->
  data/stl/hero_chamber_{A,B,AB}.png, hero_mild_sour.png (3200x1800); tests/test_geometry_hero.py.
- docs: mapped vs measured median pore size note (order reverses; report measured).

## Next (optional)
- Fit MAPPING_CONFIG to real loaf photos (analyze_crumb_image + compare_to_params).
- Propagate kinetics uncertainty (map p05/p95 runs -> parameter ranges).
- crumb._simplify falls back for chamber A (one degenerate face removed by _clean -> not watertight);
  A ships un-decimated (22 MB). Could repair the single hole instead of falling back.

## Resume
- `uv run python scripts/generate_stls.py --from-simulation` ; `... --hero` ; `uv run pytest tests/test_geometry_*.py`
