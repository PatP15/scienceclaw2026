# FermentAI serial protocol v1 (`serial_v1`, line version `"1.0"`)

The ESP32 chambers connect to the Mac over **USB serial** (native USB CDC on the ESP32-S3). This
is the transport between the device and the bridge, which is the Mac-side process
`uv run python -m fermentai.bridge`.

The application payloads are exactly the existing contracts in this folder:
`telemetry_v1`, `acknowledgment_v1` and `command_v1`. The bridge forwards them to the unchanged
HTTP API (`POST /api/v1/telemetry`, `POST /api/v1/control-ack`,
`GET /api/v1/chambers/{id}/desired-control`), so the API, DB, dashboard and agents don't know or
care which transport a device uses.

```
 ESP32-S3 (chamber A) ──USB CDC──┐
                                 ├─ fermentai.bridge (Mac) ──HTTP──> FastAPI :8000 ──> SQLite / dashboard / agents
 ESP32-S3 (chamber B) ──USB CDC──┘
```

## 1. Framing

| | |
|---|---|
| Physical | USB CDC-ACM (macOS `/dev/cu.usbmodem*`, Linux `/dev/ttyACM*`), **115200 baud** 8N1 by default (configurable: `--baud`, firmware build flag). Baud is nominal on native USB CDC. |
| Encoding | UTF-8, **one JSON object per line**, terminated by `\n`. A preceding `\r` (CRLF) is tolerated. Empty lines are ignored. |
| Max line | **8192 bytes** including the newline. Longer lines are discarded whole by the receiver (counted as `overlong`). |
| Envelope | every line has `"type"` and `"v": "1.0"`. Receivers ignore unknown extra keys. A different major `v` is rejected. Unknown `type` values are ignored and counted. |
| Numbers | finite numbers only. Use `null` for missing or failed readings. If NaN or Infinity slips through, the bridge replaces it with `null` and adds the quality flag `bridge_nan_replaced_with_null`. |
| Non-JSON | any line that isn't a JSON object (ESP-ROM boot banner, `Serial.println` debug output, a truncated line) is treated as **device log text**. It's logged, shown in bridge status (`recent_logs`), and never breaks the link. |

The JSON should be compact (no pretty-printing): one `serializeJson()` call per message,
followed by a single `Serial.write('\n')`.

## 2. Device → host messages

### `hello`

Sent at boot. Re-send it **every 30 s until a `hello_ack` arrives**, and send it again after any
reconnect you can detect.

```jsonl
{"type":"hello","v":"1.0","device_id":"fermentai-esp32-a","chamber_id":"A","firmware_version":"1.1.0","boot_id":"b7c1e2f0","capabilities":["telemetry","ack","commands","set_setpoint","animate_motor","stop_motor","capture_image","image_chunks","time_sync"],"uptime_ms":1530}
```

- `device_id` and `chamber_id` are required. They must match `configs/hardware.yaml`
  (`fermentai-esp32-a` → A, `fermentai-esp32-b` → B).
- `boot_id` is random at each boot. A new `boot_id` tells the bridge to re-send every pending
  command.
- `simulated: true` (optional, default false) marks a simulator. The bridge then labels all its
  data `simulated` and never `measured`. Real firmware omits it, or sends `false`.

### `telemetry`

`payload` is **exactly a `telemetry_v1` object** (`contracts/telemetry_v1.schema.json`).

```jsonl
{"type":"telemetry","v":"1.0","payload":{"schema_version":"1.0","device_id":"fermentai-esp32-a","chamber_id":"A","experiment_id":null,"boot_id":"b7c1e2f0","sequence_number":17,"timestamp_utc":null,"timestamp_quality":"unsynced","device_uptime_ms":86000,"chamber_temperature_c":24.3,"starter_temperature_c":23.9,"internal_heatsink_temperature_c":31.2,"external_heatsink_temperature_c":37.1,"relative_humidity_percent":72.0,"pressure_pa":101325.0,"gas_resistance_ohms":105432.0,"ph":null,"ph_method":null,"starter_height_mm":null,"door_open":false,"peltier_mode":"heating","peltier_output_fraction":0.35,"temperature_setpoint_c":25.0,"motor_running":false,"source":"measured","quality_flags":[],"diagnostics":{"free_heap_bytes":182344,"firmware_version":"1.1.0","last_command_id":null,"sensor_health":{"bme680":"ok","ds18b20_starter":"ok","ph":"missing"}}}}
```

- `sequence_number` increments per telemetry line within a boot. The server de-duplicates on
  (`device_id`, `boot_id`, `sequence_number`), so resending after a reconnect is safe.
- `source`: real hardware sends `"measured"`. If it's missing or `"real"`, the bridge writes
  `"measured"`. A device that sends `"simulated"` or `"replay"` keeps that label.
- **Clock.** The device must not need host time. Send `timestamp_utc: null` with
  `"timestamp_quality": "unsynced"` until a `time` line has arrived (see §3). The server always
  records its own receipt time.

### `ack`

`payload` is **exactly an `acknowledgment_v1` object**.

```jsonl
{"type":"ack","v":"1.0","payload":{"command_id":"b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f","device_id":"fermentai-esp32-a","chamber_id":"A","status":"accepted","device_uptime_ms":90210,"detail":"running 3.0 s"}}
{"type":"ack","v":"1.0","payload":{"command_id":"b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f","device_id":"fermentai-esp32-a","chamber_id":"A","status":"completed","device_uptime_ms":93215,"detail":"motor ran 3000 ms","result":{"ran_ms":3000}}}
{"type":"ack","v":"1.0","payload":{"command_id":"6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10","device_id":"fermentai-esp32-a","chamber_id":"A","status":"accepted","device_uptime_ms":90100,"result":{"applied_setpoint_c":24.0}}}
```

### `image` (optional)

A JPEG sent as independent base64 chunks: each chunk is a raw slice of at most 4096 bytes,
base64-encoded on its own. The bridge reassembles the chunks, checks `bytes` and `sha256`, and
uploads the result with `POST /api/v1/images`. The total must be ≤ 2 MB, and incomplete images
are dropped after 60 s. If this was a `capture_image` command, ack it separately.

```jsonl
{"type":"image","v":"1.0","chamber_id":"A","device_id":"fermentai-esp32-a","uptime_ms":95000,"bytes":5,"sha256":"cb953db6ca4673116a6853704070b0c7edcc0e305911fd00d8a449643a0dc268","encoding":"base64-chunks","chunk":"1/1","data":"/9j/2wA="}
```

(The sample is 5 bytes of JPEG header with a matching sha256. It only shows the field shapes and isn't a decodable image.)

### `log`

Structured log text. Plain non-JSON lines are treated the same way.

```jsonl
{"type":"log","v":"1.0","level":"warn","msg":"DS18B20 starter probe CRC error, retrying"}
```

`level` is one of `debug | info | warn | error`.

### `ping` / `pong`

`{"type":"ping","v":"1.0","seq":1}`. Answer every `ping` with
`{"type":"pong","v":"1.0","seq":<same>}`. Pings are optional for the device.

## 3. Host (bridge) → device messages

```jsonl
{"type":"hello_ack","v":"1.0","server_time_utc":"2026-10-31T15:00:00Z","chamber_id":"A","device_id":"fermentai-esp32-a","poll_interval_s":2.0}
{"type":"time","v":"1.0","server_time_utc":"2026-10-31T15:00:00Z"}
{"type":"command","v":"1.0","server_time_utc":"2026-10-31T15:00:00Z","payload":{"schema_version":"1.0","command_id":"6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10","chamber_id":"A","experiment_id":"experiment_001","action":"set_setpoint","parameters":{"setpoint_c":24.0,"control_mode":"auto"},"issued_at_utc":"2026-10-31T15:00:00Z","expires_at_utc":"2026-10-31T15:01:30Z","sequence_number":42}}
{"type":"command","v":"1.0","server_time_utc":"2026-10-31T15:00:02Z","payload":{"schema_version":"1.0","command_id":"b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f","chamber_id":"A","experiment_id":"experiment_001","action":"animate_motor","parameters":{"duration_ms":3000,"pattern":"wiggle"},"issued_at_utc":"2026-10-31T15:00:02Z","expires_at_utc":"2026-10-31T15:01:02Z","sequence_number":43}}
{"type":"ping","v":"1.0","seq":7}
```

- **`hello_ack`** confirms the chamber mapping. Stop re-sending `hello` once you have it.
- **`time`** is sent right after `hello_ack`, and then every 60 s. It's the
  Mac's UTC time. A device may then set its clock and report `timestamp_quality: "synced"`
  (host-derived). Until then: `null` / `"unsynced"`.
- **`command`**: `payload` is exactly one `command_v1` object, taken from desired-control's
  `commands[]`. The sibling `server_time_utc` is the server's time when that list was read:
  - Remaining validity is `expires_at_utc − server_time_utc`, measured from receipt on
    `millis()`. If it's ≤ 0, ack `expired` and don't execute.
  - Process commands in ascending `sequence_number` (one counter per chamber). Skip any
    `command_id` you've already handled, because the bridge may repeat a command that's still
    unacked after 10 s (at most 3 sends).
  - Execute and ack exactly as `firmware_contract/README_FOR_ESP32_AGENT.md` §9.3 describes
    (`set_setpoint` → `accepted`; `animate_motor` → `accepted` then `completed`;
    `stop_motor` → `completed`; `capture_image` → image chunks + `completed`).
  - **All local safety limits still apply** no matter what any line says (§5 of that README).

## 4. Connection lifecycle and reconnection

1. The bridge opens the port and writes nothing until the device identifies itself: an
   unidentified port may be a legacy text console (§8b).
2. The device sends `hello`, then the bridge sends `hello_ack` and `time`. `time` repeats every
   60 s and `ping` every 15 s, but only to devices that have spoken serial_v1.
3. Steady state:
   - The device sends `telemetry` every 5–15 s, and `ack`s as commands complete.
   - The bridge polls desired-control every `poll_interval_s` (default 2 s) and writes each
     **new** pending command once. Commands are de-duplicated by `command_id` and re-sent only
     while still pending after 10 s, at most 3 sends in total.
   - The bridge sends `time` every 60 s and `ping` every 15 s.
4. **Unplug, reset or brown-out:**
   - The bridge sees EOF/EIO and marks the chamber `disconnected`.
   - It re-opens the port path every 1 s. USB re-enumeration usually gives back the same
     `/dev/cu.usbmodem*` name, and discovery also picks up a new name.
   - After reconnecting, the bridge forgets which commands it already sent. Every still-pending
     command is re-sent, and the device de-duplicates by `command_id`.
   - The device must boot safe, with Peltier and motor off (README §5), and must not replay
     commands it received before the reset.
5. **Host down:** if the backend is unreachable, the bridge keeps telemetry and acks in an
   ordered outbox (500 entries) and retries them. The device needs to do nothing; its own
   offline rules apply (README §5: offline hold, then idle).

## 5. Backpressure and flow control

- USB CDC has hardware flow control at the USB layer. A device that writes faster than the host
  reads will block in `Serial.write()`, so keep writes small, write whole lines, and never write
  from an ISR.
- The bridge reads continuously in its own task, so HTTP latency never stalls reading. It
  queues up to 2000 received lines and drops the oldest if that overflows (counted as
  `dropped_lines`).
- Host writes are small (a command line is under 1 KB) and use a 5 s write timeout. A device
  that stops reading for 5 s gets the port reset.
- The device should drain its RX buffer at least every 100 ms. ESP32 `Serial` RX buffers are
  small; consider `Serial.setRxBufferSize(4096)` before `Serial.begin()`.
- Keep telemetry lines under 2 KB and image chunks at ≤ 4096 raw bytes per line.

## 6. Port discovery and chamber mapping (bridge)

- With no `--port`, the bridge attaches every `/dev/cu.usbmodem*` and `/dev/ttyACM*` it finds,
  checking every 2 s. It opens ports with exclusive access (`TIOCEXCL`) and doesn't toggle
  DTR/RTS, so opening the port doesn't reset the board.
- The port → chamber mapping comes from `hello.chamber_id`, or from `--map PATH=CHAMBER` /
  `--port PATH=CHAMBER`. A `--map` is authoritative: a `hello` naming another chamber blocks
  that port, and nothing from it is forwarded.
- Before a `hello` arrives, the first valid `payload.chamber_id` is used. Only one live port may
  own a chamber at a time.
- Payloads whose `chamber_id` doesn't match the port's chamber are dropped and counted
  (`chamber_mismatch`). The server also enforces the device↔chamber binding, and the device
  token if one is configured.
- Auth per chamber: `FERMENTAI_DEVICE_TOKEN_<A|B>`, else `FERMENTAI_API_TOKEN`, sent as
  `Authorization: Bearer` plus `X-Device-Id: <hello.device_id>`.

**Don't run the bridge while a serial monitor (Arduino IDE, `pio device monitor`, `screen`) holds
the same port.** Exclusive mode makes the second opener fail rather than silently splitting the
byte stream.

## 7. Status for the dashboard

The bridge writes `data/telemetry/bridge_status.json` every 2 s. The API serves it as
`GET /api/v1/bridge/status` and adds `bridge_running: false` when the file is more than 10 s old.

```json
{"transport": "usb-serial", "protocol": "serial_v1", "mode": "live", "updated_at_utc": "…",
 "chambers": {"A": {"link": "connected", "label": "USB · connected", "port": "/dev/cu.usbmodem2101",
                    "device_id": "fermentai-esp32-a", "boot_id": "b7c1e2f0", "firmware_version": "1.1.0",
                    "simulated": false, "last_seen_utc": "…", "last_telemetry_utc": "…"},
              "B": {"link": "not_attached", "label": "USB · no device", ...}},
 "ports": [{"port": "...", "link": "...", "counters": {"telemetry_ok": 12, "commands_sent": 3, "ack_ok": 3,
            "rx_log_text": 40, "chamber_mismatch": 0, ...}, "recent_logs": ["..."], "last_error": null}]}
```

`link` is one of:

| value | meaning |
|---|---|
| `connected` | protocol messages arrived within the last 30 s |
| `waiting` | port open, nothing received yet |
| `logs_only` | the device prints text but no protocol messages (wrong firmware, or still booting) |
| `silent` | protocol messages, but none for more than 30 s |
| `disconnected` | port gone, unplugged |
| `busy` / `error` | the port couldn't be opened (`busy` = another program has it) |
| `conflict` | `hello` contradicts `--map`, or two ports claim the same chamber |
| `not_attached` | no port for this chamber |
| `replay` / `replay_finished` | replay mode |
| `bridge_offline` | added by the API when the status file is stale |

## 8. Recording and replay

- `--record FILE.jsonl` appends every line to `FILE.jsonl`, one entry per line:
  `{"t": seconds, "port": ..., "dir": "rx"|"tx", "line": "<raw line>"}`.
- `python -m fermentai.bridge --replay FILE [--replay-speed N]` re-sends the recorded `rx` lines
  (or a plain file of raw device lines) through the same parser and forwarder:
  - Telemetry is labelled `source: "replay"`, and its `boot_id` is prefixed `replay-` so it
    never collides with the live record.
  - Acks are not forwarded, and **no commands are sent anywhere**.

## 8b. Legacy firmware 0.3.x status blocks (`--parse-status-log`, on by default)

Firmware 0.3.0 (Wi-Fi build, serial hook not applied) doesn't speak this protocol. Every 30 s it
prints a plain-text block like this one, which comes from the real board:

```
[A fermentai-esp32-a] fw=0.3.0 boot=1185100b reset=other state=waiting_for_command mode=idle out=0.00 armed(peltier=0 motor=1)
  chamber=28.60C rh=13.2% p=nanPa gas=nanohm starter=23.44C hs_int=nanC hs_ext=nanC
  ph=nan mv=0.0 raw=0 cal=0 door=closed opens=0 setpoint=nan faults=0x0
  ens160: present=1 addr=0x53 valid=1 warmup=0 aqi=0 tvoc=0 ppb eco2=0 ppm (indices, not CO2)
  flags: ds18b20_role_unassigned ph_out_of_range
  wifi=0 rssi=0 tel_q=0 acks_q=0 heap=239416 psram=8179891
```

`src/fermentai/bridge/status_log.py` turns each block into one telemetry_v1 record. The block is
emitted on its `wifi=` line, on the next header, or 5 s after its last line. Lines may be
missing or out of order. The record has:

- `source: "measured"` and the quality flag `legacy_status_log`;
- `timestamp_utc: null` / `"unsynced"`, and no `device_uptime_ms`;
- `sequence_number` = a counter per boot plus the bridge start time in seconds, so numbers stay
  unique when the bridge restarts during the same boot;
- `nan` → null;
- pH only when `cal=1` (`ph_method: "probe"`). Otherwise `ph` is null, with the flag
  `ph_uncalibrated` and the raw mV/ADC in `diagnostics`;
- ENS160 AQI/TVOC/eCO2 → `diagnostics.ens160` only, never `gas_resistance_ohms`;
- `motor_running: false` unless a line says the motor is running (`armed` isn't running).

`device_id`, chamber, `boot_id` and firmware come from the `[A device]` header, or from the
`FermentAI 0.3.0 chamber=A device=… boot=…` boot banner when the port was opened mid-block.

While a port is in legacy mode, the **only** thing the bridge writes to it is the console
command `motor test <ms>`. It never sends `time`, `ping` or JSON commands. This mapping is on by
default; `--no-legacy-console` turns it off so nothing is written at all.

| command_v1 | console line | console reply → ack |
|---|---|---|
| `animate_motor {duration_ms}` | `motor test <min(duration_ms, 10000)>` | `OK running <N> ms` → `accepted` (`result.granted_ms`), then `completed` N ms later (`result.completion: "inferred_from_granted_duration"`, because fw 0.3.0 doesn't report the stop). `ERR <why>` (e.g. `ERR motor not armed (commissioning)`, `ERR door open`, `ERR motor already running`) → `rejected`, with the console text as `detail`. No reply within 3 s → `failed`. |
| `stop_motor` | none (the console has no stop) | `completed` when the bounded run ends, or immediately if the motor isn't running |
| `set_setpoint`, `capture_image`, … | none | left pending; they expire unacked on the server |

A new `motor test` is held back while a previous run is still in progress, so the console never
answers "already running" because of the bridge. Commands go to serial_v1 devices only after
they identify themselves, which also switches status-text parsing off for that connection.
Disable status parsing with `--no-parse-status-log`.

## 9. What a firmware implementer must do (checklist)

1. `Serial.begin(115200)`. Print anything you like for debugging; non-JSON lines are harmless.
2. At boot, and every 30 s until `hello_ack`: emit `hello`.
3. Every 5–15 s: emit `telemetry` with `payload` = a telemetry_v1 object. `boot_id` and
   `sequence_number` are required in practice. Use `timestamp_utc: null` / `"unsynced"` until
   `time`.
4. Read lines and dispatch on `type`:
   - `hello_ack`: stop sending hello.
   - `time`: optionally set the clock.
   - `ping`: reply `pong`.
   - `command`: validate, check expiry and sequence number, execute, then emit `ack` lines.
5. Ignore lines you can't parse, and unknown `type`s. Never reset on bad input.
6. Enforce every local safety limit independently of the host.

Simulator reference implementation: `simulated_devices/transport_serial.py`
(`uv run python -m simulated_devices --chambers A --transport serial --port <pty or port>`).
