# FermentAI: consolidated status report

PLAN_MASTER §0 item 9: what is implemented, tested, simulated and hardware-unverified.

- **As of:** 2026-10-31 ≈ 05:10 UTC
- **Base commit:** `dd3273f`. The working tree also has uncommitted firmware, dashboard and API
  edits that are still in progress.
- **Author:** integration/QA workstream (I)
- **Related documents:**
  - `docs/acceptance_tests.md`: PLAN §16, item by item
  - `docs/validation_status.md`: what each result was checked against
  - `docs/test_report.md`: last automated run

## Headline

- Every automated suite passes: 405 unit, 34 virtual-device and 27 integration tests (+3 expected
  failures that document known bugs), plus the contract check and the offline check. The full run
  takes about 2 min (`scripts/run_acceptance.sh`).
- The PLAN_MASTER §14 judge loop works end to end over real HTTP with labelled virtual chambers. The
  sentence is parsed, two agents exchange linked findings, a next experiment is proposed, both motor
  commands reach `accepted → completed`, and the server makes zero non-LAN connections.
- **Nothing has run on physical hardware yet.** That covers the ESP32s, Peltiers, sensors, chef-hat
  motors, the iPad, the travel router and the Formlabs print. Every chamber reading in the system so
  far is `simulated` or `replay`.

## Workstreams (PLAN_MASTER §3)

| | Workstream | Implemented | Tested | Simulated / real | Hardware-unverified | Commits |
|---|---|---|---|---|---|---|
| A | NVIDIA molecular (`src/fermentai/molecular/`) | ALCHEMI/AIMNet2 pipeline, Brev scripts, analysis, descriptors | 22 unit tests + integration reproducibility check | **Real** GPU results (campaign 1); analysis re-derivable on the Mac | — (campaign 2 in progress, see Brev) | `efafefa`, `8c76f9e` |
| B | Fermentation model (`src/fermentai/fermentation/`) | ODE kinetics, scenarios, uncertainty, sensitivity, calibration API, ALCHEMI hook | 40+ unit tests; integration test reproduces the committed CSVs without a GPU | **Simulated**, not calibrated to real dough | — | `d92d7a4`, `f940f03`, `8c76f9e` |
| C | ESP32 firmware (`firmware/`) | PlatformIO project, 3 environments (A, B, no-IO), HAL, control, motor, network, camera | Builds SUCCESS; native unit tests 94/94 (`firmware/build_logs/`) | — | **Everything on the board**: flash, sensors, Peltier and safety, motor, camera, Wi-Fi | not committed yet (in progress) |
| D | Virtual devices (`simulated_devices/`) | Two virtual ESP32s, thermal-plant controller model, camera, motor, replay, v1.0/v1.1 client | 34 tests + live integration with the real server | **Simulated** (`source=simulated`); replay → `replay` | n/a | `b6ec68f` |
| E | Backend/API (`src/fermentai/api/`, `contracts/`) | FastAPI, SQLite, contract v1.1 (8 schemas), device auth, dedup, commands, SSE, data endpoints | ~100 API tests; contract `--check` clean; integration suite | — | travel-router LAN | `9bd590e`, `d506d57` (+ uncommitted edits) |
| F | Scientist agents (`src/fermentai/agents/`, `src/fermentai/design/`) | Two agents, sensory parser, offline policy, LLM adapter (claude CLI / API), findings log, agent state, replay demos, in-silico benchmark | 131 agents + 17 design tests; §14 flow in integration | Offline policy is deterministic; benchmark is **in-silico** | — | `d8c5ae5`, `6080941`, `1877589` |
| G | Formlabs geometry (`src/fermentai/geometry/`, `data/stl/`) | Crumb generator, mesh validation, simulation→crumb mapping, renders, photo analysis | 37 tests; integration validity and distinctness check | **Simulated** hypotheses (`provenance: simulated`) | **Not printed**; no real crumb photo compared | `4c961d8`, `c120a16` |
| H | Dashboard/demo (`src/fermentai/dashboard/`) | Offline iPad dashboard, 4 views, SSE, taste flow, vendored Plotly; pitch and demo script | Dashboard tests + live crawl and offline scan in integration; headless Chrome | Shows provenance badges | **Physical iPad** | `271c890`, `03e753a`, `dd3273f` (+ uncommitted edits) |
| I | Integration/QA (`tests/integration/`, `docs/`) | 30 end-to-end tests, network audit, offline check, acceptance runner, these documents | — | — | Physical disconnected-mode test still to be done | not committed |

Other commits: `ec730fe`, `86dbad2`, `ffd8a04` (scaffold and plan), `cfcae76` (master plan).

## Test totals (last run: `docs/test_report.md`)

| Suite | Command | Result |
|---|---|---|
| unit | `uv run pytest tests --ignore=tests/integration` | `405 passed, 1 skipped` (skip: optional `jsonschema` not installed) in 86 s |
| virtual devices | `uv run pytest simulated_devices/tests` | `34 passed` in 1.0 s |
| integration | `uv run pytest tests/integration` | `27 passed, 3 xfailed` in 22 s |
| contract | `uv run python -m fermentai.api.contract --check` | `contracts + firmware_contract examples up to date` |
| offline | `scripts/offline_check.sh` | `RESULT: PASS (offline check)`: 0 non-LAN socket events |
| firmware (native) | `pio test -e native` (firmware workstream) | `94 test cases: 94 succeeded` (from `firmware/build_logs/native_test.log`) |

`uv run pytest tests/` (no `--ignore`) now also collects `tests/integration/`, which adds about 25 s.

## Brev / GPU spend

- **Campaign 1:** `fermentai-gpu`, hyperstack_A6000 (RTX A6000), $0.60/h. Created 03:36:39Z and
  **deleted** 04:44:50Z (hyperstack cannot stop). Instance time 1.14 h, about **$0.68**
  (`data/alchemi/environment/report.md`; Brev's invoice is authoritative).
- **Campaign 2: IN PROGRESS** at the time of writing. A new `fermentai-gpu` (hyperstack_A6000,
  $0.60/h) was requested at 2026-10-31T04:59:48Z and created
  (`data/alchemi/environment/c2_brev_create.log`). The budget is $30 (`.progress/molecular.md`). It
  must be **deleted** once its results are rsynced, because hyperstack does not support stop. Its
  spend is not included above.

## Known bugs (found by the integration suite)

| # | Severity | Bug | Where | Owner | Test |
|---|---|---|---|---|---|
| 1 | **High (demo)** | `GET /api/v1/molecular/summary` returned **HTTP 500** on the committed data. `analysis_summary.json` contains bare `NaN`, which is invalid JSON, and FastAPI refuses to encode it. The dashboard's NVIDIA Molecular Explorer calls this endpoint. **Patched by QA** (one line; see below). The data file should also be written with `null`. | `src/fermentai/api/routes.py` molecular_summary (patched); `src/fermentai/molecular/analysis.py:1040` (writer) | API (patch), Molecular (writer) | `I:test_live_stack.py::test_molecular_summary_has_real_gpu_provenance` |
| 2 | **High (science)**, partly fixed | Context-key mismatch between the API and the pipeline. When QA found it, `build_context` passed `recent_telemetry` / `current_setpoints` / `limits`, but the pipeline reads `telemetry` / `setpoints` / `hardware_limits`, so the agents saw no live telemetry. **Telemetry and hardware_limits were fixed upstream during this QA run** (the test now passes). **Still open:** the setpoints go out as `current_setpoints`, so the agents assume the default 22/27 °C. Example: B set to 24 °C gives "assumed steady 27 °C for 14 h (from setpoints)". The fix is to add `"setpoints": setpoints` to the context. | `src/fermentai/api/feedback.py:131` vs `src/fermentai/agents/offline_policy.py:75` | API (or Agents) | `I:test_inprocess_flow.py::test_live_telemetry_reaches_the_agents` (passes), `::test_current_setpoints_reach_the_agents` (xfail) |
| 3 | Medium | `GET /agents/{id}/state` ignores the API's findings path. `routes.agent_state` calls `get_agent_state(agent_id)` with no `log_path`, so state reads `FERMENTAI_FINDINGS_LOG` or `data/experiments/findings_log.jsonl`, while the API writes `FERMENTAI_FINDINGS_PATH`. The default demo works because both default to the same file. Any override splits them. Two env var names for one file. | `src/fermentai/api/routes.py:426`, `src/fermentai/agents/communication.py:118`, `api/config.py` (`FERMENTAI_FINDINGS_PATH`) | API + Agents | `I:test_inprocess_flow.py::test_agent_state_uses_api_findings_path` (xfail) |
| 4 | Low | Provenance labelling gaps: the top-level `provenance` of `/molecular/summary` is free text ("ALCHEMI outputs as cached…", not a label), and the `/stl` listing has no provenance field. Every underlying file is labelled. | `src/fermentai/api/datafiles.py:75`, `src/fermentai/api/routes.py:538` | API | `I:test_live_stack.py::test_data_provenance_audit_strict` (xfail) |
| 5 | Low | Virtual devices use the **physical** device ids `fermentai-esp32-a/b`, so the device registry cannot tell a virtual board from a real one by id. `source` stays correct. | `simulated_devices/__init__.py:15` | Virtual devices | (observed in `test_both_virtual_chambers_boot_and_post_v11_telemetry`) |
| 6 | Low | `tests/test_api_contract.py:97` is skipped because `jsonschema` is not a dev dependency. The in-repo mini validator covers it. | `pyproject.toml` | Orchestrator | — |
| 7 | Info | `pytest-asyncio` warns that `asyncio_default_fixture_loop_scope` is unset. | `pyproject.toml [tool.pytest.ini_options]` | Orchestrator | — |
| 8 | Info | `firmware/build_logs/variant_b_VERBOSE_flags_evidence_elf2image_quirk.log` records a failed build variant. It is still being worked on by the firmware workstream. The main A, B and no-IO environments succeed. | `firmware/build_logs/` | Firmware | — |

**Out-of-area edit made by QA (permitted one-line fix):** in `src/fermentai/api/routes.py`,
`molecular_summary` now returns `jsonable(datafiles.molecular_summary(...))`, which maps NaN to null.
Without it, test (d) cannot pass and the dashboard's molecular tab shows an error.

## Run the demo

```bash
uv sync                                                  # once
cp .env.example .env && $EDITOR .env                     # set a unique FERMENTAI_API_TOKEN
scripts/start_demo.sh --with-simulated-devices           # no hardware: labelled SIMULATED chambers
#   or: scripts/start_demo.sh                            # real ESP32s on the LAN
# iPad: open the printed  http://<mac-ip>:8000/?token=<FERMENTAI_API_TOKEN>
# Mac:  http://127.0.0.1:8000/

scripts/offline_check.sh                                 # pre-demo disconnected-mode check (~15 s)
scripts/run_acceptance.sh                                # all suites -> docs/test_report.md (~2-3 min)
```

The spoken script and fallbacks are in `docs/demo_script.md`, and the network setup is in
`docs/network_setup.md`.

## Not done / next

1. Physical disconnected-mode test on the travel router with the iPad. Record it in
   `docs/acceptance_tests.md`.
2. Flash both ESP32s and repeat items 5, 6 and 10 with `source=measured`.
3. Fix bug 2 (setpoints) and bug 3, then remove the xfail markers. They are `strict=False`, so they will show as
   XPASS.
4. Delete the campaign-2 Brev instance when its rsync dry-run is clean.
5. Calibrate the kinetics on the first real pH and rise data (`calibrate()`).
