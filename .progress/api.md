# API / hardware-interface progress checkpoint

Owner scope: src/fermentai/api/, configs/hardware.yaml, firmware_contract/, docs/network_setup.md,
scripts/start_demo.sh, scripts/simulate_device.py, tests/test_api_*.py

## Status: COMPLETE (all milestones done) — 59/59 `uv run pytest tests/test_api_*.py` pass

## Done
- configs/hardware.yaml (limits, TTLs, motor bounds, firmware_local_limits)
- api/config.py (Settings.from_env, .env loader, HardwareConfig), schemas.py (contract models),
  db.py (sqlite WAL), auth.py, events.py (SSE from events table), devices.py (ControlService:
  set_desired_setpoint safety path, desired-control, motor, acks, expiry, telemetry, state),
  feedback.py (taste-feedback -> fermentai.agents.pipeline.run_taste_feedback_cycle w/ placeholder),
  datafiles.py, routes.py, app.py (create_app; lazy module-level `app`), contract.py, testing.py
- firmware_contract/README_FOR_ESP32_AGENT.md + 8 generated example JSONs
- scripts/simulate_device.py, scripts/start_demo.sh (live-tested on port 8765 w/ simulated devices)
- docs/network_setup.md
- /simulations/run adapted to real fermentai.fermentation.simulation.simulate(schedule dict, t_end_h,...)

## Known open items
- fermentai.agents.pipeline not present yet at time of writing -> placeholder path used; tested with a
  fake pipeline module. Re-run tests/test_api_feedback.py once it lands.
- Dashboard static dir absent -> placeholder page at "/".

## Resume
`uv run pytest tests/test_api_*.py`; regenerate contract JSON: `uv run python -m fermentai.api.contract --write`.

---------------------------------------------------------------------------------------------
# PHASE 2 (contract v1.1, PLAN_MASTER §4/§13/§14) — COMPLETE
Tests: `uv run pytest tests/test_api_*.py` -> 97 passed, 1 skipped (jsonschema lib not installed;
mini 2020-12 validator in contract.py covers it). simulated_devices/tests 34 passed; full repo 378 passed.
Contract: `uv run python -m fermentai.api.contract --write-schemas` / `--check` (clean).

Done:
- schemas.py v1.1 (TelemetryIn extras, Diagnostics, Command discriminated union, ack v1.1, AgentMessage,
  SimulationResult, ExperimentIn pair/points normalisation, schema_version const in JSON schema)
- db.py: migrate() idempotent (ALTER ADD COLUMN; rebuild telemetry -> nullable timestamp_utc), new tables
  devices/sensor_readings/temperature_schedules/scientist_hypotheses/model_calibrations/molecular_results,
  §13 alias views, dedup unique index
- auth.py: require_operator / require_device / Principal; FERMENTAI_DEVICE_TOKEN_<X>; X-Device-Id
- devices.py: device<->chamber binding (config + auto-bind), dedup, null timestamps never invented,
  sensor_readings w/ unit/provenance/quality, single per-chamber seq counter, commands[] list,
  stop_motor rows on cancel, capture_image, ack v1.1 (result/detail/device_uptime_ms), motor `ran`
- feedback.py: agent_id->sender, in_reply_to/recipient (arrow prefix / reply), decision_source provenance,
  next_experiment.id, hypotheses/schedules/calibrations tables, findings_log_path from settings
- routes: /agents/{id}/state, /demos, /demos/{path}, /devices, /chambers/{id}/readings,
  /chambers/{id}/capture-image, /simulations/{id} in simulation_result_v1 shape; app BodySizeLimit
- contracts/ (8 schemas + examples + README with deviations), firmware README §9 (v1.1), .env.example,
  docs/network_setup.md auth, scripts/simulate_device.py SimulatedDevice v1.1 (default) / v1.0
  (other agent made the script launch simulated_devices by default; `--legacy` = my reference client)

Resume: read contracts/README.md (semantics + deviations), run the tests above.
