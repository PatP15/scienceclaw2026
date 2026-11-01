# FermentAI shared contracts: revision 1.1 (wire `schema_version` "1.0")

These are the canonical, versioned interfaces between the backend (`src/fermentai/api/`), the
ESP32 firmware (`firmware/`), the virtual devices (`simulated_devices/`), the scientist agents
(`src/fermentai/agents/`) and the dashboard. PLAN_MASTER §4 is the governing spec.

**Never hand-edit the `*.schema.json` files or `examples/`.** They're generated from the
pydantic models in `src/fermentai/api/schemas.py`:

```bash
uv run python -m fermentai.api.contract --write-schemas   # regenerate contracts/ + firmware_contract/ examples
uv run python -m fermentai.api.contract --check           # exit 1 if stale or any fixture fails its schema
uv run pytest tests/test_api_contract.py
```

To change a field: edit `schemas.py`, regenerate, and update this README. Renaming or removing a
field needs orchestrator approval (PLAN_MASTER §4).

## Files

| Schema (JSON Schema draft 2020-12) | Pydantic model | Used by |
|---|---|---|
| `telemetry_v1.schema.json` | `TelemetryIn` | device → `POST /api/v1/telemetry` |
| `command_v1.schema.json` | `Command` (union, discriminated on `action`) | items of `commands[]` in `GET /api/v1/chambers/{id}/desired-control` |
| `acknowledgment_v1.schema.json` | `ControlAckIn` | device → `POST /api/v1/control-ack` |
| `agent_message_v1.schema.json` | `AgentMessage` (mirrors `fermentai.agents.communication.Finding`) | agent messages and findings (`GET /api/v1/agents/{id}/messages` → `content`) |
| `experiment_v1.schema.json` | `ExperimentIn` | `POST /api/v1/experiments` |
| `sensory_feedback_v1.schema.json` | `TasteFeedbackIn` | `POST /api/v1/taste-feedback` |
| `simulation_result_v1.schema.json` | `SimulationResult` | `POST /api/v1/simulations/run`, `GET /api/v1/simulations/{id}` |
| `desired_control_v1.schema.json` (extra) | `DesiredControl` | the whole desired-control response (v1.0 flat fields + v1.1 `commands[]`) |

`examples/` holds one generated example per schema, plus:

- `telemetry_plan_master_s4.json`: the PLAN_MASTER §4 telemetry example, **verbatim**
- `command_plan_master_s13.json`: the PLAN_MASTER §13 command example, **verbatim**
- `*_v1_0_legacy.example.json`: v1.0 payloads. These must keep validating, because v1.1 is additive.

Every value in every example is an illustrative interface fixture, not a measurement or a
model output.

## Transports

The payload contracts don't depend on the transport.

| Transport | Device side | Host side | Spec |
|---|---|---|---|
| **USB serial (current hardware plan)** | newline-delimited JSON over USB CDC at 115200 baud: `hello`, `telemetry{payload: telemetry_v1}`, `ack{payload: acknowledgment_v1}`, `image` chunks, `log` | `uv run python -m fermentai.bridge` forwards to the HTTP API and writes `command{payload: command_v1}`, `time`, `hello_ack` | **`serial_v1.md`** |
| Wi-Fi HTTP (still supported) | device calls the HTTP endpoints directly | FastAPI | this README and `firmware_contract/README_FOR_ESP32_AGENT.md` |

The bridge is a trusted Mac-local HTTP client. It uses each chamber's device token
(`FERMENTAI_DEVICE_TOKEN_<A|B>`, else `FERMENTAI_API_TOKEN`) plus `X-Device-Id`, so device↔chamber
binding, dedup, size limits and acknowledgment semantics are identical on both transports. Bridge
status is at `GET /api/v1/bridge/status`, with `transport: "usb-serial"` and a per-chamber `link`.

## Versioning

- `schema_version` on the wire stays `"1.0"`. Contract **revision 1.1** only adds optional
  fields, new enum values and new endpoints. Every v1.0 payload is still accepted, and every v1.0
  response field is still present.
- An unknown `schema_version` gets HTTP 422 with an explicit message.
- Top-level objects reject unknown fields (422) so typos show up straight away. The exceptions
  are `diagnostics` (extra keys allowed), `ExperimentIn` and `SimulationRunIn`.

## Telemetry (`telemetry_v1`)

- Required: `schema_version`, `chamber_id`, `source`. Everything else is optional and nullable.
  Use `null` for a missing sensor, no placeholder value. NaN and Infinity are rejected.
- `source` (record provenance): `measured` for physical devices, `simulated` for virtual
  devices, `replay` for recorded data. The v1.0 spelling `real` is accepted and **stored as
  `measured`** (the raw value is kept in the payload as `source_as_sent`).
- `field_provenance` (v1.1) overrides provenance for individual fields, for example
  `{"ph": "manual"}`. Values: `measured | simulated | replay | manual | estimated`.
- **Clock (PLAN_MASTER §4):**
  - `timestamp_utc` may be `null`, with `timestamp_quality` set to `unsynced`.
  - The server leaves `timestamp_utc` unset. It stores `NULL`, adds the quality flag
    `timestamp_unavailable`, and always records its own `received_at_utc`.
  - Ordering uses `boot_id`, `sequence_number` and `device_uptime_ms`.
  - API rows also carry `time_basis`: `device` or `server_receipt`.
- **Dedup:** when `device_id`, `boot_id` and `sequence_number` are all present, a repeat returns
  `200 {"duplicate": true, "telemetry_id": <original>}` and nothing is stored again. Devices
  should retry failed posts with the **same** sequence number.
- `sensor_readings`: every non-null measurement field is also stored as its own row, with
  `unit`, `provenance` and `quality`. Quality comes from `diagnostics.sensor_health[<field or
  sensor>]`, using sensor names such as `bme680`, `sht31`, `ds18b20_starter`,
  `ds18b20_heatsink_internal`, `ds18b20_heatsink_external`, `ph`, `camera` and `door`. Read them
  with `GET /api/v1/chambers/{id}/readings?field=`.
- `peltier_mode`: `idle | heat | cool | heating | cooling | switching | fault | off`. The long
  forms come from PLAN_MASTER §4. DB columns and events normalise them to `heat` and `cool`.
- `gas_resistance_ohms` is a raw BME680 resistance. **It is not CO2.**
- Maximum request body: **64 KB** (larger requests get 413).

## Commands (`command_v1`) and desired-control

`GET /api/v1/chambers/{id}/desired-control` returns every v1.0 flat field
(`command_id`, `sequence_number`, `temperature_setpoint_c`, `ttl_s`, `motor{...}`, ...). It also
returns `control_mode` and **`commands`**: the pending command objects for that chamber.

```json
{"schema_version": "1.0", "command_id": "…", "chamber_id": "A", "experiment_id": "experiment_001",
 "action": "set_setpoint" | "animate_motor" | "stop_motor" | "capture_image" | "noop",
 "parameters": {...}, "issued_at_utc": "…Z", "expires_at_utc": "…Z", "sequence_number": 42}
```

| action | parameters | created by |
|---|---|---|
| `set_setpoint` | `{setpoint_c, control_mode: auto\|hold\|idle}` | any setpoint or control-mode change (always through the clamped `set_desired_setpoint()`), and re-issued with a fresh id about every 45 s while unchanged (TTL 90 s) |
| `animate_motor` | `{duration_ms ≤ 10000, pattern: wiggle\|nod\|spin}` | `POST /chambers/{id}/motor`, and taste feedback (3000 ms on both chambers) |
| `stop_motor` | `{target_command_id}` | `DELETE /api/v1/commands/{animate_motor id}` |
| `capture_image` | `{}` | `POST /chambers/{id}/capture-image`. The device uploads with `POST /images`, then acks. |
| `noop` | `{}` | reserved (never issued today) |

Semantics:

- `commands[]` lists commands that are **not yet acknowledged and not expired**, in ascending
  `sequence_number`. Once acked, a command disappears from the list.
- `sequence_number` is **one monotonic counter per chamber across all actions**, persisted in
  SQLite. Process commands in ascending order and ignore any number ≤ the highest you've seen.
  Reset that tracking when `server_epoch` changes, because it only changes when the DB is wiped.
  v1.0 per-type monotonicity still holds.
- **Expiry without a synced clock:** remaining validity is
  `expires_at_utc − server_time_utc`, where `server_time_utc` comes from the same response.
  Measure it from receipt on the device's monotonic clock. Never execute an expired command;
  ack it with `expired`.
- The motor command can't carry setpoint or Peltier fields. Parameters are strict, so the schema
  and the server reject them.
- The device enforces its own limits no matter what it receives
  (`firmware_contract/README_FOR_ESP32_AGENT.md` §5).

## Acknowledgments (`acknowledgment_v1`)

v1.1 shape (preferred):

```json
{"command_id": "…", "device_id": "fermentai-esp32-a", "chamber_id": "A",
 "status": "accepted|rejected|completed|expired|failed", "device_uptime_ms": 123456,
 "detail": "optional text ≤300", "result": {"applied_setpoint_c": 24.0}}
```

The v1.0 shape (`schema_version`, `sequence_number`, `timestamp_utc`, `applied_setpoint_c`,
`message`, plus status `cancelled`) is still accepted.

- `set_setpoint`: ack `accepted` with `result.applied_setpoint_c`, the value after the local
  clamp.
- `animate_motor`: ack `accepted` when the motor starts, then `completed` when it stops (for
  example `result: {"ran_ms": 3000}`). **A motor counts as having run only after `completed`.**
  `GET /commands` and chamber state expose `ran` / `motor_ran`, and queued commands are never
  reported as run.
- `stop_motor`: ack `completed` once the motor is stopped.
- Unacked commands are logged as `expired_unacked`, or `superseded` when a newer command
  replaced them.
- Errors: 404 unknown `command_id`; 409 when the command belongs to another chamber; 403 for a
  device or chamber binding violation. Maximum body: 16 KB.

## Device authentication and isolation

| Credential | Env | May call |
|---|---|---|
| Operator token | `FERMENTAI_API_TOKEN` | everything; can act as any device (dashboard, agents, Mac-local simulators) |
| Device token, chamber A | `FERMENTAI_DEVICE_TOKEN_A` | telemetry, images, desired-control, control-ack **for chamber A only** |
| Device token, chamber B | `FERMENTAI_DEVICE_TOKEN_B` | the same, chamber B only |

- If a chamber's device token isn't set, its device falls back to the operator token (v1.0
  behaviour).
- A device token on an operator endpoint (setpoint, motor, taste-feedback, ...) gets 403.
- Header `X-Device-Id` (recommended): if it's present together with a body `device_id`, the two
  must match (403 otherwise).
- **Binding:** device ids listed in `configs/hardware.yaml` are bound to their chamber
  (`fermentai-esp32-a` → A, `fermentai-esp32-b` → B, plus the simulated ids listed there).
  An unlisted id is bound to the first chamber it writes (`devices` table, `device` SSE event)
  and can never write another chamber. Chamber state is fully per-chamber, so one device can't
  overwrite the other's data.
- Read-only GETs are unauthenticated, for the dashboard on the isolated demo LAN.

## Other v1.1 endpoints

- `GET /api/v1/agents/{id}/state` calls `fermentai.agents.state.get_agent_state(agent_id)` when
  that module exists. Otherwise it returns `{"available": false, "stub": true, ...}`, derived from
  the API's message, hypothesis and setpoint records.
- `GET /api/v1/demos` and `GET /api/v1/demos/{path}` serve `data/demos/*.json` (scripted and
  replay scenarios).
- `GET /api/v1/simulations/{id}` returns the stored run in the `simulation_result_v1` shape, plus
  `request` and `created_at_utc`.
- `GET /api/v1/devices` is the device registry. `GET /api/v1/chambers/{id}/readings` returns
  per-field readings. `POST /api/v1/chambers/{id}/capture-image` issues a capture command.
- Request size limits: telemetry 64 KB, ack 16 KB, image 2 MB JPEG (2 MB + 64 KB request),
  everything else 1 MB.

## Database (PLAN_MASTER §13)

Tables: `telemetry`, `sensor_readings`, `devices`, `chamber_control`, `setpoint_changes`,
`commands`, `acks`, `experiments`, `temperature_schedules`, `taste_feedback`, `agent_messages`,
`scientist_hypotheses`, `model_calibrations`, `molecular_results`, `images`, `simulations`,
`events`.

The §13 names `chambers`, `camera_images`, `experiment_definitions`, `simulation_runs`,
`hardware_commands`, `command_acknowledgments`, `scientist_messages` and `sensory_feedback` are
read-only SQL views over the corresponding tables.

Migrations (`db.migrate`) are idempotent and run on every start. A v1.0 DB gets the new columns,
and its `telemetry` table is rebuilt so `timestamp_utc` becomes nullable.

## Deviations and clarifications relative to the v1.1 brief

1. `peltier_mode` accepts both v1.0 `heat`/`cool` and PLAN_MASTER `heating`/`cooling`. Without
   this, the §4 fixture wouldn't validate.
2. `source: "real"` is accepted as an alias and stored as `measured`.
3. One extra schema, `desired_control_v1.schema.json`, describes the full envelope.
4. In `command_v1`, `experiment_id` and `sequence_number` are optional so the verbatim §13
   example validates. The server always sends both. `animate_motor.parameters.pattern` defaults
   to `wiggle`. `stop_motor.parameters.target_command_id` was added.
5. Command objects carry no `ttl_s`. Compute remaining validity from the envelope's
   `server_time_utc`, as described above.
6. Sequence numbers are one counter per chamber shared by all actions (it was per type in v1.0).
   The values stay monotonic per type, so v1.0 devices are unaffected.
7. Acks also accept v1.0 status `cancelled`. In v1.1, cancelling is a separate `stop_motor`
   command, acked `completed`.
8. `diagnostics` allows extra keys.
9. Unregistered device ids auto-bind to the first chamber they write, instead of being rejected.
   This avoids breaking virtual devices with ids nobody has listed. Add ids to
   `configs/hardware.yaml` to pin them.
10. `agent_message_v1` adds an optional `to_agent_id`. The API also derives a recipient from
    `"[scientist_A → scientist_B]"` content prefixes, or from `in_reply_to`.
11. `experiment_v1` also accepts `[t_h, setpoint_c]` pairs and `{"points": [...]}` per-chamber
    objects (the pipeline style). The schema documents the canonical object form.
12. The v1.0 quality flag `timestamp_missing_server_time_used` is gone. The server no longer
    substitutes a time; the flag is now `timestamp_unavailable`.
13. Image uploads without a `timestamp_utc` still use server receipt time as the image
    timestamp. The null-timestamp rule is implemented for telemetry, as the brief specifies.
