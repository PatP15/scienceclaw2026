# FermentAI firmware contract v1.0 (FROZEN)

This is the interface between each chamber's ESP32-S3 and the FermentAI backend running on the Mac.
It is **frozen at schema_version `"1.0"`**. Don't rename, remove or change the type of any field.
If you need something new, ask the backend owner to add an optional field. The server rejects
unknown fields with HTTP 422, so typos show up straight away.

Source of truth:

- Pydantic models: `src/fermentai/api/schemas.py`
- Example JSON in this folder: generated from those models by
  `uv run python -m fermentai.api.contract --write`. `tests/test_api_contract.py` checks that
  the files stay in sync with the models.
- Limits: `configs/hardware.yaml`
- Reference client (Python): `scripts/simulate_device.py`. It's a simulated ESP32 that follows
  this contract, and it's the quickest way to see the exact request/response sequence.

All example values are illustrative fixtures, not real sensor readings.

---

## 1. Hardware per chamber (two identical chambers, `A` and `B`)

| Part | Purpose | Telemetry field(s) |
|---|---|---|
| ESP32-S3-CAM (camera module) | controller, Wi-Fi, side-view camera | images via `POST /images` |
| Peltier module + H-bridge/driver, hot-side heatsink + fan | heating/cooling | `peltier_mode`, `peltier_duty_percent`, `fan_running`, `heatsink_temperature_c` |
| BME680 | chamber air temp, RH, gas resistance | `chamber_temperature_c`, `relative_humidity_percent`, `gas_resistance_ohms` |
| DS18B20 (waterproof) or similar | dough/starter temperature | `starter_temperature_c` |
| Heatsink thermistor/DS18B20 (recommended) | Peltier hot-side protection | `heatsink_temperature_c` |
| pH probe + analog front end (**optional, experimental**, only one exists) | periodic pH | `ph`, `ph_method` |
| Magnetic reed door switch | door state | `door_open` |
| Small servo/geared motor ("chef-hat" automaton) | decorative animation only | `motor_running` |
| Distance sensor or camera-based measurement (optional) | starter/dough height | `starter_height_mm` |

The BME680 gas resistance is a raw VOC-sensitive resistance. **It is not CO2.** Never convert it
to ppm CO2.

pH: the probe isn't necessarily food-contact certified. Report `ph` only when you actually took a
reading. Set `ph_method` to one of `probe_immersed`, `probe_sample` (separate non-edible sample),
`strip` or `manual`. In all other cases send `null`.

---

## 2. Network, base URL and auth

- The ESP32s, the Mac and the iPad all join the dedicated demo access point (see
  `docs/network_setup.md`). There's no internet dependency.
- Base URL: `http://<mac-ip>:8000` (for example `http://192.168.8.10:8000`) or
  `http://fermentai.local:8000` if mDNS resolution works on the ESP32. Make it a build-time or
  NVS-configurable constant, and fall back to the static IP.
- Plain HTTP on the isolated LAN. TLS isn't used in v1.0.
- **Every request** from the ESP32 must send:
  ```
  Authorization: Bearer <FERMENTAI_API_TOKEN>
  Content-Type: application/json            (except image uploads)
  ```
  The token is the shared `FERMENTAI_API_TOKEN` from the Mac's `.env`. Store it in NVS or a
  build flag. **Never commit it.** A missing token returns 401 and a wrong token returns 403.
- No Claude, Anthropic or other cloud credentials ever go on the ESP32.
- Timestamps are ISO-8601 UTC with `Z`, for example `2026-10-31T12:00:00Z`. The ESP32 has no NTP
  on an offline LAN, so you can set the clock from the `server_time_utc` value in every response.
  If you have no valid clock, send `"timestamp_utc": null`. The server then stamps the record and
  flags it.

---

## 3. Endpoints used by the ESP32

| Method and path | Cadence |
|---|---|
| `POST /api/v1/telemetry` | every 10 s (`telemetry.recommended_interval_s`) |
| `GET /api/v1/chambers/{chamber_id}/desired-control` | every 5 s (`poll_interval_s` in the response) |
| `POST /api/v1/control-ack` | once per new command id (and motor lifecycle events) |
| `POST /api/v1/images` | about every 300 s (optional) |

Treat any 2xx as success. On 4xx, log the response body (it's JSON `{"detail": ...}`) and don't
retry the same payload in a tight loop. On 5xx or network errors, retry with backoff
(1 s, 2 s, 4 s, capped at 30 s) and keep controlling locally the whole time (see §5).

### 3.1 `POST /api/v1/telemetry`

Request: see `telemetry_example.json`.

```json
{
  "schema_version": "1.0",
  "chamber_id": "A",
  "experiment_id": "trial_001",
  "timestamp_utc": "2026-10-31T12:00:00Z",
  "chamber_temperature_c": 24.5,
  "starter_temperature_c": 23.9,
  "relative_humidity_percent": 72.0,
  "gas_resistance_ohms": 105432.0,
  "ph": null,
  "ph_method": null,
  "starter_height_mm": 28.0,
  "door_open": false,
  "peltier_mode": "idle",
  "temperature_setpoint_c": 24.0,
  "motor_running": false,
  "source": "real",
  "quality_flags": [],
  "device_id": "esp32-chamber-a",
  "firmware_version": "1.0.0",
  "uptime_s": 3600.0,
  "wifi_rssi_dbm": -58.0,
  "heatsink_temperature_c": 31.2,
  "fan_running": true,
  "peltier_duty_percent": 0.0,
  "last_command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
  "last_sequence_number": 41,
  "fault_codes": []
}
```

Rules:

- Required fields: `schema_version` (must be `"1.0"`), `chamber_id` (`"A"` or `"B"`), and
  `source`. Everything else is optional and may be `null` or omitted.
- **Missing or failed sensor:** send `null`, never a fake value, and add a quality flag such as
  `"bme680_read_failed"` or `"ds18b20_disconnected"`. The JSON must not contain NaN or Infinity.
  Those values are rejected with 422.
- `source`: real hardware sends `"real"`. `"simulated"` and `"replay"` are only for software stand-ins.
- `peltier_mode` is one of `idle`, `heat`, `cool`, `switching` (inside the heat/cool dead time),
  `fault` or `off`.
- `temperature_setpoint_c` is the setpoint the device is actually using, after local limits.
- `fault_codes` holds active local safety faults, for example `"overtemp"`, `"heatsink_overtemp"`,
  `"sensor_stale"` or `"fan_fail"`.
- Suggested quality flags: `door_open_during_reading`, `sensor_warming_up`,
  `ph_uncalibrated`, `height_estimate_low_confidence`.

Response (200): see `telemetry_response_example.json`.
`{"ok":true,"telemetry_id":1234,"chamber_id":"A","received_at_utc":"...","server_time_utc":"...","quality_flags":[...]}`.
The server may add flags of its own, for example `device_clock_skew_<n>s`.

Errors: 401/403 auth; 404 unknown chamber; 422 validation. A 422 includes a clear message for an
unsupported `schema_version`.

### 3.2 `GET /api/v1/chambers/{chamber_id}/desired-control`

Response (200): see `command_example.json` and `command_no_motor_example.json`.

```json
{
  "schema_version": "1.0",
  "command_type": "control",
  "command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
  "sequence_number": 42,
  "server_epoch": "3f9a1c2b7d4e",
  "issued_at_utc": "2026-10-31T12:00:00Z",
  "expires_at_utc": "2026-10-31T12:01:30Z",
  "ttl_s": 90,
  "server_time_utc": "2026-10-31T12:00:00Z",
  "chamber_id": "A",
  "experiment_id": "trial_001",
  "temperature_setpoint_c": 24.0,
  "peltier_mode_hint": "heat",
  "motor": {
    "command_id": "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f",
    "action": "run",
    "run": true,
    "duration_s": 5.0,
    "pattern": "wiggle",
    "sequence_number": 7,
    "ttl_s": 60,
    "expires_at_utc": "2026-10-31T12:01:00Z"
  },
  "limits": {"min_setpoint_c": 4.0, "max_setpoint_c": 35.0, "max_step_c_per_update": 3.0,
             "motor_max_duration_s": 10.0},
  "poll_interval_s": 5
}
```

The response carries two independent commands:

1. The **control command**: `command_id`, `sequence_number` and `temperature_setpoint_c`.
2. The **motor command**: everything inside `motor`, which has its own `command_id` and
   `sequence_number`.

They're acked separately. The motor command can never change the setpoint or the Peltier.

#### Command semantics (both types)

- **Expiry is relative.** A command is valid for `ttl_s` seconds measured on the ESP32's own
  monotonic clock (`millis()`) from the moment the response was received. Don't rely on comparing
  `expires_at_utc` with an unsynchronised RTC. If `ttl_s <= 0`, the command is stale: don't apply
  it. You can ack it with `"status": "expired"`.
- **Re-delivery.** The server re-serves the same control `command_id` on each poll until fewer
  than 45 s remain, then issues a new id with `sequence_number + 1`. A changed setpoint gets a new
  id immediately. If the `command_id` equals one you've already processed, it's a no-op: don't
  re-ack and don't re-run.
- **Sequence numbers** increase monotonically per chamber and per command type (`control` and
  `motor` are counted separately). Ignore a command whose `sequence_number` is lower than the
  highest you've applied for that type. That's a replay or a stale response.
- **`server_epoch`** changes only if the server database was wiped. When it changes, reset your
  "highest sequence seen" for both types.
- **`peltier_mode_hint`** (`heat`, `cool`, `hold`, `idle` or `unknown`) is advisory only. Your
  local controller decides the actual mode. The hint can change between deliveries of the same
  `command_id`.
- **`limits`** are informational. The ESP32 enforces its own compiled-in limits (§5) no matter
  what is received.

#### Motor directive

| `action` | meaning |
|---|---|
| `"run"` | Start the animation for `min(duration_s, 10 s)` if this `motor.command_id` is new, its `sequence_number` is current, `ttl_s > 0`, and the door is closed. Ack `accepted` when it starts and `completed` when it finishes. If you won't run it (expired, door open, fault), ack `rejected` with a `message`. |
| `"stop"` | Stop the motor immediately if it's running, and ack that `command_id` with `cancelled` (once). This is the result of `DELETE /api/v1/commands/{id}` on the server. |
| `"none"` | Nothing to do. `command_id` is `null`. |

The motor is decorative. It signals that "a new experiment was proposed." It doesn't mix, feed
or bake anything.

### 3.3 `POST /api/v1/control-ack`

Send one ack per new control `command_id` (status `accepted`, or `rejected` or `expired`). For
motor commands, send `accepted` when the motor starts, then `completed` or `failed` (or
`cancelled` after a stop directive). Examples: `control_ack_example.json`, `motor_ack_example.json`.

```json
{
  "schema_version": "1.0",
  "command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
  "chamber_id": "A",
  "status": "accepted",
  "sequence_number": 42,
  "timestamp_utc": "2026-10-31T12:00:02Z",
  "applied_setpoint_c": 24.0,
  "message": null
}
```

- `status` is one of `accepted`, `rejected`, `completed`, `failed`, `cancelled` or `expired`.
- `applied_setpoint_c` is the setpoint actually in force after your local clamp. If it differs
  from what was commanded, say why in `message` (300 characters max).

Response (200): `control_ack_response_example.json`, i.e.
`{"ok":true,"command_id":"...","command_type":"control","command_status":"accepted","late":false,"server_time_utc":"..."}`.
`late: true` means the server had already marked the command as expired.
Errors: 404 unknown `command_id`; 409 when the command belongs to another chamber; 422 validation.

On the server, commands that are never acked are logged as `expired_unacked`, or as `superseded`
when a newer command replaced them. The dashboard shows this.

### 3.4 `POST /api/v1/images` (optional)

You can send it in either of two forms. The raw form is simplest on the ESP32:

**Raw JPEG body:**

```
POST /api/v1/images?chamber_id=A&source=real&timestamp_utc=2026-10-31T12:00:00Z
Authorization: Bearer <token>
Content-Type: image/jpeg

<JPEG bytes>
```

**Multipart:** `multipart/form-data` with the fields `file` (JPEG), `chamber_id`, `source`
(`real`, `simulated` or `replay`), and the optional `timestamp_utc` and `experiment_id`.

- The body must be a JPEG starting with `FF D8` and no larger than 3,000,000 bytes (aim for
  ≤ 200 KB: SVGA, quality around 12).
- Response: `image_upload_response_example.json`. The file is stored under
  `data/images/<chamber>/`.
- Errors: 413 too large; 415 wrong content type; 422 not a JPEG, missing `source`, or bad timestamp.
- Never block temperature control while capturing or uploading. Run the upload in a separate
  task with a timeout.

---

## 4. Polling loop (reference)

```
every 5 s   : GET desired-control
              -> handle control command (apply + ack if new/valid)
              -> handle motor directive (run/stop + ack)
every 10 s  : POST telemetry
every ~300 s: POST image (optional, non-blocking)
continuously: local control loop (≥ 1 Hz) + safety interlocks (§5), independent of Wi-Fi
```

`scripts/simulate_device.py` implements exactly this loop.

---

## 5. What the ESP32 must enforce LOCALLY (whatever any command says)

The server clamps setpoints too (4–35 °C, at most 3 °C per update, rate-limited). The firmware
must **not rely on that**. The following must be enforced in firmware, with values compiled in
from `configs/hardware.yaml: firmware_local_limits`:

1. **Setpoint limits.** Clamp every received setpoint to **4.0–35.0 °C** before using it, and
   report the clamped value in `temperature_setpoint_c` and `applied_setpoint_c`.
2. **Over/under-temperature cutoff.** If the chamber temperature goes above 38 °C or below 2 °C,
   switch the Peltier off, set `peltier_mode: "fault"`, and add `"overtemp"` or `"undertemp"` to
   `fault_codes`. Latch the fault until the temperature is back in range for 60 s.
3. **Sensor failure.** If there's no valid chamber temperature for 30 s (read errors, implausible
   values such as < -20 or > 80 °C, or a value stuck for a long time), switch the Peltier off.
   Report the sensor as `null`, set the quality flag and the fault code `"sensor_stale"`.
   Never run the Peltier open-loop.
4. **Fan protection.** The heatsink fan must run whenever the Peltier is energised, and for
   **30 s after** it stops. If the heatsink goes above 60 °C, or the fan fails (when you have a
   tach or current sense), switch the Peltier off immediately and raise
   `"heatsink_overtemp"` / `"fan_fail"`.
5. **Safe heat↔cool switching.** Never reverse the Peltier polarity directly. Go to off, report
   `peltier_mode: "switching"`, wait at least **60 s of dead time**, then energise in the new
   direction. Use hysteresis of ±0.3 °C or more around the setpoint to avoid chattering.
   Ramp the PWM duty rather than stepping it.
6. **Door open.** While the reed switch reports the door open, put the Peltier in idle (off, fan
   may keep running), refuse to start the motor (ack `rejected`, message `"door open"`), and stop
   a running motor. Report `door_open: true` and add the quality flag `door_open_during_reading`.
7. **Motor limits.** Never run the motor for more than **10 s per command**, whatever
   `duration_s` says. Use a hardware or RTOS timer, not just the polling loop. The motor must not
   share a control path with the Peltier driver. A motor fault must never affect the Peltier.
8. **Lost connection.** If no valid (unexpired) control command has arrived for **1800 s**, go to
   `peltier_mode: "idle"` (passive). Until then, keep holding the last validly applied setpoint.
   Never execute an expired command, and never replay an old motor command after reconnecting.
9. **Boot state.** Power up with the Peltier off and the motor off. Only start controlling after
   the sensors are valid and a fresh control command has been received.
10. **Electrical.** Use hardware current limiting and fusing appropriate to the Peltier module,
    and a driver able to disable the output on brown-out or reset (with a pull-down on the enable
    line). Software limits don't replace these.

No LLM, agent or judge text can bypass these limits. Everything that reaches the ESP32 has gone
through the server's `set_desired_setpoint()` clamp, and then through your local clamp again.

---

## 6. Behaviour examples

| Situation | ESP32 does |
|---|---|
| New control command, `ttl_s=90`, setpoint 24 | clamp to 24 locally, apply, ack `accepted` with `applied_setpoint_c: 24.0` |
| Same `command_id` again on the next poll | nothing (no re-ack) |
| Command with `sequence_number` lower than last applied (same epoch) | ignore |
| Response delayed, `ttl_s: 0` | don't apply; optionally ack `expired` |
| Setpoint 40 °C somehow received | apply 35 °C, ack `accepted` with `applied_setpoint_c: 35.0` and message `"clamped locally"` |
| `motor.action: "run"`, door closed | run ≤ 10 s, ack `accepted`, then `completed` |
| `motor.action: "run"`, door open | ack `rejected` with message `"door open"` |
| `motor.action: "stop"` | stop now, ack `cancelled` once |
| Wi-Fi drops for 10 min | keep the last setpoint, all interlocks active, retry with backoff |
| Wi-Fi drops for 40 min | Peltier idle (offline hold elapsed) |
| BME680 read fails | `chamber_temperature_c: null`, flag `bme680_read_failed`. If > 30 s, Peltier off and `sensor_stale` |

---

## 7. Testing without the real backend

```bash
scripts/start_demo.sh                                         # backend on 0.0.0.0:8000
curl -s -H "Authorization: Bearer $FERMENTAI_API_TOKEN" \
     http://<mac-ip>:8000/api/v1/chambers/A/desired-control | jq
curl -s -X POST -H "Authorization: Bearer $FERMENTAI_API_TOKEN" -H 'Content-Type: application/json' \
     --data @firmware_contract/telemetry_example.json http://<mac-ip>:8000/api/v1/telemetry
# trigger a motor animation to test the device:
curl -s -X POST -H "Authorization: Bearer $FERMENTAI_API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"duration_s": 5, "pattern": "wiggle"}' http://<mac-ip>:8000/api/v1/chambers/A/motor
# cancel it:
curl -s -X DELETE -H "Authorization: Bearer $FERMENTAI_API_TOKEN" http://<mac-ip>:8000/api/v1/commands/<id>
# command + ack log:
curl -s http://<mac-ip>:8000/api/v1/commands?chamber_id=A | jq
```

The example JSON files in this folder can be posted as-is. (`telemetry_example.json` is labelled
`source: "simulated"` because it's a fixture.)

## 8. Changelog

- **1.0** (2026-10-30): initial frozen contract.
