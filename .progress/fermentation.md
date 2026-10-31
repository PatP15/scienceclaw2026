# Fermentation simulator — progress checkpoint

Owner area: src/fermentai/fermentation/, configs/kinetics.yaml, configs/experiments.yaml,
scripts/simulate_loaves.py, data/predictions/, docs/scientific_assumptions.md,
tests/test_fermentation_*.py

## Done
- Read PLAN.md / AGENTS.md / README.md; designed model (see below).
- configs/kinetics.yaml + configs/experiments.yaml (citations verified via Europe PMC).
- kinetics.py, simulation.py, scenarios.py, calibration.py — working & tuned
  (final: 22C peak 2.11x @9.4h, pH16h 4.13, overproof 12.8h; 27C peak 2.09x @7.2h, pH16h 3.91, overproof 9.5h).

- plotting.py (polished, dataviz palette A=#2a78d6 B=#eb6834), sensitivity.py,
  scripts/simulate_loaves.py -> data/predictions/ (30 files, ~6 s). Visually checked PNGs.

- Expansion sub-model reformulated (retention efficiency + rupture only above capacity) so
  cooling stabilises instead of deflating; overproof signal = fall from peak (rise at 25C ref).
- tests: 28 passing (tests/test_fermentation_{model,schedule,calibration}.py).
- Paired MC comparison -> data/predictions/comparison_summary.json.

- SCOPE ADD (coordinator): upstream ALCHEMI hook kinetics.apply_molecular_descriptors (+ new
  param undissociated_lactic_rel_potency, explicit `source:` on pKa/hac params, molecular_coupling
  config block) and downstream co2_retained / gluten_weakening_index columns +
  simulation.summarize_for_morphology; script writes chamber_{A,B}_morphology.json and
  molecular_coupling_report.json (+ *_alchemi_informed.csv when descriptors exist). Real descriptor
  file present -> status partial (potency 0.10->0.124; no deprot data -> pKa literature).
  tests: 40 passing (added tests/test_fermentation_coupling.py). Docs §9-§10 added.

- FOLLOW-UP (campaign 2): apply_molecular_descriptors rewritten: prefers top-level
  relative_pka_estimate (c2), c2 Boltzmann assoc-per-water (mean over common n) > c2 lowest > c1;
  same-field-for-both-acids rule (fixes null acetic deprot_n6 no-op); report records field+campaign
  and relative-pKa comparison to experiment. Real file -> status applied: potency 0.10->0.127,
  pKa 3.835/4.785 (cap binds; raw shift -0.30). Effect: A pH12 -0.007, pH16 -0.009; B -0.011/-0.013;
  peak/overproof times unchanged. 44 tests pass. Docs §9 rewritten.

## Next
- DONE. docs/scientific_assumptions.md written. Remaining optional work: calibrate against real
  pH/rise data once available (calibrate(observations, schedule=None, free_params=[...])).

## Design notes
- States: substrate, yeast, lab, lactic_acid, acetic_acid, co2_cumulative (+aux gas_retained,
  gluten_integrity for expansion). Units g/kg dough; biomass relative (initial = 1).
- CTMI (Rosso 1993) temperature; Luedeking-Piret acid production; Presser-type pH inhibition;
  charge-balance buffered pH with lumped flour buffer groups.

## Resume
`uv run pytest tests/test_fermentation_*.py`; `uv run python scripts/simulate_loaves.py`
