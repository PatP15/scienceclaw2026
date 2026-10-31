# ALCHEMI environment report

Generated 2026-10-31T13:50:20Z by `fermentai.molecular.alchemi_runner report`.
Provenance of all numbers below: **real** (read from files produced on the Brev GPU instance).

## Hardware / instance
- Brev instance: `fermentai-gpu`, type `hyperstack_A6000`, listed price $0.60/hr (`brev search`)
- GPU: NVIDIA RTX A6000 (compute capability 8.6, 50.91 GB)
- NVIDIA driver 570.195.03, driver CUDA version 12.8
- OS/platform: Linux-6.8.0-90-generic-x86_64-with-glibc2.35

## Software
- Python 3.12.16
- PyTorch 2.10.0 (CUDA 12.8, cuDNN 91002)
- NVIDIA ALCHEMI Toolkit `nvalchemi-toolkit` 0.2.0; `nvalchemi-toolkit-ops` 0.5.0
- NVIDIA Warp `warp-lang` 1.18.0+cu12 (the `+cu12` build; see note below)
- `aimnet` 0.2.5, ASE 3.29.0, PhysicsNeMo 2.2.2
- Full lockfile: `data/alchemi/environment/pip_freeze.txt`

## Model
- AIMNet2 checkpoint: `aimnet2_wb97m_d3_3`, sha256 `0c5f26b9de15c72a9ccb1d411b3b7814816bb06cca46f784dccd1f5efb2c334b`, 2205593 parameters, AEV cutoff 5.0 Å
- Downloaded automatically by `aimnet` from `https://storage.googleapis.com/aimnetcentral/aimnet2v2/AIMNet2/aimnet2_wb97m_d3_3.pt`
- Composition: `PipelineModelWrapper([[AIMNet2Wrapper, EwaldModelWrapper] (shared autograd), [DFTD3ModelWrapper]])`, D3(BJ) {'s6': 1.0, 'a1': 0.566, 's8': 0.3908, 'a2': 3.128}

## Validation
- Official example `08_aimnet2_ewald_pipeline.py` reproduced on the GPU: see `official_example/`.
- Selftest all finite: True; ALCHEMI pipeline vs upstream `aimnet` calculator water__n1__c0: ΔE=-0.21 meV, max|ΔF|=1.0e-04 eV/Å, acetic_acid__n0__c0: ΔE=-0.52 meV, max|ΔF|=2.8e-04 eV/Å, lactic_acid__n0__c0: ΔE=-0.39 meV, max|ΔF|=1.6e-04 eV/Å, acetic_acid__n1__c0: ΔE=+1.02 meV, max|ΔF|=1.0e-04 eV/Å

## Runtime and cost
- Instance created 2026-10-31T03:36:39Z; stopped/deleted 2026-10-31T04:44:50Z
- Elapsed instance time 1.14 h × $0.60/h ≈ **$0.68** (estimate; Brev's invoice is authoritative)
- Campaign 2 (instance vjdcojwux; STILL RUNNING at report time - SSH gateway unreachable since ~13:20Z; delete pending human decision; see environment/c2_release_note.md): instance created 2026-10-31T04:59:48Z; deleted (still running at report time); 8.84 h × $0.60/h ≈ $5.31
- **Total across sessions: 9.98 h ≈ $5.99**
- Campaign 2: 907 optimisations (907 converged), 79 vibrational analyses, 8 long-MD runs
- Sum of per-structure optimisation wall time: 21.5 min; MD wall time: 154.3 min
- Calculations: 159 optimisations (159 converged, 0 failed), 72 MD runs (72 completed)

## Notes
- PyPI `warp-lang` ≥1.10 is built with CUDA 13 and needs an R580+ driver. On this instance (driver 570.195.03, CUDA 12.8) the first run of example 08 failed with `ValueError: Invalid device identifier: cuda:0` (log preserved: `official_example/08_aimnet2_ewald_pipeline__attempt1_FAILED_warp_cu13_driver12.8.log`). Fixed per Warp's install guide by installing the `+cu12` wheel from GitHub Releases (automated in `remote/setup_env.sh`).
- hyperstack_A6000 has no stop support; campaign-1 instance deleted 04:44:01Z after an rsync dry-run with 0 differences.
- Campaign 2: ~7 h of the billed time (about 05:50Z to 12:33Z) were idle while the operator session was paused by a usage limit.
- Campaign 2: GPU out-of-memory event at 05:20Z with 14 concurrent processes; affected jobs re-run (logs/failed/c2_oom_*).
- Campaign 2 relative pKa (lactic - acetic): -3.89 +- 1.73 (cluster-phase, n=6 and 10) vs experiment -0.90; acetic acid cyclic dimer -15.26 kcal/mol vs literature -14 to -16.
- Bug found and fixed during campaign 1: v1 ion-pair builder placed the transferred proton on top of existing H in 7/53 inputs; those results are archived in data/alchemi/superseded/ionpair_v1/ and are not used.
