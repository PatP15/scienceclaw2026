# Firmware (ESP32-S3-CAM): progress checkpoint

Owner: ESP32 firmware agent. Scope: `firmware/` + this file. Last update: 2026-10-31.

## Done

- **PlatformIO project** `firmware/platformio.ini`, using PlatformIO Core 6.2.0 (`uv tool install platformio`):
  - Platform espressif32@6.9.0, with framework-arduinoespressif32 3.20017.241212 (Arduino core 2.0.17).
  - Board `esp32-s3-devkitc-1` with OPI-PSRAM overrides. The verbose log confirms the `qio_opi` SDK
    and `BOARD_HAS_PSRAM`.
  - Envs:
    - `esp32s3cam_a`: pH on.
    - `esp32s3cam_b`: no pH.
    - `esp32s3cam_noio`: mock I/O, `source=simulated`.
    - `native`: Unity tests.
- **Build status** (logs in `firmware/build_logs/`). All are clean full builds, with 0 warnings
  from firmware sources.

  | Env | RAM | Flash |
  |---|---|---|
  | `esp32s3cam_a` | 62,536 B (19.1%) | 939,285 B (28.1%) |
  | `esp32s3cam_b` | 62,536 B | 938,549 B |
  | `esp32s3cam_noio` | 57,040 B (17.4%) | 825,849 B (24.7%) |
  | variant B (ALT pins + Peltier output compiled in + SHT31 + v1.0 profile) | — | SUCCESS |

- **Native tests:** 94/94 pass.

  | Suite | Tests |
  |---|---|
  | control | 20 |
  | sensors | 18 |
  | motor | 12 |
  | commands | 23 |
  | telemetry | 13 |
  | network | 8 |

- **Contract:** coded against revision 1.1 (wire `schema_version` "1.0") in `contracts/` and
  `firmware_contract/README_FOR_ESP32_AGENT.md` §9. There is a compile-time profile:
  `FERMENTAI_CONTRACT_V11=1` (default) or `0` for a strict v1.0 server.
  - Serializer output was validated against the backend's own Pydantic models
    (`firmware/tools/validate_contract_samples.py`): 8/8 OK.
  - The v1.0-profile payloads were also checked against the frozen v1.0 models (`git d506d57^`):
    all OK.
- **Docs:** `firmware/docs/GPIO_MAP.md`, `SENSOR_WIRING.md`, `BRINGUP.md` and
  `HARDWARE_UNCERTAINTIES.md`.

## Key design decisions

- **Two-core split:**
  - loopTask (core 1) runs a cooperative Scheduler: safety 200 ms, control 1 s, door 20 ms,
    motor 20 ms, DS18B20 2 s, env 5 s, pH 5 s. It also executes commands and takes the snapshot.
  - netTask (core 0) does Wi-Fi, polling every 2 s, acks, telemetry every 10 s with a queue of
    ≤ 50, and camera every 60 s.
  - The motor has an independent esp_timer cut-off.
  - The loop WDT is enabled.
- **Peltier is double-locked:** compile flag `FERMENTAI_ALLOW_PELTIER_OUTPUT=0` (the default in
  every env) AND the NVS arm flag (`arm peltier <ID>`, refused without both heatsink probes).
  The motor needs the NVS `arm motor`.
- **Commands:** flat v1.0 fields and the v1.1 `commands[]` list (same ids) are deduped by an
  (id, kind) ring.
  - Sequence channels: v1.0 control/motor use `<`; the v1.1 array uses `<=` across all actions.
  - Expiry is measured from receipt: `ttl_s`, otherwise `expires_at − server_time`.
  - The motor replay guard is persisted in NVS.
  - Setpoints are clamped to 4–35 °C from `device_config.h`.
- `timestamp_utc` stays null and `unsynced` until `server_time_utc` sync.

## Next

1. Hardware bring-up per `firmware/docs/BRINGUP.md`. The first gate is step 2: measure the
   GPIO38/40 reset levels, which decides the default or ALT pin map.
2. Confirm the module marking (N16R8 vs a "V" variant, which makes GPIO47 1.8 V) and the camera
   pins on the real Aideepen board.
3. Resolve 3.3 V logic compatibility of the IBT-2/BTS7960 module (74HC244 on 5 V).
4. Tune the control constants (`device_config.h` `cfg::`) during step 10c.
5. Optional: a fan tach input (`fan_fail`), and a 16 MB partition table if the flash is 16 MB.

## How to resume

```bash
uv tool install platformio          # if pio is missing
cd firmware
pio test -e native                  # 94 tests
pio run -e esp32s3cam_a             # / _b / _noio
cd .. && uv run python firmware/tools/validate_contract_samples.py   # after pio test
```

If the backend contract changes, regenerate it (`uv run python -m fermentai.api.contract
--write-schemas`) and re-run the native tests. `test_telemetry` and `test_commands` read
`firmware_contract/` and `contracts/` directly.
