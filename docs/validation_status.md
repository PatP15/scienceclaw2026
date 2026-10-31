# Validation status

What each component has been checked against, and what has **not** been checked. Written by the
integration/QA workstream on 2026-10-31 (UTC ≈ 05:10), against working tree on top of commit
`dd3273f`. PLAN.md §17 asks for this document. The PLAN §16 item-by-item table is in
`docs/acceptance_tests.md`; the latest automated run is in `docs/test_report.md`.

Vocabulary used below:

| Word | Meaning here |
|---|---|
| **validated against X** | compared with an independent reference X, and the comparison is recorded |
| **sanity-checked** | checked for internal consistency or physically plausible behaviour, with no reference data |
| **tested** | automated tests exercise the code path; this says nothing about physical truth |
| **simulated** | produced by a model or a virtual device, and labelled `simulated` |
| **hardware-unverified** | written for hardware that has not been run on that hardware |

## Summary

| Component | Validated against | Status |
|---|---|---|
| NVIDIA ALCHEMI / AIMNet2 molecular energies (campaign 1) | upstream `aimnet` calculator (same structures), NVIDIA official example 08, literature water-cluster and acetic acid·H₂O binding energies | **Real GPU results**, cross-checked; cluster-phase only |
| Fermentation kinetics | sanity properties only (monotonic acidification, temperature ordering, rise saturates then collapses, mass balance); parameters from literature ranges | **Not calibrated** to any physical dough |
| Crumb STL specimens | geometric validation (watertight, winding, volume, min thickness, …) | **Not printed**; not compared with a real crumb |
| Backend API + contract | JSON-schema contract check, 100+ API tests, live end-to-end runs with virtual devices | **Tested (software)** |
| Virtual ESP32 devices | the real API over HTTP; controller unit tests | **Simulated**; thermal plant is a controller-test model, not a physical simulation |
| ESP32 firmware | compiles for 3 PlatformIO environments; 94 native unit tests; contract samples validated | **Hardware-unverified** (never flashed to a board in this repo's evidence) |
| Scientist agents / offline policy | deterministic tests; PLAN_MASTER §14 sentence end-to-end; in-silico benchmark | **Tested (software)**; benchmark is in-silico only |
| Dashboard | headless Chrome (dashboard workstream), static + live offline scans, HTTP crawl | **Not tested on a physical iPad** |
| Local network / offline | `scripts/offline_check.sh` on the Mac (0.0.0.0 bind, LAN-address access, outbound audit) | **Not tested on the travel router**; WAN was physically up during the automated run |

## 1. NVIDIA ALCHEMI molecular calculations

**Validated against**

- *Upstream reference implementation.* `data/alchemi/selftest/` and
  `data/alchemi/environment/report.md`: the ALCHEMI pipeline (AIMNet2 + Ewald + DFT-D3) agrees with the
  upstream `aimnet` calculator to |ΔE| ≤ 1.02 meV and max|ΔF| ≤ 2.8·10⁻⁴ eV/Å on the self-test
  structures. Across all 159 optimised structures: max |ΔE| 5.9 meV, max |ΔF| 1.5·10⁻³ eV/Å
  (`molecular_descriptors.json → provenance.crosscheck_upstream_aimnet`).
- *NVIDIA's own example.* Official example 08 (10 water pentamers) reproduced on the A6000; all
  converged (`data/alchemi/official_example/`).
- *Literature values* (`docs/simulation_provenance.md` §8): water trimer −17.9 vs CCSD(T)/CBS ≈ −15.8
  kcal/mol; water hexamer −45.5 vs ≈ −45.9 kcal/mol; acetic acid·H₂O −10.3 kcal/mol. The direction
  of the lactic-vs-acetic ion-pair result agrees with the experimental aqueous pKa ordering (3.86 vs
  4.76). It is **not** a pKa.
- *Convergence.* 159/159 optimisations converged to fmax < 0.01 eV/Å; 72/72 MD runs completed.

**Where the numbers came from.** Brev instance `fermentai-gpu` (hyperstack_A6000, RTX A6000), created
03:36:39Z and deleted 04:44:50Z on 2026-10-31. Instance time was 1.14 h, about **$0.68**. Results
were rsynced to the Mac (a dry-run showed 0 differences) before deletion. The Mac re-derives every
summary without a GPU (`uv run python -m fermentai.molecular.analysis`).

**Not validated**

- Energies are 0 K, gas-phase microsolvated clusters with 6 seeded starts each. They are not free
  energies, not bulk solution, and global minima are not guaranteed. The n = 6 deprotonation ordering
  is inside the sampling noise.
- Nothing here predicts pH, sourness, CO₂ or crumb structure. The coupling into the kinetics
  changes predicted pH by ≤ 0.002 (`data/predictions/molecular_coupling_report.json`).
- **Campaign 2 is in progress** while this is written: `.progress/molecular.md`,
  `data/alchemi/environment/c2_*`. A new `fermentai-gpu` instance was requested at 04:59:48Z, with a
  $30 budget. Its results (`results_c2/`) are not covered by this document or by the tests.

**Data-integrity issue found.** `data/alchemi/analysis/analysis_summary.json` contains bare `NaN`
tokens, which are not valid JSON. Python reads them, but browsers' `JSON.parse` and FastAPI's
encoder reject them. That made `GET /api/v1/molecular/summary` return HTTP 500. The API endpoint is
now patched to map NaN → null (`src/fermentai/api/routes.py`, molecular_summary). The file itself
should still be written with `null` (owner: molecular, `analysis.py:1034`).

## 2. Fermentation kinetics (`src/fermentai/fermentation/`)

**Sanity-checked only. No physical calibration.**

- Parameters come from literature ranges, with references in `configs/kinetics.yaml` and
  `docs/scientific_assumptions.md` §11. Unit tests (`tests/test_fermentation_*.py`) check
  qualitative properties: B (27 °C) acidifies faster and peaks earlier than A (22 °C); rise saturates,
  then collapses past capacity; schedules interpolate; calibration recovers parameters from
  *synthetic* data.
- Reproducibility: `tests/integration/test_artifacts_reproducible.py` re-runs both nominal scenarios in
  a clean interpreter with no torch or CUDA loaded. It matches the committed
  `data/predictions/chamber_{A,B}_prediction.csv` to rtol 2·10⁻⁴.
- Monte-Carlo envelopes reflect assumed parameter ranges, not structural error.

**Not validated:** no pH, rise or temperature data from this flour, starter or vessel has been
fitted. `calibrate()` exists, but nothing has been run through it with real observations. The model
does not predict flavour or perceived sourness. Its CO₂ is modelled production, not a sensor
quantity, and the BME680 gas resistance is not CO₂.

## 3. Crumb STLs (`src/fermentai/geometry/`, `data/stl/`)

**Validated geometrically. Not printed.**

- `crumb_chamber_{A,B}.stl` each pass 10/10 checks in `*_validation.json`: watertight, consistent
  winding, positive volume, min wall thickness ≥ printable, and others. The integration test also
  checks the binary-STL size against the triangle count and confirms that A ≠ B.
- The mapping from simulation to crumb parameters is documented coefficient by coefficient
  (`docs/morphology_coupling.md`). Most coefficients are labelled `assumption`.

**Not validated:** no Formlabs print was made (`formlabs_preform.py` is description-only). No PreForm
import or printability check was done. No comparison was made with a photographed real crumb
(`analyze_crumb_image` exists, but no photo has been analysed). Chamber A ships un-decimated
(≈ 22 MB) because mesh simplification falls back.

## 4. Backend API and contracts (`src/fermentai/api/`, `contracts/`)

**Tested (software).** `uv run python -m fermentai.api.contract --check` is clean: 8 JSON schemas plus
examples, v1.0 fixtures still validate. The API unit tests and the integration suite drive real HTTP
against uvicorn with the virtual devices: telemetry, dedup, setpoint clamp, step and rate limits,
motor accepted→completed, `ran` semantics, cancel, capture, taste feedback, SSE.

**Known defects** are listed in `docs/STATUS.md` → Known bugs. The most important one is a
context-key mismatch between the API and the agents. The telemetry part was fixed upstream during
QA. The setpoint part is still open: the agents assume 22/27 °C rather than the chambers' actual
setpoints. It is an xfail test in `tests/integration/test_inprocess_flow.py`.

## 5. Virtual devices (`simulated_devices/`)

**Simulated, and tested against the real API.** Every record is `source: "simulated"`, with the quality
flag `simulated_device` and firmware `virtual-0.1`. The starter rise uses the real kinetics
(`fermentai.fermentation.simulation.simulate`, accelerated clock). The integration test asserts this
model is the one loaded, not the logistic fallback. The thermal plant is a **controller-test model
with assumed time constants**, not a validated simulation of the chambers.

Note: the virtual devices identify as `fermentai-esp32-a/b`, the same ids as the physical boards.
Provenance stays correct through `source`, but the device registry cannot tell a virtual board from
a real one by id.

## 6. ESP32 firmware (`firmware/`)

**Compiles; native tests pass; hardware-unverified.** This comes from `firmware/build_logs/` as of
2026-10-31 ≈ 05:03 UTC (the firmware workstream is still editing):

- `esp32s3cam_a`, `esp32s3cam_b`, `esp32s3cam_noio`: `pio run` SUCCESS (PlatformIO espressif32 6.9.0).
- `native` environment: 94/94 unit-test cases passed (commands, telemetry, network, motor, control,
  sensors).
- `contract_validation.log`: firmware sample payloads validated against the API models.

No `.progress/firmware.md` existed when this was written. **Not verified:** flashing, sensors (BME680,
DS18B20, pH), Peltier and H-bridge control, thermal safety cut-outs, camera capture, motor actuation,
Wi-Fi on the travel router. None of these has been exercised on a physical board in recorded
evidence.

## 7. Scientist agents (`src/fermentai/agents/`, `src/fermentai/design/`)

**Tested (software).** The PLAN_MASTER §14 sentence yields the structured preference "More sourness /
Lower density / Preserve good rise". scientist_A and scientist_B exchange distinct findings linked by
`in_reply_to`, and the round ends with a next experiment that requires human confirmation. Mode
labels: `offline_policy` / "Offline Policy". Live mode with no LLM falls back to the offline policy
and says so.

- `claude -p` live mode was smoke-tested once by the agents workstream (`.progress/agents.md`). It is
  not part of the automated suite, because it needs a network.
- The collaboration benchmark (`docs/benchmark_results.md`) is **in-silico**, on simulated worlds.
  It is not evidence about real bread.
- Through the API, the agents now receive the chambers' telemetry, but **not** their current
  setpoints. They assume the defaults (22/27 °C), which is an open bug.

## 8. Dashboard (`src/fermentai/dashboard/`)

**Tested headless. Not on a physical iPad.**

- Static scans find no external URLs, CDN, web fonts or `@import`. Plotly is vendored and
  byte-identical to the installed package.
- Live crawl: the integration suite fetches `/` and every referenced asset and ES-module import over
  real HTTP. All return 200 with JavaScript MIME, and none contains an external URL. The scan reuses
  the dashboard's own scanner.
- The dashboard workstream drove headless Chrome (SwiftShader WebGL) at 390, 820 and 1180 px widths,
  with 0 external requests and 0 console errors (`.progress/dashboard.md`).
- The dashboard is being edited concurrently, so these results apply to the tree at the time of the
  run.

**Not verified:** iPadOS Safari rendering, touch interaction, and the token flow on a physical iPad.

## 9. Local network / disconnected mode

**Verified on the Mac only.** `scripts/offline_check.sh` and
`tests/integration/test_replay_contract_offline.py::test_lan_bound_server_full_loop_without_external_traffic`:

- The server is bound to `0.0.0.0` and reached through the Mac's LAN address (Wi-Fi interface, as a
  stand-in for the iPad).
- The full judge loop ran with virtual devices.
- `lsof` showed only loopback or private socket peers.
- A PEP 578 audit hook recorded **0** DNS lookups or connects to non-private hosts. A positive-control
  test proves the hook flags public addresses.

**Not verified:** the GL.iNet or other travel router, a physical iPad, the ESP32s on 2.4 GHz WPA2,
client isolation, mDNS `fermentai.local`, and the macOS firewall prompt. During the automated run
the Mac's WAN was **up**. The audit proves the server never *tried* to reach the internet, but no
run has yet been made with the WAN physically disconnected. The manual procedure is in
`scripts/offline_check.sh --help` and `docs/acceptance_tests.md`.

## What is NOT validated (consolidated)

1. Any physical measurement. No real telemetry, pH, rise, image or sensory data from the actual
   chambers or loaves is in the repo. Every chamber reading seen so far is `simulated` or `replay`.
2. Kinetics calibration against real dough.
3. Crumb STL printing (Formlabs) and comparison with a real crumb photo.
4. ESP32 firmware on hardware: flashing, sensors, Peltier control and safety interlocks, motor,
   camera.
5. Physical iPad and travel-router network. Disconnected mode with the WAN physically off.
6. Live-LLM agent mode in the automated suite. The one manual `claude -p` smoke test is recorded by
   the agents workstream.
7. ALCHEMI campaign 2 (in progress at the time of writing).
8. Agents using the chambers' actual setpoints through the API (known bug; see `docs/STATUS.md`).
