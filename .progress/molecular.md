# Molecular / ALCHEMI workstream checkpoint

Updated: 2026-10-31T04:55Z -- STATUS: COMPLETE

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
