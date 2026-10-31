# Simulation provenance: NVIDIA ALCHEMI / AIMNet2 molecular calculations

This document says what was computed for FermentAI's "NVIDIA Molecular Explorer", how it was
computed, what it does and does not mean, and how to reproduce it. All numbers quoted here are copied
from files under `data/alchemi/` that were produced on a remote NVIDIA GPU (NVIDIA Brev) and copied
back to the Mac. None were typed in by hand or estimated.

Provenance label used everywhere for these results: **`real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000`**.
The starting geometries (`data/alchemi/inputs/`) are *not* ALCHEMI output. RDKit generated them
on the Mac, and they are labelled as starting structures in their manifest.

## 1. Question

Lactic acid and acetic acid are the two main organic acids that sourdough lactic acid bacteria
produce. Their ratio (the "fermentation quotient") strongly affects perceived sourness. As a
molecular-level supplement to the macroscopic fermentation model, we asked:

* How strongly does each neutral acid bind 1, 3 and 6 explicit water molecules (gas-phase
  microsolvation), and which hydrogen-bond motifs form?
* How do the two acids differ in this respect?

## 2. What ran, where

| Step | Where | Code | Output |
|---|---|---|---|
| Starting structures | Mac | `src/fermentai/molecular/structures.py` (RDKit 2026.09.1 ETKDGv3 + seeded H-bond water placement) | `data/alchemi/inputs/manifest.json`, `inputs/xyz/` |
| Environment | Brev GPU | `remote/setup_env.sh` | `data/alchemi/environment/` |
| Official example reproduction | Brev GPU | `remote/official_examples/08_aimnet2_ewald_pipeline.py` (unmodified NVIDIA example) | `data/alchemi/official_example/` |
| Self-test + cross-check | Brev GPU | `remote/run_alchemi.py selftest` | `data/alchemi/selftest/selftest.json` |
| Geometry optimisations | Brev GPU | `remote/run_alchemi.py optimize` | `data/alchemi/results/` |
| NVT molecular dynamics (optional) | Brev GPU | `remote/run_alchemi.py md` | `data/alchemi/md/` |
| Analysis + figures | Mac | `src/fermentai/molecular/analysis.py` | `data/alchemi/analysis/` |

The Mac-side scripts `scripts/setup_brev.sh`, `scripts/run_remote_alchemi.sh`,
`scripts/collect_brev_results.sh` and `scripts/stop_brev.sh` handle transfer (rsync over the
Brev-managed SSH alias `fermentai-gpu`). Every remote run writes its real stdout/stderr to
`data/alchemi/logs/<mode>_<UTC stamp>.log`. Failed attempts are kept: see
`data/alchemi/logs/failed/` and
`data/alchemi/official_example/08_aimnet2_ewald_pipeline__attempt1_FAILED_warp_cu13_driver12.8.log`.

## 3. Species and starting structures

* **L-(+)-lactic acid**, (S)-2-hydroxypropanoic acid, SMILES `C[C@@H](C(=O)O)O`. RDKit's CIP
  assignment (S) is recorded in the manifest. **Acetic acid**, SMILES `CC(=O)O`. **Water.**
* Explicit hydrogens come from RDKit `AddHs`, and 3D coordinates from ETKDGv3 with a fixed
  `randomSeed` for each structure.
* Clusters acid·(H₂O)ₙ use n = 1, 3, 6, with 6 independent seeded starting structures for each
  (acid, n). Each water is placed in turn at a hydrogen-bonding position on a randomly chosen
  polar site (seeded NumPy RNG). The site can be the acid's O atoms or acidic/hydroxyl H, or a
  previously placed water. Clashing placements are rejected. Water starts at the experimental
  gas-phase geometry (r(OH) = 0.9572 Å, ∠HOH = 104.52°).
* References: 6 ETKDG conformers of each isolated acid, one water monomer, and 4 starting
  structures each of the pure water clusters (H₂O)₃ and (H₂O)₆.
* All species are neutral closed-shell singlets (charge 0, multiplicity 1).
* Seeds come from a SHA-256 hash of `base_seed = 20261031` and the structure label, so they are
  stable across machines and Python sessions. Generation is deterministic for a given RDKit
  version; `tests/test_molecular_structures.py` checks this.

## 4. Model and method

* **Engine:** NVIDIA ALCHEMI Toolkit (`nvalchemi-toolkit` 0.2.0, `nvalchemi-toolkit-ops` 0.5.0, NVIDIA
  Warp 1.18.0+cu12) with PyTorch 2.10.0+cu128 on an NVIDIA RTX A6000.
* **Potential:** AIMNet2, checkpoint `aimnet2_wb97m_d3_3` (trained to ωB97M-D3 reference data).
  This is the checkpoint used by NVIDIA's official example 08. The `aimnet` package (0.2.5)
  downloaded it automatically.
* **Composition**, as documented by ALCHEMI: `AIMNet2Wrapper` deliberately disables AIMNet2's
  internal Coulomb and D3 terms, and the toolkit adds them back with a pipeline:

  ```
  PipelineModelWrapper(groups=[
      PipelineGroup(steps=[AIMNet2Wrapper, EwaldModelWrapper], use_autograd=True),  # charges -> Ewald, forces through both
      PipelineGroup(steps=[DFTD3ModelWrapper(s6=1.0, a1=0.5660, s8=0.3908, a2=3.1280)]),  # ωB97M-D3(BJ)
  ])
  ```

  Each molecule sits in a 60 Å periodic box, as in example 08. The Ewald accuracy is 1e-6, and
  the neighbour lists come from the toolkit's hooks. AIMNet2 runs in float32, which the wrapper
  enforces.
* **Cross-check.** `selftest.json` compares this pipeline with the upstream `aimnet`
  `AIMNet2Calculator` (gas phase, with its own Coulomb + D3) at the same geometries, and so does
  `results/crosscheck_upstream_aimnet.json` for all 159 optimised structures. Energies agree to
  ≤ 5.9 meV (mean 1.1 meV), and forces to ≤ 1.5 × 10⁻³ eV/Å. The large box and the Ewald/D3
  composition therefore reproduce the gas-phase AIMNet2 model, including its D3 parameters.
* **Optimiser:** `nvalchemi.dynamics.optimizers.fire2.FIRE2` (dt = 0.01, the official example's
  value). A run converges when max per-atom |F| < 0.01 eV/Å (5× tighter than the example's
  0.05), with at most 4000 steps. An L-BFGS fallback was configured, but `nvalchemi.dynamics.LBFGS`
  does not exist in toolkit 0.2.0 (ImportError recorded). It was only ever attempted for the two
  superseded v1 ion-pair runs. All 159 final optimisations converged with FIRE2 alone.
* **Recorded for every structure** (`data/alchemi/results/json/<id>.json`): identities and fragment
  atom ranges, seeds, charge and multiplicity, checkpoint, D3 parameters, software versions, GPU,
  initial and optimised coordinates, total energy (from an independent single point at the
  optimised geometry), per-atom forces, max force, AIMNet2 partial charges, convergence status,
  iteration count, wall time and the full per-step energy/fmax trajectory (`results/traj/<id>.csv`).
  XYZ files: `results/xyz/<id>_initial.xyz` and `<id>_optimized.xyz`.

## 4b. Conjugate bases and ion pairs (stage 2)

Added at the coordinator's request so the fermentation model can get a *relative* acidity signal.
These structures are derived on the Mac (`structures.py --derived-from data/alchemi/results/json`) from
the **optimised neutral ALCHEMI geometries**. They keep the same seeds and conformer index as the
neutral cluster they came from:

* `<acid>_anion__n<n>__c<k>`: carboxylate (lactate⁻ / acetate⁻) with n waters, charge −1. The
  carboxylic H of the optimised acid·(H₂O)ₙ is deleted (n = 0 uses each optimised acid conformer).
* `<acid>_ionpair__n<n>__c<k>`: contact ion pair A⁻·H₃O⁺·(H₂O)ₙ₋₁, charge 0, the same composition as
  acid·(H₂O)ₙ. The carboxylic proton moves to the nearest water O.
* `<acid>_ionpairSS__n<n>__c<k>`: solvent-separated ion pair. The proton is relayed one water further
  along an existing H-bond wire (Grotthuss-style), so a neutral water sits between carboxylate and
  H₃O⁺ (n ≥ 3, when such a wire exists).
* `hydronium__n0__c0`: H₃O⁺, charge +1.

The total charge is passed explicitly to AIMNet2 (`AtomicData.charge`, and AIMNet2's charge
equilibration conserves it: Σq = −1.000 / +1.000 e).

**Charged species in a periodic box.** Ewald summation of a charged system in a cubic box of side L
adds the Makov–Payne monopole self-image term, E_MP = −q²α_M/(2L) with α_M = 2.837297 (−0.3405 eV at
L = 60 Å for |q| = 1). `data/alchemi/selftest/chargetest.json` shows that for acetate⁻ and H₃O⁺ at
L = 40–180 Å, E_ALCHEMI − E_gas(upstream aimnet) = E_MP to ≤ 1 meV, and forces are unaffected
(≤ 1e-3 eV/Å). For every structure, `energy_ev` therefore stores the gas-phase energy
E_periodic − E_MP; the raw value is kept as `energy_periodic_raw_ev`. Each optimised structure is also
re-evaluated with the upstream gas-phase `aimnet` calculator (`crosscheck_delta_ev`).

**Final protonation state.** An optimisation may move the proton back. Each H is assigned to its
nearest O, and each optimised structure is classified as `neutral_acid`, `anion`,
`contact_ion_pair` (H₃O⁺ O within 3.0 Å of a carboxylate O) or `solvent_separated_ion_pair`
(`analysis/ionic_states.csv`). Only structures that are still ion pairs after optimisation enter
ΔE_deprot,IP.

## 5. Derived quantities (Mac, `analysis.py`)

* **Association energy:** ΔE_assoc(n) = E(acid·nH₂O) − E(acid) − n·E(H₂O). E(acid) is the
  lowest converged energy among that acid's isolated conformers, and E(H₂O) is the optimised
  monomer. Both use exactly the same model and settings as the clusters.
* **Acid-to-water-network energy:** E(acid·nH₂O) − E(acid) − E((H₂O)ₙ), with E((H₂O)ₙ) the lowest
  converged pure water cluster (n = 3, 6).
* **Deprotonation energy, ion pair at matched composition:**
  ΔE_deprot,IP(n) = E(A⁻·H₃O⁺·(H₂O)ₙ₋₁) − E(HA·(H₂O)ₙ). Both are the lowest-energy converged
  structures, and the ion pair must still be an ion pair. The value is null, with a reason, when
  every ion-pair start relaxed back to the neutral acid.
* **Deprotonation energy, separated ions:** ΔE_deprot,sep(n) = E(A⁻·(H₂O)ₙ) + E(H₃O⁺) −
  E(HA·(H₂O)ₙ) − E(H₂O). All energies are gas-phase.
* **`molecular_descriptors.json`** (read by the fermentation model): per acid, the mean association
  energy per water, ΔE by n, the deprotonation energies, the shortest acid–water H···O distance,
  `lactic_minus_acetic` differences, and a provenance block.
* **Hydrogen bonds** (geometric criterion): donor O–H ≤ 1.25 Å, d(O···O) ≤ 3.5 Å,
  d(H···O) ≤ 2.5 Å, ∠O–H···O ≥ 120°. Each bond is classified as acid→water, water→acid,
  water→water or intramolecular, and by acid site (carbonyl O, carboxyl OH, α-hydroxyl OH).

## 6. What these results do and do not imply

These are **0 K electronic (potential) energies** of small **gas-phase** clusters from a machine-
learned surrogate of ωB97M-D3. They are not free energies and include no zero-point, thermal,
entropic or bulk-solvent effects. The global minima are not guaranteed: each cluster had 6
seeded starts and a local optimiser.

They **can** support statements such as "in this model, lactic acid's extra α-hydroxyl group gives
more and different hydrogen-bonding sites than acetic acid, and its clusters with n waters are bound
by X vs Y kcal/mol".

ALCHEMI/AIMNet2 does **not** predict, and these results must not be presented as predicting:

* dough or starter **pH**, titratable acidity or acid dissociation. All species here are neutral,
  undissociated acids, and no pKa was computed;
* **perceived sourness** or any sensory attribute;
* **gas (CO₂) production**, fermentation rate or dough rise;
* **crumb structure** or the crumb STL models.

Those come from the separate macroscopic fermentation model (`src/fermentai/fermentation/`) and from
real measurements. The STL crumb models are illustrative hypotheses.

## 7. Reproducing

```bash
# Mac
uv run python -m fermentai.molecular.structures          # regenerate inputs (deterministic)
brev create fermentai-gpu --type hyperstack_A6000        # or any single NVIDIA GPU (check price)
scripts/setup_brev.sh                                     # env + nvidia-smi + pip freeze
ssh fermentai-gpu 'cd fermentai/results/official_example && ../../.venv/bin/python 08_aimnet2_ewald_pipeline.py'
scripts/run_remote_alchemi.sh selftest
scripts/run_remote_alchemi.sh optimize
scripts/run_remote_alchemi.sh md --ids <cluster ids>     # optional
scripts/stop_brev.sh --delete-if-unstoppable             # pulls results first
uv run python -m fermentai.molecular.analysis             # tables + figures, no GPU needed
uv run pytest tests/test_molecular_*.py
```

If the GPU driver is older than R580 (CUDA 12.x), `remote/setup_env.sh` replaces the PyPI Warp wheel
(CUDA 13) with the matching `+cu12` wheel from Warp's GitHub release (the fix the Warp install guide
documents). Without it the toolkit's kernels fail with `Invalid device identifier: cuda:0`.

Exact package versions are in `data/alchemi/environment/pip_freeze.txt`.
Expect bitwise differences on other GPUs or driver versions, at the float32 level (~1 meV in total
energy).

## 8. Results (all `real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000`)

Machine-readable versions: `data/alchemi/analysis/analysis_summary.json`, `molecular_descriptors.json`,
`association_energies.csv`, `deprotonation_energies.csv`, `hbonds.csv`, `md_summary.csv`.

**Reproduction of the official example.** NVIDIA example 08 (10 water pentamers, AIMNet2 + Ewald,
FIRE2) ran on the A6000: all 10 converged in 95 steps, E ≈ −10405.11 eV, fmax 0.013–0.049 eV/Å
(`official_example/08_aimnet2_ewald_pipeline.log`).

**Optimisations.** 159 structures were optimised, and all 159 converged to fmax < 0.01 eV/Å with FIRE2:
57 neutral, 48 anion, 53 ion-pair starts and H₃O⁺. The median number of steps was 40–402
depending on the species, with a maximum of 818. No geometry has atoms closer than 0.956 Å.

**Sanity checks against the literature.** AIMNet2 gives a binding energy of −10.3 kcal/mol for
acetic acid·H₂O (cyclic doubly H-bonded). For the pure water trimer and hexamer it gives −17.9 and
−45.5 kcal/mol, close to CCSD(T)/CBS values of about −15.8 and −45.9 kcal/mol.

**Association energies, lowest-energy conformers (kcal/mol):**

| n | L-lactic acid ΔE_assoc (per H₂O) | acetic acid ΔE_assoc (per H₂O) |
|---|---|---|
| 1 | −9.5 (−9.5) | −10.3 (−10.3) |
| 3 | −27.6 (−9.2) | −26.1 (−8.7) |
| 6 | −61.5 (−10.2) | −57.8 (−9.6) |

The mean per-water association energy is −9.66 (lactic) vs −9.54 (acetic) kcal/mol, a difference of
−0.12 kcal/mol, which is not significant. Binding of the acid to a pre-formed (H₂O)₆ network is
−16.0 (lactic) vs −12.3 (acetic) kcal/mol. The shortest acid→water H-bond, carboxyl O–H···O(water),
is 1.64 Å (O···O 2.64 Å) for lactic acid and 1.49 Å (O···O 2.52 Å) for acetic acid. Lactic acid's
α-OH gives extra acceptor/donor sites. Its lowest isolated conformer (c3) has its α-OH pointing at the carbonyl O=C
(H···O 2.11 Å, ∠ 117°) and lies 2.3 kcal/mol below the other five conformers. In those, the α-OH
points at the carboxyl OH oxygen instead (H···O 2.0–2.1 Å). These five-membered-ring contacts fall
just below the 120° angle cutoff, so they are not counted in `hbonds.csv`.

**Deprotonation (cluster-phase, kcal/mol).**

| n | lactic ΔE_sep | acetic ΔE_sep | lactic − acetic | ion pairs surviving (lactic / acetic) |
|---|---|---|---|---|
| 0 | 171.4 | 185.1 | −13.7 | – |
| 1 | 163.6 | 174.9 | −11.3 | 0/6 / 0/6 |
| 3 | 152.7 | 160.1 | −7.5 | 0/9 / 0/9 |
| 6 | 152.3 | 148.0 | +4.3 | 2/11 (ΔE_IP = +11.3) / 0/12 |

ΔE_sep = E(A⁻·nH₂O) + E(H₃O⁺) − E(HA·nH₂O) − E(H₂O). Hydration lowers the cost of proton
release for both acids. For n ≤ 3, lactic acid is clearly the more acidic of the two in this model,
by more than the conformer-sampling gaps of ≤ 3.6 kcal/mol. At n = 6 the order flips by 4.3
kcal/mol, but the gap between the lowest and second-lowest starts is 4.3–6.0 kcal/mol at n = 6, so
this reversal is **not resolved** by the 6-start sampling.

At matched composition, every acetic acid ion-pair start (n = 1, 3, 6) and every lactic acid start
for n ≤ 3 relaxed back to the neutral acid (barrierless proton return). Only lactic acid at n = 6
kept 2 of 11 starts as contact ion pairs, 11.3 kcal/mol above the neutral cluster. This qualitative
result (lactic acid can host a metastable ion pair where acetic acid cannot at this cluster size) is
the cleanest relative-acidity signal. It agrees in direction with the experimental aqueous pKa
values (3.86 vs 4.76), but it is **not** a pKa.

**NVT Langevin MD.** 72 runs: 6 clusters (neutral n = 3, 6 and anion n = 6 for each acid) × 4
thermostat temperatures (5, 22, 27, 37 °C) × 3 seeds, 2 ps each with dt 0.5 fs and γ 0.01 fs⁻¹.
All 72 completed, and no molecule detached. In the second ps:
* Neutral n = 6: lactic acid makes 3.7–3.8 acid–water H-bonds vs 2.1–2.6 for acetic acid.
* Anion n = 6: lactate⁻ 4.3–5.2 vs acetate⁻ 3.2–3.8.
* Total intermolecular H-bonds are about 8 (neutral n = 6) and about 9 (anion n = 6) for both acids.

Over 5–37 °C there is no temperature trend outside the seed scatter. Two-picosecond runs on small
gas-phase clusters say nothing about bulk-dough temperature effects.

## 9. Superseded / failed attempts (kept for provenance)

* `official_example/08_aimnet2_ewald_pipeline__attempt1_FAILED_warp_cu13_driver12.8.log`: the CUDA 13
  Warp wheel on a CUDA 12.8 driver. Fixed with the `+cu12` Warp wheel.
* `logs/failed/selftest_20261031T034932Z.*`: a runner bug (neighbour hooks need a `HookContext`).
  Fixed.
* `logs/superseded/`: a sequential MD run, stopped before any trajectory finished and replaced by
  parallel runs.
* `superseded/ionpair_v1/`: 53 ion-pair optimisations from a buggy proton-placement routine. In 7
  inputs the new H sat 0.12–0.55 Å from an existing H, and two runs relaxed into unphysical
  (H–H 0.13 Å) spurious minima about 0.75 eV too low. All ion pairs were rebuilt and re-run. A 0.70 Å
  minimum-distance guard now rejects such geometries in both the runner and the analysis.
* `logs/tests/`: a 2-structure smoke test of the optimiser.

---

# Campaign 2 (additive): sampling, larger clusters, co-solutes, thermochemistry, long MD

Campaign 2 used the same model, checkpoint, software stack and settings as campaign 1, on a new
`hyperstack_A6000` instance. Nothing from campaign 1 was changed. New inputs are in
`data/alchemi/inputs_c2/` (generated by `src/fermentai/molecular/structures_c2.py`). New results are
in `data/alchemi/results_c2/`, `vib_c2/` and `md_long/`, produced by `remote/run_alchemi_c2.py`.
Analysis is in `src/fermentai/molecular/campaign2.py`, which `analysis.py` calls automatically.

## C2.1 What was computed

| Set | Species | Starts |
|---|---|---|
| Sampling | lactic acid, acetic acid, lactate⁻, acetate⁻ with n = 0 / 3 / 6 waters | 12 / 40 / 40 seeded RDKit + H-bond placement starts each; the anions are solvated directly here (in campaign 1 they were derived from neutral minima) |
| Anneal-and-quench | top-5 unique minima per species at n = 3, 6 | NVT Langevin 400 K, 5 ps (γ 0.01 fs⁻¹); snapshots every 1 ps quenched with FIRE2 → `<id>__aq<k>` |
| Larger clusters | the four species at n = 10, 15; pure (H₂O)₁₀, (H₂O)₁₅ | 12 each; 10 each |
| Co-solutes | ethanol, β-D-glucose, Ac-Gln-Gln-NHMe: monomer, X·3H₂O, lactic/acetic + X + 3H₂O | 8 each |
| Acid pairs | acetic–acetic dimer (8 random + 1 explicit C2h cyclic start), lactic–acetic dimer, lactic + acetic + 6H₂O | 8 each |
| References | H₂O, H₃O⁺ | – |
| Thermochemistry | the H₂O and H₃O⁺ references, plus up to 5 unique minima within 3 kcal/mol of the lowest for each of the four species at n = 0, 3, 6, 10 | tight FIRE2 (fmax ≤ 0.002 eV/Å), then ASE `Vibrations` (central finite differences, δ = 0.01 Å) of the ALCHEMI pipeline forces |
| Long MD | the n = 6 neutral and anion clusters of both acids at 295 K and 310 K | 3 replicas × 20 ps; started from the campaign-1 lowest-energy n = 6 minima |

Optimisations are **batched** in the toolkit: up to 16 systems per `Batch`, FIRE2, fmax < 0.01 eV/Å.
The per-system iteration count is the first step at which that system's fmax fell below the
threshold. Converged systems keep being relaxed until the whole batch converges, so final forces
are often well below the threshold.

Thermochemistry uses `ase.thermochemistry.IdealGasThermo` (non-linear, symmetry number 1, singlet,
298.15 K, 1 atm). The six smallest |ν| are removed as translations and rotations. Two variants are
reported: RRHO, and quasi-harmonic, where real modes below 100 cm⁻¹, and any remaining small
imaginary modes, are raised to 100 cm⁻¹ (Ribeiro, Marenich, Cramer & Truhlar 2011). The ASE
calculator is a thin wrapper around the same ALCHEMI single point, returning gas-phase energy and
forces.

Pooled minima: campaign-1 and campaign-2 minima of the same species are pooled (identical model and
settings). A minimum is used only if all of the following hold:
* it converged;
* no two atoms are closer than 0.70 Å;
* for clusters, its protonation state matches the species (a neutral acid is still a neutral acid, an
  anion is still an anion);
* no molecule has drifted more than 4.5 Å (heavy-atom distance) from the rest.

## C2.2 Definitions

* **Boltzmann conformational free energy** of the unique minima (energies equal within
  0.01 kcal/mol count once), at 298.15 K: F = E_min − RT ln Σ exp(−ΔE_i/RT).
* **Relative acidity**, from the exchange reaction
  HA(lactic)·nW + A⁻(acetate)·nW → A⁻(lactate)·nW + HA(acetic)·nW:
  ΔΔX(n) = [X(lactate⁻·nW) − X(lactic·nW)] − [X(acetate⁻·nW) − X(acetic·nW)] for X = E, F or G.
  H₃O⁺ and H₂O cancel exactly, so no proton reference is needed.
  ΔpKa(lactic − acetic) = ΔΔG / (RT ln 10), with RT ln 10 = 1.364 kcal/mol at 298.15 K.
* **Uncertainty**, combined in quadrature from three parts:
  * the bootstrap SD (B = 2000, resampling the seeded and annealed starts of each of the four species)
    of the lowest-minimum ΔΔE;
  * half the range of ΔΔG over n;
  * the difference between using the lowest-G minimum and the Boltzmann average over the
    vibrationally characterised minima.
* **Co-solute energies** (kcal/mol):
  * total: E(acid·X·3W) − E(acid) − E(X) − 3E(W);
  * X binding to the hydrated acid: E(acid·X·3W) − E(acid·3W) − E(X);
  * the acid binding to hydrated X: E(acid·X·3W) − E(X·3W) − E(acid).
* **Acid pairs:**
  * dimer energy: E(AB) − E(A) − E(B);
  * pairing energy in water: E(L·A·6W) + E((H₂O)₆) − E(L·6W) − E(A·6W).
* **Long MD** (acid or anion with water H-bonds, geometric criterion):
  * continuous lifetimes, from 10 fs frames;
  * the Luzar–Chandler intermittent correlation C(t) and its integral τ over 0–5 ps;
  * the carboxyl-O / water-O pair distribution, normalised per shell volume (a cluster curve, not a
    bulk g(r));
  * protonation-state changes;
  * exchange events of first-shell (H-bonded) waters per ps.

## C2.3 What it does and does not imply

The relative pKa is a **cluster-phase relative estimate**: 6–10 explicit waters, gas-phase ideal-gas
thermochemistry, a harmonic/quasi-harmonic treatment of very anharmonic cluster modes, and no bulk
continuum. Its agreement or disagreement with the experimental aqueous difference (−0.90) is a
validation check of the approach, not a prediction of dough pH.

The Ac-Gln-Gln-NHMe complexes are a **molecular proxy** for acid contacts with glutamine-rich gluten
side chains and backbone amides. They are hypothesis-level evidence about local H-bond energetics,
not a model of gluten network mechanics or of "acid weakens gluten". There is no Na⁺ or Cl⁻: the
salt in the dough is not represented.

## C2.4 Results (all `real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000`)

Machine-readable sources: `data/alchemi/analysis/c2_summary.json`, `c2_species_minima.csv`,
`c2_association_by_n.csv`, `c2_lactic_minus_acetic_by_n.csv`, `c2_vibrational_thermo.csv`,
`c2_md_long_summary.csv`, and the new keys in `molecular_descriptors.json`.

**Volume of work.**
* 607 seeded campaign-2 optimisations and 300 anneal-and-quench minima, all converged. Sixteen were
  re-run after a GPU out-of-memory event at 05:20Z; the failed records are kept in
  `logs/failed/c2_oom_*`.
* Pooled with campaign 1, 1009 minima pass the sanity filters and 4 are rejected.
* 79 vibrational analyses; after de-duplication, the lowest-G copy of each distinct minimum is used.
* 8 long-MD runs (3 replicas × 20 ps each), all completed. Two of them were re-run after the OOM event.

**Relative pKa (headline).** ΔpKa(lactic − acetic) = **−3.89 ± 1.73**, from the n = 6 and n = 10
quasi-harmonic cycles. The experimental aqueous value is **−0.90** (3.86 − 4.76).

| n | ΔΔE electronic (kcal/mol) | ΔΔG qh, Boltzmann (kcal/mol) | ΔpKa |
|---|---|---|---|
| 0 | −13.6 | −12.8 | −9.3 |
| 3 | −7.5 ± 0.7 | −7.8 | −5.7 |
| 6 | −9.0 ± 2.1 | −6.3 | −4.65 |
| 10 | −4.1 ± 1.8 | −4.3 | −3.12 |
| 15 | +3.9 ± 7.0 (12 starts, no anneal: unconverged; no thermochemistry) | – | – |

The sign is right: lactic acid is the stronger acid. The magnitude is too large by about 3 pKa
units, about 1.7 of the combined uncertainty.

The estimate moves toward experiment as explicit hydration grows (−9.3 → −5.7 → −4.7 → −3.1). This
is the behaviour expected from a cluster model that lacks bulk dielectric screening. A gas-phase
cluster over-stabilises the lactate's internal α-OH···carboxylate hydrogen bond relative to the
fully solvated ion.

What the uncertainty is made of (kcal/mol, combined in quadrature):
* bootstrap sampling SD: 2.1;
* half-range over n = 6 and 10: 1.0;
* lowest-vs-Boltzmann choice of minimum: 0.2.

This is a cluster-phase relative estimate, not a solution pKa.

Before annealing, n = 10 had 12 starts and only one lactate conformer within 3 kcal/mol, and gave
ΔpKa = +0.79. Annealing found lower minima and moved it to −3.12, consistent with n = 6. This
illustrates how sensitive the cycle is to sampling.

**Acetic acid cyclic dimer.** ΔE = **−15.26 kcal/mol**, electronic. The literature range is about
−14 to −16 kcal/mol. The lowest structure is the C2h doubly hydrogen-bonded dimer (H···O 1.69 Å,
O···O 2.68 Å ×2), reached from the explicit cyclic start. The lactic–acetic heterodimer is
−14.95 kcal/mol.

**Association energy per water (lowest pooled minima, kcal/mol).**

| n | lactic acid | acetic acid | lactate⁻ | acetate⁻ |
|---|---|---|---|---|
| 1 | −9.48 | −10.29 | −17.41 | −20.48 |
| 3 | −11.08 | −11.03 | −16.24 | −18.24 |
| 6 | −11.38 | −11.84 | −14.96 | −16.20 |
| 10 | −11.67 | −11.09 | −14.12 | −14.50 |
| 15 | −11.26 | −10.84 | −13.30 | −14.06 |

* For the neutral acids, the lactic − acetic difference per water stays within ±0.8 kcal/mol at all n.
  Its sign flips with n, so it is not resolved.
* Acetate⁻ binds water more strongly per molecule than lactate⁻ at small n (+3.1 at n = 1, +2.0 at
  n = 3). The difference shrinks to +0.4 to +0.8 at n = 10–15. Lactate's α-OH competes with water for
  the carboxylate.
* The better sampling lowered the n = 6 neutral values by 1–2 kcal/mol per water compared with
  campaign 1 (6 starts). The campaign-1 numbers were under-sampled.

**Co-solutes.** Values are X binding to the hydrated acid, E(acid·X·3W) − E(acid·3W) − E(X), in
kcal/mol, with the key acid–X H-bond:

| X | lactic acid | acetic acid |
|---|---|---|
| ethanol | −8.3 (carboxyl OH → ethanol O, 1.58 Å) | −9.1 |
| β-D-glucose | −11.1 | −10.8 |
| Ac-Gln-Gln-NHMe | −19.1 (carboxyl OH → amide O=C 1.83 Å, plus α-OH···O 1.92 Å) | −15.8 (carboxyl OH → amide O 1.68 Å) |

* The amide contacts are the strongest of the three co-solutes. Lactic acid binds the glutamine
  dipeptide about 3 kcal/mol more strongly than acetic acid does, through its extra α-OH. This is a
  hypothesis-level molecular proxy for acid–gluten amide contacts. The 8 starts per complex leave
  gaps to the second-lowest minimum of 1.4–10.7 kcal/mol, so the dipeptide values are the least
  converged.
* Mixed lactic + acetic + 6 H₂O: the total association energy is −76.1 kcal/mol. The "pairing"
  energy, E(L·A·6W) + E((H₂O)₆) − E(L·6W) − E(A·6W) = +17.7 kcal/mol, is **not interpretable**. The
  mixed cluster had only 8 starts against more than 70 for each single-acid cluster, and (H₂O)₆ had
  only 4 starts. It is reported for completeness only.
* The same thin (H₂O)ₙ sampling (4 starts at n = 3 and 6) affects `dE_to_water_network` at n = 3 and 6.

**Long MD (20 ps × 3 replicas, n = 6).**

| Cluster | Acid–water H-bonds, 295 / 310 K | Water exchange events / ps, 295 / 310 K |
|---|---|---|
| lactic acid | 3.98 / 3.26 | 6.4 / 6.2 |
| acetic acid | 2.33 / 2.43 | 3.7 / 5.6 |
| lactate⁻ | 3.98 / 3.95 | 4.1 / 4.0 |
| acetate⁻ | 3.77 / 3.66 | 3.0 / 3.2 |

* The intermittent H-bond τ is 2.9–3.9 ps for all eight runs.
* Mean continuous lifetimes are 0.3–0.6 ps. The medians (0.03–0.08 ps) are dominated by librational
  flicker across the geometric cutoff.
* There were no protonation-state changes (no proton transfer) in any trajectory.
* The carboxyl-O/water-O distribution peaks at 2.6–2.7 Å for all clusters.
* The 295 → 310 K differences are within replica scatter, except that neutral lactic acid loses
  about 0.7 acid–water H-bonds at 310 K.

## C2.5 Campaign-2 failures and their handling

* A GPU out-of-memory event at 05:20Z hit 14 concurrent processes on the 48 GB card. One
  optimisation batch (16 structures) and two long-MD runs failed. Their records and tracebacks are
  kept in `logs/failed/c2_oom_*`, and all of them were re-run successfully.
* The work was interrupted for about 7 h by an operator usage limit. The instance sat idle
  (no jobs) from about 05:50Z to 12:33Z, and that idle time is billed.
* Some vibrationally characterised "conformers" turned out to be duplicates of the same minimum,
  differing only by float32 energy noise of up to 0.03 kcal/mol. They are now de-duplicated by
  energy (< 0.05 kcal/mol) and geometry fingerprint (< 0.02 Å RMS). Without this, Boltzmann averages
  would be biased by −RT ln k.
* At about 13:20Z the Brev SSH gateway stopped accepting connections ("banner exchange timed out")
  while `brev ls` still showed the instance as running. See `environment/report.md` for how the
  instance was released.
