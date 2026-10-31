# Validation status

This document records what each component has been checked against, and what has **not** been
checked. It was written by the integration/QA workstream at the 2026-10-31 final pass (`HEAD`
`b83297a`). PLAN.md §17 asks for it.

Related documents:

- `docs/acceptance_tests.md`: the PLAN §16 item-by-item table
- `docs/test_report.md`: the latest automated run
- `docs/STATUS.md`: the overall status

Vocabulary:

| Word | Meaning here |
|---|---|
| **validated against X** | compared with an independent reference X, and the comparison is recorded |
| **sanity-checked** | checked for internal consistency or physically plausible behaviour, with no reference data |
| **tested** | automated tests exercise the code path; this says nothing about physical truth |
| **real (tool output)** | produced by the real external tool (NVIDIA GPU run, Formlabs PreForm server), not by our models |
| **simulated** | produced by our model or a virtual device, and labelled `simulated` |
| **hardware-unverified** | written for hardware that has not been run on that hardware |

## Summary

| Component | Validated against | Status |
|---|---|---|
| NVIDIA ALCHEMI / AIMNet2 (campaigns 1 + 2) | Upstream `aimnet` calculator; NVIDIA example 08; **experiment**: ΔpKa(lactic − acetic) −3.89 ± 1.73 vs −0.90; **literature**: acetic acid dimer −15.26 vs −14…−16 kcal/mol, water clusters vs CCSD(T)/CBS | **Real** GPU results, cross-checked. Cluster-phase only. |
| Fermentation kinetics | Sanity properties only. Parameters come from literature ranges. The ALCHEMI hook is applied (ΔpH ≤ 0.013). | **Not calibrated** to any physical dough |
| Crumb STL specimens | Geometric validation, plus the **live Formlabs PreForm** scene check (auto-support, printability validation) | **Print-ready per PreForm, never printed**. Not compared with a real crumb. |
| Backend API + contract | JSON-schema contract check, 103 API tests, live end-to-end runs with virtual devices | **Tested (software)** |
| Virtual ESP32 devices | Run against the real API over HTTP; controller unit tests | **Simulated**. The thermal plant is a controller-test model. |
| ESP32 firmware | Compiles for 3 board environments; 94 native unit tests; 8/8 contract samples validated against the API models | **Hardware-unverified** |
| Scientist agents / offline policy | Deterministic tests; PLAN_MASTER §14 sentence end to end; in-silico benchmark | **Tested (software)**. The benchmark is in-silico only. |
| Dashboard | Headless Chrome; static and live offline scans; HTTP crawl | **Not tested on a physical iPad** |
| Local network / offline | `scripts/offline_check.sh` on the Mac: 0.0.0.0 bind, LAN-address access, outbound audit | **Not tested on the travel router**. The WAN was physically up. |

## 1. NVIDIA ALCHEMI molecular calculations

**Validated against**

- **Upstream reference implementation.** The ALCHEMI pipeline (AIMNet2 + Ewald + DFT-D3) agrees with
  the upstream `aimnet` calculator to |ΔE| ≤ 1.02 meV on the self-test structures. Across all
  campaign-1 structures it agrees to max |ΔE| 5.9 meV and max |ΔF| 1.5·10⁻³ eV/Å
  (`data/alchemi/selftest/`, `molecular_descriptors.json`).
- **NVIDIA's own example.** Official example 08 was reproduced on the A6000
  (`data/alchemi/official_example/`).
- **Experiment, relative acidity (campaign 2).** A cluster-phase quasi-harmonic exchange cycle
  (n = 6 and n = 10 explicit waters, annealed sampling, Boltzmann-averaged) gives
  ΔpKa(lactic − acetic) = **−3.89 ± 1.73**. The experimental aqueous value is **−0.90**.
  - The sign is right: lactic acid is the stronger acid.
  - The magnitude is too large by about 3 pKa units, about 1.7σ.
  - The estimate moves toward experiment as hydration grows (n = 0, 3, 6, 10: −9.3, −5.7, −4.65,
    −3.12). That is the expected behaviour of a cluster model with no bulk dielectric.
  - It is **not** a solution pKa (`docs/simulation_provenance.md`).
- **Literature, dimerisation (campaign 2).** The acetic acid cyclic (C2h) dimer comes out at
  ΔE = **−15.26 kcal/mol**, inside the literature range of −14 to −16 kcal/mol.
- **Literature, campaign 1.** The water trimer comes out at −17.9 kcal/mol against CCSD(T)/CBS
  ≈ −15.8. The hexamer comes out at −45.5 against ≈ −45.9. Acetic acid·H₂O comes out at
  −10.3 kcal/mol.
- **Convergence.**
  - Campaign 1: 159/159 optimisations (fmax < 0.01 eV/Å) and 72/72 MD runs.
  - Campaign 2: 907/907 optimisations, 79 vibrational analyses (0 failed) and 8/8 runs of 20 ps MD.
    16 optimisations and 2 MD runs were re-run after a GPU out-of-memory event; the failed records
    are kept.

**Where the numbers came from.** Brev `fermentai-gpu`, hyperstack_A6000 (RTX A6000), $0.60/h, in two
campaigns:

- Campaign 1: 1.14 h, about $0.68.
- Campaign 2: about 8.8 h billed, about $5.3. About 7 h of that was idle.
- Total: about $6. Both instances are deleted (`brev ls`: no instances).

Results are on the Mac. For campaign 1, an rsync dry-run showed 0 differences. For campaign 2,
completeness is argued file by file in `data/alchemi/environment/c2_release_note.md`, because the
SSH gateway failed before the final dry-run. Without a GPU, the Mac re-derives every summary with
`uv run python -m fermentai.molecular.analysis`. The analysis now writes strict JSON (no NaN).

**Not validated**

- Energies are gas-phase microsolvated clusters with harmonic (quasi-harmonic) thermochemistry.
  There is no bulk solvent, and global minima are not guaranteed.
- The n = 15 clusters are unconverged and are not used.
- Nothing here predicts pH, sourness, CO₂ or crumb structure directly.

## 2. Fermentation kinetics (`src/fermentai/fermentation/`)

**Sanity-checked only. No physical calibration.**

- Parameters come from literature ranges, with references in `configs/kinetics.yaml` and
  `docs/scientific_assumptions.md`.
- Unit tests (44) check qualitative properties. B (27 °C) acidifies faster and peaks earlier than A
  (22 °C). The rise saturates, then collapses. Calibration recovers parameters from *synthetic*
  data.
- **ALCHEMI coupling is now `applied`** (`data/predictions/molecular_coupling_report.json`):
  - The cluster ΔpKa shifts the lactic–acetic pKa gap from −0.90 to −0.95. The shift is capped at
    0.05, so only the sign of the discrepancy is used.
  - The undissociated-lactic potency moves from 0.10 to 0.127.
  - The effect on the forecasts is |ΔpH| ≤ 0.013 at 12–16 h, with no change in peak or overproof
    timing.
  - This is transparent and traceable, but not decisive for the forecast.
- Reproducibility: `tests/integration/test_artifacts_reproducible.py` re-runs both nominal scenarios
  with no torch or CUDA loaded. They match the committed CSVs to rtol 2·10⁻⁴.

**Not validated:**

- No pH, rise or temperature data from this flour, starter or vessel has been fitted.
- The model does not predict flavour or perceived sourness.
- The model's CO₂ is modelled production. It is not a sensor quantity, and the BME680 gas
  resistance is not CO₂.

## 3. Crumb STLs and Formlabs print preparation

**Validated geometrically, prepared by the real PreForm server, never printed.**

- `crumb_chamber_{A,B}.stl` each pass 10/10 checks in `*_validation.json`. The integration test also
  checks the binary-STL size against the triangle count, and that A ≠ B.
- **Live PreForm Local API** (PreFormServer 3.64.2, notarized Formlabs build; source
  `preform_local_api`):
  - Imported both specimens for Form 4 / Grey V5 / 0.1 mm, open face up.
  - Auto-generated supports and ran PreForm's printability validation.
  - Saved `.form` scenes.
  - PreForm's own **estimates**: 1.36 h and 150.24 mL resin; 185 layers.
  - PreForm flags cup regions (A: 1, B: 3) as warnings.
  - The wire transcript and manifest are in `data/stl/print/`. An auto-orient alternative is in
    `alternative_preform_auto/`.
- Submission is never automated. It needs `--i-confirm-print` and
  `FERMENTAI_FORMLABS_CONFIRM_PRINT=yes`, and even then a person starts the print.

**Not validated:**

- No physical print, post-processing or dimensional check.
- No comparison with a photographed real crumb. `analyze_crumb_image` exists but has not been run
  on a photo.
- Most mapping coefficients are labelled `assumption` (`docs/morphology_coupling.md`).
- Resin parts are display-only and not food-safe.

## 4. Backend API and contracts (`src/fermentai/api/`, `contracts/`)

**Tested (software).**

- `uv run python -m fermentai.api.contract --check` is clean. The JSON-schema contract test now runs
  with `jsonschema`, so it no longer skips.
- 103 API tests plus the integration suite drive real HTTP against uvicorn with the virtual devices.
  They cover telemetry, dedup, the setpoint clamp and its step and rate limits, motor
  accepted→completed, `ran` semantics, cancel, capture, taste feedback and SSE.
- The issues found in the first QA pass are fixed:
  - NaN-safe JSON.
  - Telemetry, setpoints and limits are passed to the agents.
  - Agent state reads the API's findings log.
- Remaining open item: two listing envelopes lack a provenance label (`docs/STATUS.md`).

## 5. Virtual devices (`simulated_devices/`)

**Simulated, and tested against the real API.**

- Every record is `source: "simulated"` with quality flag `simulated_device` and firmware
  `virtual-0.1`.
- The starter rise uses the real kinetics, and the integration test asserts that this model, not the
  fallback, is loaded.
- The thermal plant is a **controller-test model with assumed time constants**. It is not a
  validated simulation of the chambers.
- The virtual devices reuse the physical device ids `fermentai-esp32-a/b`.

## 6. ESP32 firmware (`firmware/`, commit `1e2dfc1`)

**Compiles, native tests pass, hardware-unverified** (`.progress/firmware.md`, `firmware/build_logs/`).

- Builds: `esp32s3cam_a`, `esp32s3cam_b` and `esp32s3cam_noio` all SUCCESS, with 0 warnings from
  firmware sources. The ALT-pin variant B also builds.
- Native unit tests: 94/94 (control 20, sensors 18, motor 12, commands 23, telemetry 13, network 8).
- Contract: the serializer output passes the backend's Pydantic models (8/8). The v1.0 profile
  passes the frozen v1.0 models.

**Not verified:**

- Flashing.
- Reset-level pin states (GPIO38/40; see `docs/STATUS.md` → Hardware bring-up next steps).
- Sensors.
- Peltier control and its interlocks.
- Camera.
- Motor.
- Wi-Fi on the travel router.

None of these has been exercised on a physical board.

## 7. Scientist agents (`src/fermentai/agents/`, `src/fermentai/design/`)

**Tested (software).**

- The PLAN_MASTER §14 sentence gives the structured preference "More sourness / Lower density /
  Preserve good rise".
- scientist_A and scientist_B exchange distinct findings linked by `in_reply_to`. A next experiment
  requiring human confirmation follows.
- Through the API the agents now receive the chambers' live telemetry, actual setpoints and
  hardware limits. Integration tests cover this.
- Live mode with no LLM falls back to the labelled offline policy.
- `claude -p` live mode was smoke-tested once by the agents workstream. It is not in the automated
  suite.
- The collaboration benchmark (`docs/benchmark_results.md`) is **in-silico**.

## 8. Dashboard (`src/fermentai/dashboard/`)

**Tested headless. Not on a physical iPad.**

- Static scans find no external URLs, CDN, web fonts or `@import`. Plotly is vendored and
  byte-identical.
- The live crawl in the integration suite fetches `/` and every referenced asset and ES-module
  import over HTTP. All return 200 with JavaScript MIME, and none contains an external URL.
- The dashboard workstream drove headless Chrome at iPad widths, with 0 external requests and
  0 console errors.

**Not verified:** iPadOS Safari, touch interaction, and the token flow on a physical iPad.

## 9. Local network / disconnected mode

**Verified on the Mac only**, by `scripts/offline_check.sh` and
`tests/integration/test_replay_contract_offline.py::test_lan_bound_server_full_loop_without_external_traffic`:

- The server is bound to `0.0.0.0` and reached through the Mac's LAN address.
- The full judge loop ran with the virtual devices.
- `lsof` showed only loopback or private socket peers.
- The PEP 578 audit hook recorded **0** DNS lookups or connections to non-private hosts. A
  positive-control test proves the hook flags public addresses.

**Not verified:**

- The travel router.
- A physical iPad.
- The ESP32s on 2.4 GHz WPA2.
- mDNS.
- The macOS firewall prompt.
- A run with the WAN physically disconnected. The audit proves the server never *tried* to reach the
  internet.

## What is NOT validated (consolidated)

1. Any physical measurement. No real telemetry, pH, rise, image or sensory data from the actual
   chambers or loaves is in the repo.
2. Kinetics calibration against real dough.
3. The physical Formlabs print, and its comparison with a real crumb photo. The print was prepared
   and validated by PreForm only.
4. ESP32 firmware on hardware: flashing, reset levels, sensors, Peltier control and safety, motor,
   camera.
5. The physical iPad and travel-router network, and disconnected mode with the WAN physically off.
6. Live-LLM agent mode in the automated suite.
7. Solution-phase acidity. The ALCHEMI ΔpKa is a cluster-phase estimate and is 3 units too large in
   magnitude.
