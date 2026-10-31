# Virtual devices (PLAN_MASTER §6) — checkpoint

Owner area: `simulated_devices/`, `scripts/simulate_device.py`, this file.

## Done
- Package `simulated_devices/`:
  - `virtual_thermal_plant.py` - controller-test model (NOT a validated physical simulation):
    internal sink -> air -> starter lag, external sink, ambient drift + random walk, door-open
    coupling; local controller with ±0.3 C hysteresis, PI output fraction 0..1 slew-limited,
    60 s dead time on heat<->cool (mode "switching"), fan overrun, over/under-temp + heatsink +
    sensor-stale faults, boot "off", offline hold -> idle.
  - `limits.py` - local limits read from `configs/hardware.yaml: firmware_local_limits` (same
    compiled-in defaults; tests check equality).
  - `virtual_sensors.py` - BME680-like RH (vapour-pressure model), pressure drift, gas
    resistance (VOC load + burn-in; NOT CO2); DS18B20 quantisation; starter height + pH from
    `fermentai.fermentation.simulation.simulate` on the starter-temperature history
    (accelerated clock `--ferment-time-scale`, default 30x), logistic fallback.
  - `virtual_camera.py` - Pillow JPEG of a jar, fill height = starter height, red "SIMULATED"
    banner. Static fixtures in `fixtures/jar_{A,B}_{25..55}mm.jpg` (`--write-fixtures`).
  - `virtual_motor.py` - run clamped to 10 s, completed only after the duration.
  - `network_client.py` - Bearer token (FERMENTAI_DEVICE_TOKEN_A|B -> FERMENTAI_API_TOKEN ->
    default, next token tried on 401/403) + `X-Device-Id`; contract shaping v1.0/v1.1;
    auto fallback v1.1 -> v1.0 on a 422 naming a v1.1 field; re-probe v1.1 every 300 s.
  - `device.py` - VirtualChamber: poll desired-control (2 s, clamped 1-3 s), handles the
    `commands` list (set_setpoint / animate_motor / stop_motor / capture_image / noop) AND
    the v1.0 flat fields, one shared seen-set (no double execution), per-type seq high-water
    marks with server_epoch reset, expiry via ttl_s or expires_at - server_time_utc, ordered ack
    outbox with retry, telemetry outbox (50) with backoff, image uploads.
  - `replay.py` - csv/jsonl/json (incl. backend /telemetry export) -> source "replay",
    timestamps preserved, fresh boot_id per replay session.
  - `cli.py`, `__main__.py`, `chamber_A.py`, `chamber_B.py` (B never has pH).
- `scripts/simulate_device.py` -> thin wrapper around the package (`--legacy` = old loop;
  `SimulatedDevice` class kept for tests/test_api_simdevice.py).
- Tests: `uv run pytest simulated_devices/tests` -> 34 passed (unit + e2e against the real app
  via httpx ASGITransport + a strict-v1.0 mock for the fallback).
- Live runs: uvicorn (working-tree v1.1 backend) with per-device tokens: A+B telemetry, images,
  motor round-trip, capture_image, manual pH provenance, door events, 0 errors. Committed v1.0
  backend (git archive HEAD into scratch): fell back to v1.0 on first 422, 0 errors after.

## Decisions
- set_setpoint is acked `accepted` only (standing command). Acking `completed` made the backend
  mint a new control command_id on every poll (its re-serve rule requires issued/accepted).
- peltier_mode sent as v1.0 values (heat/cool/...), accepted by v1.0 and v1.1.
- A v1.1 cancel produces both a stop_motor list command and the flat "stop" directive; the
  cancelled motor command is acked once (terminal-ack set).

## Next / open
- Seen command ids are in memory only (a process restart could re-run a still-valid motor command
  within its 60 s TTL; same as a real ESP32 reboot without NVS).
- Fault paths (overtemp, heatsink_overtemp) are unit-tested in the controller but not injectable from
  the CLI yet (could add `--inject-fault`).

## Resume
`uv run pytest simulated_devices/tests -q`;
`uv run python -m simulated_devices --chambers A B --base-url http://127.0.0.1:8000 --interval 5`.
