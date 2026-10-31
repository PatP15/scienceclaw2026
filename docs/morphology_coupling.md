# Morphology coupling: from the simulation chain to printed crumb specimens

The printed crumb specimens `data/stl/crumb_chamber_A.stl` and `crumb_chamber_B.stl` are derived
from the simulation chain, not from hand-picked presets. The original `crumb_mild.stl` and
`crumb_sour.stl` illustrative presets are still generated, byte-for-byte unchanged, as a baseline.

Code: `src/fermentai/geometry/morphology_map.py`. CLI: `uv run python scripts/generate_stls.py`
builds both families. Use `--from-simulation` for the chamber specimens only, or `--presets-only` for
mild/sour only. Tests: `tests/test_geometry_morphology_map.py`.

> **This is a hypothesis.** Fermentation kinetics, and sourness in particular, do not uniquely
> determine crumb structure. Mixing, flour, hydration, shaping, scoring, oven spring and baking all
> matter at least as much. Every mapping magnitude below is an assumption, to be replaced by fits to
> real loaf photographs (see §6).

## 1. The chain and its provenance

| Step | What | Provenance |
|---|---|---|
| 1 | ALCHEMI/AIMNet2 acid–water cluster descriptors (`data/alchemi/analysis/molecular_descriptors.json`) | **real** (AIMNet2 via nvalchemi-toolkit, NVIDIA RTX A6000, checkpoint `aimnet2_wb97m_d3_3`) |
| 2 | `kinetics.apply_molecular_descriptors` nudges `undissociated_lactic_rel_potency` from 0.10 to 0.1237 (status `partial`; pKa values stay at literature values) | alchemi-informed, bounded |
| 3 | `simulation.simulate` with that parameter, using each chamber's schedule from `chamber_<id>_morphology.json`, then `summarize_for_morphology` at the window end, t = 16 h | simulated |
| 4 | `morphology_map.map_to_crumb_params` | **assumption**: documented, monotone, bounded |
| 5 | `crumb.generate_crumb`, then STL, `validate_mesh` and the render | simulated (seeded) |

`data/predictions/chamber_{A,B}_morphology.json` is written by `scripts/simulate_loaves.py` from
the **literature-parameter** run. The ALCHEMI-informed run is only stored as a CSV, rounded to 5
significant figures. `kinetics_inputs()` therefore re-runs the ODE in both variants, using the same
schedule, horizon (24 h) and step (0.05 h). The literature re-run reproduces the JSON exactly
(max |Δ| = 0), and that check is recorded in `chain_summary.json`. The printed specimens use the
**ALCHEMI-informed** variant.

## 2. Inputs (ALCHEMI-informed kinetics, t = 16 h, simulated)

| Input | A (22 °C) | B (27 °C) | ALCHEMI − literature (A / B) |
|---|---|---|---|
| `co2_retained_g_per_kg` | 1.540 | 1.261 | −5e-6 / −4e-5 |
| `co2_retained_peak_g_per_kg` | 1.748 | 1.688 | −3e-4 / −4e-4 |
| `expansion_ratio_peak` | 2.106 | 2.086 | −2e-4 / −3e-4 |
| `expansion_ratio_final` | 1.974 | 1.811 | −3e-6 / −3e-5 |
| `max_expansion_rate_per_h` | 0.205 | 0.263 | −1.5e-4 / −2.5e-4 |
| `overproof_flag` | True (onset 12.85 h) | True (onset 9.5 h) | 0 / 0 |
| `expansion_fall_from_peak_fraction` | 0.119 | 0.253 | −1.4e-4 / −1.6e-4 |
| `final_ph` | 4.125 | 3.899 | −1.4e-4 / −1.4e-3 |
| `gluten_weakening_index` | 0.489 | 0.768 | +1.6e-5 / +7e-5 |
| `final_fermentation_quotient` | 3.58 | 4.08 | +1e-5 / +3e-4 (not mapped) |

Both chambers are past their overproof onset at 16 h. The A/B contrast is therefore one of
*degree*: B is more collapsed, more acidified and faster-rising. It is not "proofed vs overproofed".

## 3. The mapping

`u(x; lo, hi) = clip((x − lo)/(hi − lo), 0, 1)`. Every driver lies in [0, 1], so every output is
bounded by construction and monotone (non-decreasing or non-increasing) in each input. Coefficients
live in `MAPPING_CONFIG`. Each has a value, a unit and a `source`, and each can be overridden with
`map_to_crumb_params(inputs, config={"porosity_max": 0.55})`. Unknown keys raise an error, and the
wall floor can never be set below 0.9 mm.

**Drivers**

| Driver | Formula | A | B |
|---|---|---|---|
| gas_fraction | 1 − 1/expansion_ratio_final (volume identity: the rise is all gas) | 0.494 | 0.448 |
| s_gas | 0.6·u(gas_fraction; 0.25, 0.60) + 0.4·u(CO₂ retained; 0.5, 2.5 g/kg) | 0.626 | 0.491 |
| s_rate | u(max rise rate; 0.10, 0.35 /h) | 0.421 | 0.653 |
| s_gwi | gluten_weakening_index | 0.489 | 0.768 |
| s_collapse | 0.35·[overproof] + 0.65·u(fall from peak; 0, 0.40) | 0.543 | 0.761 |
| s_acid | u(5.0 − pH; 0, 1.5) | 0.583 | 0.734 |
| s_size | (0.40·s_rate + 0.35·s_gwi + 0.25·s_collapse) | 0.475 | 0.721 |
| s_irreg | (s_rate + s_gwi)/2 | 0.455 | 0.711 |
| s_weak | (s_acid + s_gwi)/2 | 0.536 | 0.751 |

**Outputs** (`CrumbParams`, rounded to 1e-4)

| Parameter | Formula | Bounds | Direction | A | B |
|---|---|---|---|---|---|
| `porosity_target` (cut-face pore-area fraction) | 0.20 + 0.30·s_gas | 0.20–0.50 | ↑ rise, ↑ retained CO₂ | **0.3876** | **0.3474** |
| pore diameter median (mm) | 0.9 + 2.3·s_size | 0.9–3.2 | ↑ rate, ↑ GWI, ↑ collapse | 1.9925 | 2.5571 |
| pore diameter CV | 0.30 + 0.50·s_collapse | 0.30–0.80 | ↑ collapse | 0.571 | 0.681 |
| `pore_diam_mean_mm` / `_sd_mm` | lognormal: mean = median·√(1+CV²), sd = CV·mean | — | — | **2.295 / 1.311** | **3.093 / 2.105** |
| `pore_diam_max_mm` | min(12, max(4 + 8·s_collapse, 2.5·median)) | 4–12 | ↑ collapse | 8.34 | 10.09 |
| `eccentricity` | 0.10 + 0.70·s_irreg | 0.10–0.80 | ↑ rate, ↑ GWI | **0.418** | **0.598** |
| `eccentricity_jitter` | 0.25 + 0.35·s_irreg | 0.25–0.60 | ↑ rate, ↑ GWI | 0.409 | 0.499 |
| `clustering` (merged cells) | 0.05 + 0.70·s_collapse | 0.05–0.75 | ↑ overproof, ↑ fall | **0.430** | **0.583** |
| `cluster_radius_mm` | 3.0 + 2.0·s_collapse | 3–5 | ↑ collapse | 4.09 | 4.52 |
| `cluster_spacing_mm` | 14 + 4·s_collapse | 14–18 | ↑ collapse | 16.17 | 17.04 |
| `min_wall_mm` | max(0.9, 0.9 + 0.6·(1 − s_weak)) | **0.9**–1.5 | ↓ lower pH, ↓ higher GWI | **1.178** | **1.049** |
| `seed` | fixed 20261031 for both chambers (common random numbers) | — | — | 20261031 | 20261031 |

`final_fermentation_quotient` is recorded but **not mapped**. `CrumbParams` has no
anisotropy parameter, and the lactic:acetic ratio has no established direct effect on crumb
geometry at this resolution. Peak expansion and peak retained CO₂ enter only through the
fall-from-peak and end-of-window values.

**Sources for the directions.** All magnitudes are assumptions.
- More retained gas and rise give a more open crumb. This is a volume identity plus general
  bread-crumb knowledge.
- Faster gas production and weakened gluten give larger, more irregular cells. Over-proofing and
  collapse give coalesced cells and a long tail of large holes. General direction from Scanlon &
  Zghal (2001), *Food Res Int* 34:841–864, and Gan, Ellis & Schofield (1995), *J Cereal Sci*
  21:215–230 (gas-cell stabilisation, coalescence).
- Acid-activated proteolysis weakens gluten. Gänzle, Loponen & Gobbetti (2008), *Trends Food Sci
  Technol* 19:513–521, which already underlies the GWI (docs/scientific_assumptions.md §10).
- The citations were recalled from memory and used only for *direction*. They were not re-checked
  against the papers during this work.

The bounds and coefficients were chosen once, before generation. A and B came out visibly
different without widening the mapping.

## 4. Result

| | A (22 °C) | B (27 °C) | B − A |
|---|---|---|---|
| Measured cut-face pore fraction | 0.387 | 0.343 | −0.044 |
| Cut-face pores (count) | 92 | 53 | −39 |
| Mean / p90 section diameter (mm) | 3.73 / 7.70 | 4.35 / 10.11 | +0.61 mean |
| 3D void fraction | 0.057 | 0.089 | |
| Triangles / STL size | 442,476 / 22.1 MB | 98,498 / 4.9 MB | |
| Validation (10 checks) | all pass | all pass | |

Reading of the hypothesis: Chamber A at 22 °C holds more gas at 16 h, giving a somewhat more open
but finer crumb with more, smaller cells. Chamber B at 27 °C rose faster, has more acid-weakened
gluten and has fallen further from its peak. That gives a slightly less open crumb dominated by
fewer, larger, irregular and merged cells, with thinner walls.

Measured section diameters exceed the generator's sphere-equivalent mean. This is because
clustered pores merge after the wall-thickness opening.

A's mesh is not decimated. `manifold3d` simplification succeeds, but `crumb._clean` then removes a
degenerate face, so `_simplify` falls back to the valid full-resolution mesh. The result is still
watertight, still validates and is within the 25 MB STL budget.

Outputs in `data/stl/`:
- `crumb_chamber_{A,B}.stl`
- `_validation.json`
- `_mapping.json`: MappingReport with every input, driver, formula, bound, coefficient source and a
  provenance chain `{alchemi, simulated, assumption}` per output
- `crumb_chamber_comparison.png`
- `chain_summary.json`: per chamber, the STL SHA-256, the kinetics numbers used plus the
  literature counterfactual, ALCHEMI descriptor provenance and attribution, the mapped parameters
  and measured statistics

## 5. How much of the geometry traces to ALCHEMI vs kinetics vs assumption

- **ALCHEMI** moved one kinetic parameter (lactic potency 0.10 → 0.124). The mapped crumb
  parameters changed by at most **0.067 %** (B eccentricity −0.0004; porosity_target unchanged
  at 1e-4). The measured meshes changed by ≤ 0.0002 in cut-face pore fraction, against an A–B
  difference of 0.044. Pore counts changed by ≤ 2.
  - A's mean section diameter shifts by 0.05 mm. That is the stochastic generator's sensitivity
    to tiny parameter changes (pore merging), not a signal.
  - In plain terms, ALCHEMI is traceably *in* the chain but is not what makes A and B different.
- **Kinetics** (temperature schedule → simulated rise, gas, acidification, GWI) produces all of
  the A/B contrast: 22 °C vs 27 °C.
- **Assumption** sets the scale. The reference ranges, weights and bounds in `MAPPING_CONFIG`
  determine how big the A/B geometric contrast is. Halve a span and the contrast halves. The
  generator's fixed parameters (slab size, crust, voxel, seed) are also assumptions.

## 6. Limitations and how a real photograph updates the comparison

- Sourness, pH and the fermentation quotient do not determine crumb. The mapping ignores mixing,
  shaping, hydration, flour, oven spring, baking and slicing. No mapping coefficient has been
  fitted to data.
- The kinetic inputs are themselves simulated, with uncertainty envelopes not propagated here.
  Only the nominal run is mapped.
- The GWI is a phenomenological index, not rheology. The volume identity `1 − 1/E` ignores
  dissolved CO₂ and initial occluded air.
- Cut-face pore-area fraction from a photo depends on lighting and threshold. The generator
  calibrates to the same quantity, measured the same way on its mesh heightmap.

**Testing against a loaf.** Photograph each cross-section square-on, with a ruler in frame, then:

```python
from fermentai.geometry.image_analysis import analyze_crumb_image, compare_to_params
from fermentai.geometry.morphology_map import MorphologyInputs, map_to_crumb_params
params, _ = map_to_crumb_params(MorphologyInputs.from_json("data/predictions/chamber_A_morphology.json"))
meas = analyze_crumb_image("photos/loaf_A.jpg", scale_mm_per_px=0.05)   # provenance imported_human
cmp_ = compare_to_params(meas, params)
# cmp_["pore_area_fraction"]["difference"], cmp_["mean_section_diameter_mm"]["ratio"],
# cmp_["suggested_params"] -> the porosity / pore size the photo implies
```

A photo updates the hypothesis in this order:
1. Report the observed-minus-predicted pore fraction and the section-diameter ratio per chamber.
2. Check whether the **sign** of B − A matches. The predicted signs are: B lower pore fraction,
   larger mean section diameter, fewer pores.
3. With several loaves, refit `porosity_min/max`, `pore_median_min/max` and the weights in
   `MAPPING_CONFIG`, using `config=` overrides first, then new defaults with
   `source: fitted to <photos>`.

A single loaf pair cannot separate the mapping from loaf-to-loaf variability.

## 7. Reproduce

```bash
uv run python scripts/generate_stls.py                    # presets + chamber specimens (~60 s)
uv run python scripts/generate_stls.py --from-simulation  # chamber specimens only (~35 s)
uv run pytest tests/test_geometry_*.py                    # ~60 s
```

The output is deterministic: the mapped parameters are rounded to 1e-4 and the seed is fixed. It
re-reads the molecular descriptors on every run. If the ALCHEMI file changes, for example when
deprotonation energies arrive and the pKa nudge applies, the specimens and the attribution update
automatically.

## 8. Change made to `crumb.py`

`crumb._clean` now ends with `return trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)`
instead of `return mesh`. With trimesh 5.2, `trimesh.repair.fix_normals` flips the winding of the
inward-oriented marching-cubes mesh without invalidating the cached `face_normals`.

Whenever `_simplify` did not rebuild the mesh, the exported STL facet normals were all inverted
relative to the (correct) winding, and the oblique render showed the slab inside-out. This first
appeared with Chamber A.

The mild/sour outputs were unaffected and are regenerated byte-identical. A regression test is
`test_stl_facet_normals_match_winding_without_simplify`.
