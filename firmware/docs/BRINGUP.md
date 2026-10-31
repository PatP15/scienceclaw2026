# Physical bring-up and commissioning (per chamber)

Rule: **high-power outputs stay disabled until every earlier check has passed.** Each step lists a
pass criterion. Stop at the first failure and record it in the log at the end. Do one chamber at
a time. Never leave an armed Peltier unattended before step 10d.

Two independent locks protect the Peltier:

1. **Compile time:** `FERMENTAI_ALLOW_PELTIER_OUTPUT=0` in `platformio.ini` (the default).
   The BTS7960 pins are then never driven HIGH, whatever the software state.
2. **Runtime:** the NVS flag set with `arm peltier <CHAMBER_ID>`. With the default
   `HEATSINK_PROBES_REQUIRED=1` it is refused unless both heatsink probes are assigned.
   **Both chamber envs currently build with `HEATSINK_PROBES_REQUIRED=0`** (operator decision,
   2026-10-31: heatsink probes not fitted). The firmware then has no view of the hot side, so the
   bimetal thermostats on both heatsinks and the supply fuse are the only over-temp protection, and
   step 10b item 7 cannot be tested. `disarm peltier` clears the flag.

The motor needs `MOTOR_ENABLED=1` plus `arm motor`.

Tools needed: multimeter, ideally a scope, USB cable, the Mac backend
(`scripts/start_demo.sh`), pH 4/7(/10) buffers, a heat gun or hot water, and a power resistor as
a dummy load (for example 2 Ω / 50 W for a 12 V Peltier stage, or a 12 V lamp).

## Step 0: build and flash basics

```bash
cd firmware
pio run -e esp32s3cam_noio -t upload      # first flash: no hardware I/O at all
pio device monitor                        # 115200 baud; type `help`
```

If upload fails, hold BOOT (GPIO0), tap RST and retry. Use the UART (CH340/CH343) USB port for
logs. Logs go to UART0 (`ARDUINO_USB_CDC_ON_BOOT=0`).

## Step 1: identify the board (nothing connected)

1. Read the module marking (for example `ESP32-S3-WROOM-1 N16R8`). If it ends in **V** (1.8 V
   PSRAM), GPIO47 is a 1.8 V pin, so move OneWire (GPIO_MAP §3).
2. Flash `esp32s3cam_noio` and run `status`. Check `psram=` > 0 (PSRAM detected). If it's 0 the
   board is not OPI, so change `board_build.arduino.memory_type`.
3. Check that pins 1, 14, 21, 38, 39, 40, 41, 42 and 47 are broken out on the headers.
4. Set the network: `wifi <ssid> <pass>`, `backend http://<mac-ip>:8000`, `token <token>`,
   `reboot`. **Pass:** the dashboard shows chamber A as **SIMULATED**, with telemetry every 10 s
   and `source: simulated`.

## Step 2: reset-level check (decides the pin map). No loads connected.

With a multimeter or scope, measure each pin **while RST is held down** and **during the first
second after release**:

| Pin | Must be | If not |
|---|---|---|
| GPIO40 (Peltier EN) | LOW (< 0.5 V) | build with `-DFERMENTAI_PINMAP_ALT=1`, or remove the SD pull-up |
| GPIO38 (motor gate) | LOW | same |
| GPIO14, GPIO21 (PWM) | LOW / floating then LOW | add or verify the 10 kΩ pull-downs |

With the external pull-downs fitted (SENSOR_WIRING), re-measure. Every output must read LOW
during reset, during boot and after `setup()`. Record the results in GPIO_MAP §5.

## Step 3: hardware firmware, no peripherals

```bash
pio run -e esp32s3cam_a -t upload    # or esp32s3cam_b
```

**Pass:**

- Boot banner shows `peltier_output_compiled=0`.
- `status` shows `state=waiting_for_sensors`, `mode=idle`, and flags such as
  `bme680_missing`, `ds18b20_role_unassigned` and `peltier_output_disarmed`.
- Telemetry arrives with `source: measured` and nulls (not zeros) for the missing sensors.
- Camera: the log says `camera: ok`, and an image appears in `data/images/A/` within 60 s.
  If init fails, check the camera pins (table 1) before anything else.
- Multimeter: GPIO14, 21, 38 and 40 are LOW.

## Step 4: door reed switch

Wire it as in SENSOR_WIRING. Open and close the door several times.

**Pass:** `status` shows `door=open/closed` correctly, the open count increments once per opening
(no bounce), and telemetry `door_open` matches. If it's inverted, flip `kDoorOpenLevelHigh`.

## Step 5: BME680 (or SHT31)

Connect it to I²C 41/42.

**Pass:**

- `status` shows a plausible chamber temperature (within 2 °C of a reference thermometer), RH
  and pressure (~100 kPa).
- Gas resistance is non-zero, and `sensor_warming_up` is flagged for the first 5 min.
- Unplugging the sensor gives `bme680_read_failed`, then `stale`, then `null` in telemetry.
  After 30 s (step 10 only) the safety manager raises `sensor_stale`.

## Step 6: DS18B20 probes, one at a time

1. Connect the bus (4.7 kΩ pull-up) with **one** probe. Run `scan` and note its ROM. If it shows 0
   probes, `owtest` tells pull-up / presence apart and `owsearch` prints raw ROMs with CRC. Assign it,
   for example `assign 28ff641e0f123456 starter`, and label the cable.
2. Add the next probe, run `scan` (the new ROM shows `UNASSIGNED`), and assign
   `internal_heatsink`. Repeat for `external_heatsink`.
3. Warm each probe in your hand, one at a time. **Pass:** only the matching role changes.
4. Reboot. **Pass:** the roles persist even if bus order changes.
5. Unplug one probe. **Pass:** that role goes `missing` within 15 s, the flag
   `ds18b20_disconnected` appears, and the other roles keep working.

## Step 7: pH (chamber A only)

1. **Before connecting GPIO1:** measure the interface output range in buffers and at both
   supply rails. Fit the divider so the maximum is ≤ 3.0 V (SENSOR_WIRING).
2. Connect it. In pH 7 buffer wait for stability, then `phcal 7`. Rinse, pH 4, `phcal 4`.
3. **Pass:**
   - `status` shows `cal=1`.
   - pH 7 buffer reads 7.0 ± 0.1 and pH 4 reads 4.0 ± 0.1.
   - pH 10 buffer (if available) reads within ± 0.3. If not, the response is non-linear: note
     it and keep to the 4–7 range.
   - Telemetry `ph_method` = `probe_sample` (change `PH_METHOD` if the probe is immersed).
4. Record the calibration date shown, and the mV values, in the log.

## Step 8: camera field of view

Mount the board at the window, fix the lighting, and check several consecutive images.
**Pass:** the jar fills the frame, exposure is stable, and images are ≤ 200 KB at SVGA quality 12.
While uploading, check `status` timing: the door and safety loops keep running (a camera upload
never blocks the loop task).

## Step 9: motor (low power)

1. Bench-test the MOSFET circuit with the motor. Scope the gate during reset and power-up.
   **Pass:** no gate pulse.
2. `arm motor`, then `motor test 1000`. **Pass:** the motor runs about 1 s and stops.
   `motor test 60000` runs at most 10 s.
3. Open the door during `motor test 8000`. **Pass:** it stops immediately. Starting with the
   door open is refused (`door open`).
4. From the backend:
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d '{"duration_ms":3000}' http://<mac>:8000/api/v1/chambers/A/motor
   ```
   **Pass:** the ack sequence `accepted` → `completed` with `ran_ms ≈ 3000`, and the motor
   is **not** replayed after you reboot the ESP32 within the TTL.

## Step 10: Peltier stage, staged

### 10a: logic only (no 12 V)

Connect the BTS7960 logic (VCC, GND, EN, RPWM, LPWM) and leave B+ unpowered.

**Pass:**

- With 3.3 V logic, the BTS7960 IS/INH behaviour is correct, or the 74HC244 VCC issue has
  been resolved (SENSOR_WIRING warning).
- EN, RPWM and LPWM read LOW at reset and boot.

### 10b: dummy load, outputs enabled

1. Edit `platformio.ini` for this env: `-DFERMENTAI_ALLOW_PELTIER_OUTPUT=1`. Upload.
2. Fuse the 12 V supply and connect the **power resistor** to M+/M-.
3. `arm peltier A`. It is refused unless both heatsink probes are assigned.
4. `setpoint 30` (above ambient). After the 10 s ramp, measure the M+/M- polarity and note
   which side is HEAT (`kHeatOnChannelA`).
5. `setpoint 10` (below ambient). **Pass:**
   - Output goes to 0, `mode=switching` for **60 s**, then the reverse polarity ramps up.
   - RPWM and LPWM are never active together (scope both).
6. Unplug the BME680. **Pass:** `fault`, `sensor_stale` within 30 s, and the output goes to 0.
7. Heat the external heatsink probe above 60 °C. **Pass:** `heatsink_overtemp` and the output
   goes to 0 immediately. It stays latched until < 50 °C for 60 s.
8. Open the door. **Pass:** `mode=idle` and the output goes to 0.
9. `disarm peltier`. **Pass:** the output goes to 0 immediately.

### 10c: real Peltier, supervised

Fit the Peltier with the heatsinks, fans (own branch, running) and **hardware thermostats on both
heatsinks** (test that each one opens: heat it and confirm the 12 V feed opens).

1. Confirm the polarity matches `kHeatOnChannelA`. A heating command must warm the inside.
2. Do short supervised runs at setpoint ±5 °C from ambient. Watch both heatsink temperatures and
   keep the hot side well below 60 °C.
3. Check the regulation band (hysteresis 0.5 °C) and that the door, sensor and over-temp
   interlocks still trip.

### 10d: long run

Run 12 h at the demo setpoint with logging. Check there are no faults, no `brownout_reset`, and
that the wiring is not warm. Only after this may the chamber run unattended. A human must still be
able to reach the supply switch.

## Step 11: network and offline behaviour

1. Unplug the router WAN. **Pass:** nothing changes (LAN only).
2. Power the AP off for 5 min. **Pass:**
   - Control continues on the last setpoint.
   - Telemetry queues locally (`tel_q` grows, maximum 50).
   - On reconnect it is delivered with the original sequence numbers and the server reports
     `duplicate` for any re-sends.
3. Offline hold: keep the backend away > 1800 s (or build a test image with a shorter
   `kOfflineHoldMs`). **Pass:** `offline_idle`, and the Peltier goes idle.
4. Reboot while a motor command is pending. **Pass:** the motor does not run again.
5. Set a 40 °C setpoint via the API. **Pass:** the server clamps it. If you bypass the server
   (console `setpoint 40`), the device applies 35.0 °C and acks "clamped locally".

## Bring-up log

| Step | Chamber | Result (pass/fail + values) | Date | By |
|---|---|---|---|---|
| 1 | A | partial: console on native USB ok, `psram=8179891`, `camera: ok`. Module marking and header pin check still to record. Network not yet set. | 2026-10-31 | PP + Claude |
| 2 | A | firmware boot snapshot (before any pin is driven): GPIO38=HIGH, GPIO40=HIGH even against the internal pull-down → board pull-ups; 14/21/2/48 LOW. **ALT map selected and flashed.** Multimeter check with RST held still to do before steps 9/10. | 2026-10-31 | PP + Claude |
| 3 | A, B | pass on both: banner `peltier_output_compiled=0`, `camera: ok`, nulls (nan) for missing sensors, PSRAM ≈8 MB, flash 16 MB. B: MAC b8:1f:3f:ab:db:6c. B first came up in download mode (`boot:0x0`, GPIO0 low) until RST/replug; watch for a recurrence. | 2026-10-31 | PP + Claude |
| 4 | A, B | B: pass, tracked each magnet move, count 0→4. A: pass (bench, bare reed on GPIO39): magnet near = `closed`, away = `open`, tracked every move; open count rose faster than the moves (possible bounce or threshold hover), recheck debounce once mounted on the door. | 2026-10-31 | PP + Claude |
| 5 | A, B | ENS160+AHT21 (B: AHT21 0x38 23.7 °C / 19 % RH, ENS160 0x53 live after ~20 s.) A detail: ENS160+AHT21 on I²C 41/42 (instead of BME680): AHT21 0x38 23–25 °C / 21 % RH; ENS160 found at 0x53, first data after ~20 s (AQI 1, TVOC 9–24 ppb, eCO2 400 ppm, `warmup=1`). Pressure/gas null as expected. Breath test: RH 21→54 %, TVOC 22→91 ppb, eCO2 400→535, AQI 1→2, back to baseline within 15 s. Reference-thermometer comparison and unplug test pending. | 2026-10-31 | PP + Claude |
| 6 | A, B | B: probe `28d3a9ca00000061` → starter (label 0061). A, in progress: probe 1 found after the OneWire GPIO47 fix, ROM `283e4825000000c6` → starter (cable label 00C6). Hand-warm: 23.5 → 27.1 °C in 40 s, pass. Role persisted across several resets (NVS), pass. Side finding: AHT21 reads ≈3 °C above the DS18B20 in the same air (ENS160 self-heating). Unplug test and probes 2 and 3 pending. | 2026-10-31 | PP + Claude |
| 7 | | | | |
| 8 | | | | |
| 9 | A | in progress: D4184 dual-MOSFET module on GPIO48 (ALT map). `arm motor` OK; `motor test 3000` drives the gate HIGH for 3.0 s then LOW (read back via `pin 48`). GPIO48 LOW at boot. Operator confirmed the motor runs on the 3 s pulse. Door-open refusal: pass (`motor test 2000` with door open → `ERR door open`). Door-open stop mid-run not yet demonstrated (door pin stayed closed during both 8 s attempts); backend command test pending (needs Wi-Fi). Earlier the board went silent/un-enumerated after the first motor wiring attempt and recovered after re-plugging; cause not identified (suspect motor power on the 5V pin). | 2026-10-31 | PP + Claude |
| 10a–d | A | 10a partial: BTS7960 (handsontec IBT-2, control input level 3.3–5 V per datasheet) logic wired RPWM=14, LPWM=21, R_EN+L_EN=2, VCC=3V3, B+ unpowered. GPIO14/21/2 LOW at boot (plain and with pull-down) and after setup. 12 V / dummy-load stage (10b) not started: build flag still 0. Note: board A went silent on USB (enumerated, no ROM/console) after each new wiring session and recovered after unplug/replug; always wire with USB unplugged. | 2026-10-31 | PP + Claude |
| 11 | | | | |
