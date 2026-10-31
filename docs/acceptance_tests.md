# Acceptance tests: PLAN.md §16

Status as of 2026-10-31 ≈ 05:10 UTC, run against the working tree on top of `dd3273f`.
Re-run with `scripts/run_acceptance.sh`, which regenerates `docs/test_report.md`. Validation
caveats for every component are in `docs/validation_status.md`.

Legend:

- **PASS**: demonstrated, with the evidence listed.
- **PASS (software-only, simulated device)**: demonstrated end to end with the labelled virtual
  ESP32s (`simulated_devices`) in place of the physical chambers.
- **HARDWARE-UNVERIFIED**: the hardware part has not been demonstrated.
- **FAIL**: not demonstrated.

Test ids are relative to the repo root. `I:` = `tests/integration/`.

| # | PLAN §16 criterion | Status | Evidence |
|---|---|---|---|
| 1 | Real NVIDIA ALCHEMI energies and forces were calculated on the remote GPU | **PASS** | Brev `fermentai-gpu` (RTX A6000), 159/159 optimisations converged, 72/72 MD runs: `data/alchemi/environment/report.md`, `data/alchemi/results/`, `data/alchemi/logs/`, `data/alchemi/official_example/`; cross-check against upstream aimnet ≤ 5.9 meV. Tests: `I:test_artifacts_reproducible.py::test_alchemi_results_and_gpu_logs_are_on_the_mac`, `tests/test_molecular_*.py`. The GPU run cannot be repeated now because the instance was deleted. The evidence is the preserved outputs and logs. |
| 2 | Results were transferred back to the Mac | **PASS** | rsync dry-run showed 0 differences before deletion (`report.md` Notes). The Mac serves them: `I:test_live_stack.py::test_molecular_summary_has_real_gpu_provenance` (provenance `real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000`). |
| 3 | The Mac can reproduce fermentation forecasts without a GPU | **PASS** | `I:test_artifacts_reproducible.py::test_forecasts_reproduce_on_the_mac_without_gpu` re-runs both scenarios with no torch or CUDA loaded and matches `data/predictions/chamber_{A,B}_prediction.csv` (rtol 2e-4). Also `tests/test_fermentation_*.py` and `scripts/simulate_loaves.py`. |
| 4 | Both crumb STLs are valid and visually distinguishable | **PASS** (not printed) | `data/stl/crumb_chamber_{A,B}_validation.json` passes 10/10 checks; `data/stl/crumb_chamber_comparison.png`. `I:test_artifacts_reproducible.py::test_both_chamber_stls_valid_and_distinguishable`, `tests/test_geometry_*.py`. The Formlabs print is **HARDWARE-UNVERIFIED**. |
| 5 | The API can ingest simulated test telemetry for both chambers | **PASS (software-only, simulated device)** | `I:test_live_stack.py::test_both_virtual_chambers_boot_and_post_v11_telemetry`: uvicorn and `python -m simulated_devices`, contract v1.1 fields on the wire, `timestamp_utc` never invented, per-chamber device binding. Also `simulated_devices/tests/test_end_to_end.py`, `tests/test_api_telemetry.py`. |
| 6 | The API can return independent temperature setpoints | **PASS (software-only, simulated device)** | `I:test_live_stack.py::test_independent_setpoints_round_trip`: B set to 25 °C, device B applies and acks 25 °C, A stays at 22 °C, and an 80 °C request is step-limited. `I:test_inprocess_flow.py::test_judge_text_cannot_bypass_setpoint_limits`. |
| 7 | The dashboard is reachable from an iPad or second browser over a local network | **PASS (software-only)**; physical iPad **HARDWARE-UNVERIFIED** | Server bound to `0.0.0.0` and reached through the Mac's LAN address: `I:test_replay_contract_offline.py::test_lan_bound_server_full_loop_without_external_traffic`, `scripts/offline_check.sh` ("reachable via LAN address", "dashboard index served over the LAN address"). Headless Chrome at iPad widths (`.progress/dashboard.md`). Not yet opened on a physical iPad over the travel router. |
| 8 | The dashboard does not require internet, cloud fonts or CDN resources | **PASS** | `I:test_live_stack.py::test_dashboard_and_every_asset_resolve_without_external_urls` crawls `/` and every asset and import over HTTP, using the dashboard's own scanner. `tests/test_dashboard_offline.py`. The server audit hook logged 0 external DNS or connects (`I:test_live_stack.py::test_server_made_no_outbound_connections`). FastAPI's `/docs` uses a CDN but is not part of the demo. |
| 9 | Judge feedback results in an actual experiment recommendation | **PASS** | PLAN_MASTER §14 sentence: structured preference "More sourness / Lower density / Preserve good rise"; next experiment with A and B schedules, stored as an experiment with provenance `offline_policy`. `I:test_live_stack.py::test_feedback_structured_preference`, `::test_feedback_next_experiment`, `I:test_inprocess_flow.py::test_live_mode_without_llm_falls_back_to_offline_policy`. Live telemetry reaches the agents (`I:test_inprocess_flow.py::test_live_telemetry_reaches_the_agents`). **Caveat:** the agents still assume the default 22/27 °C setpoints instead of the chambers' actual setpoints (known bug, `::test_current_setpoints_reach_the_agents`, xfail). |
| 10 | A motor command can be emitted and logged, with a simulated device acknowledged during software-only testing | **PASS (software-only, simulated device)**; physical chef-hat motor **HARDWARE-UNVERIFIED** | Feedback issues `animate_motor` (3000 ms) for A and B. The virtual devices ack `accepted`, then `completed` at least 3000 ms of device uptime later. `ran` is true only after `completed`: `I:test_live_stack.py::test_motor_commands_acked_accepted_then_completed` (invariant checked on every poll) and `I:test_inprocess_flow.py::test_feedback_motor_ran_only_after_completed_ack` (fake clock, exact). |
| 11 | Distinct scientist agents exchange structured findings | **PASS** | scientist_A and scientist_B send distinct `agent_message_v1` findings. Cross-agent replies carry `in_reply_to`, and the recipient is derived from it. `GET /agents/{A,B}/state` returns real state (not the stub). `I:test_live_stack.py::test_feedback_two_agents_exchange_structured_messages`, `::test_agent_state_is_real[*]`. |
| 12 | The system can clearly explain which data are real, modeled, estimated or replayed | **PASS** (2 labelling gaps, xfail) | `I:test_live_stack.py::test_data_provenance_audit` walks chamber state, telemetry, readings, agent state, prediction CSV rows and JSON, molecular summaries, STL sidecars and demos. Every record carries a label from {real, measured, simulated, replay, estimated, imported_human, offline_policy, llm}. Replay path: `I:test_replay_contract_offline.py::test_replay_cli_labels_records_replay`. Gaps: the top level of `GET /molecular/summary` is free text, and `GET /stl` has no envelope label (`::test_data_provenance_audit_strict`, xfail). |

Supporting checks:

- `I:test_replay_contract_offline.py::test_contract_check_passes`
  (`python -m fermentai.api.contract --check`)
- `I:test_replay_contract_offline.py::test_net_audit_positive_control`, which proves the outbound
  audit detects public IPs and internet hostnames

## How the automated end-to-end run works

`tests/integration/conftest.py::live_stack` starts the real server, with
`fermentai.api.app:app` under uvicorn on a free port. It uses a temp SQLite DB, a temp findings log
and a temp data dir whose `predictions/`, `alchemi/`, `stl/` and `demos/` are symlinks to the
repo's. The server runs inside `tests/integration/net_audit.py`, a PEP 578 audit hook on
getaddrinfo, connect and sendto, using the asyncio loop so no socket bypasses the hook.
`python -m simulated_devices --chambers A B` then runs as a separate process with 1 s telemetry,
1 s polling and the real kinetics for the starter rise. All tests talk to it with plain HTTP.
Nothing is mocked. Runtime is about 25 s for the whole integration suite.

## Physical disconnected-mode test (to do before the demo)

The procedure is in `scripts/offline_check.sh --help` (PHYSICAL PART) and `docs/network_setup.md`
§6. Summary:

1. Travel router WAN unplugged. Mac and iPad on the demo SSID only, iPad cellular off.
   `ping -c1 -t2 1.1.1.1` fails; `ping <router>` succeeds.
2. `scripts/offline_check.sh` → must print `RESULT: PASS` with the WAN off.
3. `scripts/start_demo.sh --with-simulated-devices` (or without the flag with the ESP32s).
4. On the iPad, open the printed `http://<mac-ip>:8000/?token=…`. All four tabs render, A and B update
   live with provenance badges, and the NVIDIA and Digital Twin tabs load from cache.
5. Submit the §14 sentence. Check the preference, the two agents' messages, the next experiment and
   the "Offline Policy" label. Check that both motors show `accepted → completed`
   (`curl -s http://127.0.0.1:8000/api/v1/commands?command_type=motor`). With hardware, the chef
   hats move for ≤ 10 s.

Record results here:

| Date | Router | Devices (real/simulated) | WAN off confirmed | iPad renders | Live telemetry | Feedback → plan | Motors completed | Tester |
|---|---|---|---|---|---|---|---|---|
| _not yet run_ | | | | | | | | |
