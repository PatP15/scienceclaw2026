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
