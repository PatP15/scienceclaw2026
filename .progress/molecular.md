# Molecular / ALCHEMI workstream checkpoint

Updated: see campaign 2 section -- campaign 1 COMPLETE

## Brev instance
- fermentai-gpu, hyperstack_A6000 (1x RTX A6000 48GB), $0.60/hr. Created 03:36:39Z.
- hyperstack does NOT support `brev stop` ("does not support stop"); after rsync dry-run showed 0 differences,
  the instance was DELETED at 04:44:01Z (gone 04:44:50Z). `brev ls`: no instances.
- Instance time 1.14 h -> est. $0.68 (cap $120).

## Done
- P0 env/nvidia-smi; P1 official example 08 reproduced (after Warp +cu12 fix); selftest + chargetest.
- P2/P3: 159 optimisations (57 neutral, 48 anion, 53 ion-pair v2, H3O+), all converged (fmax<0.01 eV/A).
- P4: analysis (CSV, JSON, molecular_descriptors.json, Plotly HTML self-contained, PNGs).
- P5: data/alchemi/environment/report.md + pip_freeze.txt; docs/simulation_provenance.md.
- P6: 72 NVT Langevin runs (6 clusters x 4 T x 3 seeds), all completed.
- Tests: uv run pytest tests/test_molecular_*.py -> 22 passed.

## How to resume / re-run
- Mac-only (no GPU): `uv run python -m fermentai.molecular.analysis` regenerates data/alchemi/analysis/.
- New GPU work needs a new instance: `brev create fermentai-gpu --type hyperstack_A6000`, then
  scripts/setup_brev.sh, scripts/run_remote_alchemi.sh <mode>, scripts/stop_brev.sh --delete-if-unstoppable.

## CAMPAIGN 2 (budget $30, wall <= ~4 h) -- IN PROGRESS
- Instance fermentai-gpu (hyperstack_A6000, $0.60/h) created 2026-10-31T04:59:48Z, ready 05:08Z, setup OK 05:09Z (RUNNING).
  NOT stoppable -> delete at end after rsync dry-run shows 0 diffs.
- Plan: inputs -> data/alchemi/inputs_c2/, results -> data/alchemi/results_c2/ (+ vib_c2/, md_long/), additive only.
- Priorities: 1 relative pKa cycle + acetic dimer; 2 sampling (40 starts + anneal/quench n=3,6);
  3 n=10,15; 4 cosolutes; 5 20 ps MD; 6 descriptors/docs/figures/tests.
- Existing descriptor fields are kept unchanged; new fields added (kinetics reads assoc_energy_per_water_kcal,
  deprot_energy_n6_kcal(/separated fallback), hbond_min_A).
- 05:10Z smoke tests OK (batched optimize + vib; archived in data/alchemi/logs/tests/c2_smoke_*).
- 05:13Z RUNNING: optimize 607 c2 structures, 6 shards (logs optimize_s{0..5}_*.log) -> results_c2/
- 05:14Z RUNNING: mdlong 8 procs (4 n=6 clusters x 295/310 K, 3 replicas x 20 ps; c1 minima as starts) -> md_long/
- NEXT: when optimize done -> Mac: write data/alchemi/c2_jobs/anneal_*.json (top-5 per species n=3,6), run anneal;
  then vib job lists (top-5 within 3 kcal/mol per species n=0,3,6,10 + water + hydronium), run vib shards;
  then analysis (uv run python -m fermentai.molecular.analysis), docs, report, rsync dry-run, brev delete.
- Code: src/fermentai/molecular/{structures_c2,campaign2}.py, remote/run_alchemi_c2.py, tests/test_molecular_campaign2.py (32 tests pass).

### Campaign 2 resume (12:33Z, after a usage-limit gap; instance sat idle ~7 h)
- Instance fermentai-gpu vjdcojwux RUNNING since 04:59:48Z (est. $0.60/h; ~7.6 h = ~$4.5 at 12:33Z).
- DONE on remote and collected: 607/607 c2 optimisations converged (16 re-run after a GPU OOM at 05:20Z, failed records
  in logs/failed/c2_oom_optimize/), 8/8 md_long runs (2 re-run after OOM; logs/failed/c2_oom_mdlong/).
- 12:35Z RUNNING: anneal (4 shards, c2_jobs/anneal_*.json, 8 groups x top-5) + vib n=0,10 (3 shards, c2_jobs/vib_n0n10_*.json).
- NEXT: vib for n=3,6 after anneal (uv run python -m fermentai.molecular.campaign2 vib --ns 3 6 --tag _n3n6),
  analysis, docs/report, rsync dry-run, brev delete.
- 12:49Z anneal n=3,6 DONE (200 quench minima, all converged); vib n=0,10 DONE (30) and n=3,6 DONE (36); 66 vib total, 0 failed.
- Dedupe fix: vib minima duplicates (|dE|<0.05 kcal & fingerprint RMS<0.02 A) removed before Boltzmann (else -RT ln k bias).
- Interim headline: dpKa(lactic-acetic) = -1.93 +- 3.91 (n=6: -4.65, n=10: +0.79) vs exp -0.90. acetic dimer -15.26 kcal/mol.
- QA fixes: strict JSON (no NaN) via analysis.dump_json; top-level provenance strings in c2_summary.json / c2_md_long_curves.json.
- ~13:00Z RUNNING: anneal n=10 (4 procs, c2_jobs/anneal_n10_*.json). NEXT: vib n=10 refresh (--ns 10 --tag _n10b), analysis, docs, delete.
- 13:45Z FINAL ANALYSIS DONE (n=10 anneal + 13 extra vib included): dpKa(lactic-acetic) = -3.89 +- 1.73 vs exp -0.90
  (n=0 -9.3, n=3 -5.7, n=6 -4.65, n=10 -3.12); acetic dimer -15.26 kcal/mol (lit -14..-16). 907 c2 minima, 79 vib, 8 md_long.
- Docs/report/descriptors/figures regenerated; tests: 36 passed.
- !!! INSTANCE STILL RUNNING (vjdcojwux) !!! SSH gateway unreachable since ~13:20Z (banner-exchange timeouts), so the
  zero-diff rsync dry-run could not run; `brev delete` was blocked by the session permission policy pending a human
  decision. Evidence that the Mac copy is complete: data/alchemi/environment/c2_release_note.md.
  TO RESUME: brev refresh; if ssh works -> scripts/collect_brev_results.sh; rsync -azn ... (see note) ; brev delete fermentai-gpu.
  If ssh never returns -> human decides `brev delete fermentai-gpu` (billing ~$0.60/h meanwhile).
