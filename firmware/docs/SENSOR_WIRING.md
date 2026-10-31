# Sensor and actuator wiring (per chamber)

Pins refer to the **provisional** default map (`include/board_config.h`; see `GPIO_MAP.md`).
All signal levels are 3.3 V. **No ESP32 pin may see more than 3.3 V.**

```
             3.3V ─┬───────────┬──────────┬───────────────┐
                   │4.7k       │(module   │10k (optional) │
 DS18B20 ×3 DQ ────┴─ GPIO47   │ pull-ups)│               │
 (VDD=3.3V, GND)          BME680 SDA ─ GPIO41    Reed ────┴─ GPIO39 ── reed switch ── GND
                          BME680 SCL ─ GPIO42                 (internal pull-up also on)
 pH board AOUT ── divider ── 1k ── GPIO1 (ADC1_CH0) ── 100nF ── GND
 BTS7960: RPWM ─ GPIO14, LPWM ─ GPIO21, R_EN+L_EN ─ GPIO40   (each 10k to GND)
 N20 MOSFET gate ─ 100Ω ─ GPIO38   (100k gate→GND)
 GND: ESP32, sensors, BTS7960 logic GND and MOSFET source share ONE reference point
```

## DS18B20 (×3, one shared bus) — role by ROM address

| Wire | Connect to |
|---|---|
| Red (VDD) | 3.3 V (three-wire, **not** parasitic power) |
| Black (GND) | GND |
| Yellow/white (DQ) | GPIO47, and **4.7 kΩ to 3.3 V** (one resistor for the whole bus) |

- Roles: `starter_temperature`, `internal_heatsink_temperature` (the Peltier side facing the
  chamber) and `external_heatsink_temperature` (the outside / fan side). Assign each one by ROM
  address with the console (`scan`, then `assign <rom> <role>`). Bus order is never used. Label
  each probe cable with its ROM's last 4 hex digits.
- For long runs (> 3 m) or three probes on long cables, drop the pull-up to 2.2–3.3 kΩ and add
  100 nF across VDD/GND at the probe end.
- **Food contact:** a generic stainless probe is not certified food-safe. Put the starter probe in
  a separate, non-edible instrumented sample jar (`ph_method`/placement as documented in the
  experiment notes).
- Heatsink probes: thermally bond them to the heatsink base near the Peltier (thermal paste plus
  Kapton tape or a clamp). The firmware trips `heatsink_overtemp` above 60 °C. This is **in
  addition to** the hardware thermal cutoff below, never instead of it.

## BME680 (I²C; default ENV_SENSOR)

| Pin | Connect to |
|---|---|
| VIN/VCC | 3.3 V (check the breakout: Adafruit/Pimoroni have a regulator; bare modules may be 3.3 V only) |
| GND | GND |
| SDA / SCL | GPIO41 / GPIO42. Most breakouts have 10 kΩ pull-ups. Don't add more unless the bus is long. |
| SDO / ADDR | Sets the address: 0x77 (high, Adafruit default) or 0x76 (low). The firmware tries both. |

- Mount it in the chamber air, away from the Peltier cold plate and the jar, and shaded from the
  camera light.
- **Warm-up:** the gas heater (320 °C, 150 ms per cycle) needs **≥ 5 min** after power-up before
  gas resistance settles (telemetry flag `sensor_warming_up`). Brand-new sensors need about 48 h
  of burn-in. The die self-heats, so temperature reads about 0.5–2 °C high. Compare it with a
  DS18B20 and set `kBme680TempOffsetC`.
- **Gas resistance is not CO₂.** It is a broad VOC-, humidity- and temperature-sensitive
  resistance and is reported only as `gas_resistance_ohms`.

## ENS160 + AHT21 combo board (`-DENV_SENSOR=ENV_SENSOR_ENS160_AHT21`; fitted on chamber A, 2026-10-31)

| Pin | Connect to |
|---|---|
| VIN / VCC | 3.3 V (never 5 V; the I²C lines must stay at 3.3 V) |
| GND | GND |
| SDA / SCL | GPIO41 / GPIO42 (breakout has its own pull-ups) |
| INT | not connected |
| ADD | not connected (ENS160 at 0x53) or GND (0x52). The firmware tries both. |

- AHT21 (0x38) supplies `chamber_temperature_c` and `relative_humidity_percent`. `pressure_pa` and
  `gas_resistance_ohms` are null with this sensor.
- ENS160 (0x53/0x52) gives TVOC (ppb), eCO2 (ppm equivalent) and a 1–5 AQI. These are chip-computed
  **indices, not a CO₂ measurement**, compensated with the AHT21 T/RH. Shown in the console
  `status` line `ens160:`; not yet in telemetry (the contract has no field for them, needs a
  backend-owner addition). Warm-up: ~3 min after power-up, ~1 h on first-ever use (`warmup=1`).
- **Self-heating:** the ENS160 hot-plate sits millimetres from the AHT21. Bench observation
  2026-10-31: the reading climbed 23.1 → 25.2 °C in the first 90 s after power-up at constant room
  temperature. Compare against a DS18B20 in the same air and set an offset before trusting it as
  the control temperature.

## SHT31 (optional, `-DENV_SENSOR=ENV_SENSOR_SHT31`)

Same I²C bus. Address 0x44 (ADDR low) or 0x45 (ADDR high). No address conflict with the BME680
(0x76/0x77), so both can be fitted. `ENV_SENSOR` selects which one supplies
`chamber_temperature_c` / RH. The other can be used for cross-checks during commissioning.
The SHT31 gives no pressure or gas, so those fields are null.

## pH interface (chamber A only, `PH_SENSOR_ENABLED=1`)

The ESP32 reads the **interface board's conditioned output**, never the glass electrode.

1. Before connecting anything to GPIO1, power the interface board and measure AOUT with a
   multimeter in pH 4, pH 7 and pH 10 buffers, and with the probe disconnected.
   Common "PH-4502C"-style boards run on **5 V and can output up to 5 V**.
2. If the maximum output is more than **3.0 V**, add a divider, for example 10 kΩ (top) and
   20 kΩ (bottom) for ×0.667. Re-measure at the divider output. It must stay ≤ 3.0 V in every
   case, including rail-to-rail faults. Optionally add a 3.3 V Schottky clamp to the 3.3 V rail.
3. Series 1 kΩ plus 100 nF to GND at GPIO1 (RC filter, input protection).
4. Calibrate **after** the divider (the stored millivolts are post-divider): pH 7 buffer →
   `phcal 7`; rinse, pH 4 buffer → `phcal 4`. The calibration date and both millivolt values
   are stored in NVS. Without both points pH is published as `null` with
   `ph_uncalibrated`, while raw ADC counts and millivolts are still reported for diagnostics.
5. The ADC window used for validity is 50–3000 mV. Anything outside it gives `ph_out_of_range`
   and a null pH.

The permanently immersed probe may foul or drift in dough. Re-check it against a buffer and
manual strips, and record manual readings as manual (`field_provenance: manual`). Never copy
chamber A's pH into chamber B.

## Reed switch (door)

- A normally-open reed switch between GPIO39 and GND, with the magnet on the door. The internal
  pull-up is enabled. Add an external 10 kΩ to 3.3 V and 100 nF to GND for cables longer than
  about 30 cm.
- Polarity: with this wiring the door is **closed = LOW** and **open = HIGH**
  (`kDoorOpenLevelHigh = true`). A normally-closed switch, or a different mounting, inverts this.
  Check it with `status` and flip `kDoorOpenLevelHigh` if needed. Debounce is 60 ms.

## Camera

On-board OV2640 behind the chamber window, outside the jar. Fix the lighting: an LED strip on
its own supply, not the camera flash LED, to avoid exposure drift. Lock the board mechanically so
the field of view doesn't move between images. The camera is not used for any on-device analysis.

## Peltier stage (BTS7960 / IBT-2 module)

| IBT-2 pin | Connect to |
|---|---|
| RPWM | GPIO14 + 10 kΩ to GND |
| LPWM | GPIO21 + 10 kΩ to GND |
| R_EN and L_EN (tied) | GPIO40 + 10 kΩ to GND |
| VCC (logic) | **See the logic-level warning** |
| GND (logic) | ESP32 GND |
| B+ / B- | 12 V Peltier supply through a **fuse** sized for the module (e.g. 10 A slow-blow for a 12706 at ~6 A) |
| M+ / M- | Peltier module |

- **Logic-level warning (unverified):** many IBT-2 boards buffer the inputs through a
  74HC244 powered from the VCC pin. With VCC = 5 V, a 3.3 V input is **not guaranteed** to read
  HIGH (VIH ≈ 3.5 V). Options:
  - Power VCC from 3.3 V, if the board's design allows it; check the BTS7960 INH/IN thresholds.
  - Use a 74HCT/level-shifter stage.
  - Verify reliable switching with a scope across the full temperature range.
- **Independent thermal cutoff:** put a normally-closed bimetal thermostat (e.g. KSD301, about
  70 °C) on **each** heatsink, in series with the Peltier supply (or the BTS7960 B+ feed). It must
  open the circuit with the ESP32 unpowered. Software alone is **not** adequate thermal safety.
- **Fans:** run them on their own fused 12 V branch, always on while the 12 V supply is on. They
  are not controlled by Peltier polarity and not by the firmware (`fan_running: null`).
  Contract §5.4 (fan running ≥ 30 s after the Peltier stops) is met because they are
  hard-wired on.
- Keep hot surfaces and high-current wiring clear of the XPS foam (combustible). Use heat-rated
  standoffs and wire sized for the current (≥ 1.5 mm² / 16 AWG for 6 A).
- Brown-out: R_EN/L_EN pulled down means the output disables when the ESP32 resets or loses
  power.

## N20 chef-hat motor

```
 +5V (or motor supply) ── N20 motor ──┬── drain   logic-level N-MOSFET (Vgs(th) < 2 V, e.g. AO3400)
                          1N5819 ◄────┘           gate ── 100 Ω ── GPIO38 ; 100 kΩ gate→GND
                          (flyback, cathode to +) source ── GND
```

- The motor is never powered from a GPIO, never shares the BTS7960, and has its own supply
  decoupling (470 µF).
- The firmware caps every run at 10 s. An esp_timer cut-off forces the gate LOW even if the main
  loop stalls.

## Power and grounding

- ESP32-S3-CAM: a dedicated 5 V supply rated ≥ 1 A (camera + Wi-Fi peaks). Not from the
  BTS7960's 5 V.
- One common ground point between the logic supply and the 12 V Peltier supply. Peltier return
  current must not flow through the ESP32 ground wiring.
- Brown-out detection is enabled by default. A brown-out reset is reported as the quality flag
  `brownout_reset`.
