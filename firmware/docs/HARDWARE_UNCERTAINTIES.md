# Hardware uncertainties and the tests that resolve them

The firmware compiles and its logic is unit-tested. **Everything below is unverified on real
hardware.** Each item names the bring-up step that resolves it.

| # | Uncertainty | Risk if wrong | How it is resolved |
|---|---|---|---|
| 1 | The Aideepen ESP32-S3-CAM is a Freenove "ESP32-S3 WROOM CAM" layout clone (camera pins = ESP32-S3-EYE set) | Camera init fails. Peripheral pins could collide with the camera. | Read the module marking and silkscreen. `camera: ok` and image upload (BRINGUP 1, 3). |
| 2 | Module is N8R8/N16R8 (3.3 V octal PSRAM) | Wrong `memory_type` means no PSRAM (camera limited to VGA, 1 framebuffer). On a "V" (1.8 V) variant GPIO47 is 1.8 V and the OneWire pull-up over-volts it. | BRINGUP 1 (`psram=`), module marking |
| 3 | Flash size: declared 8 MB, may be 16 MB | None (8 MB layout works on 16 MB). The spare flash is just unused. | Marking; optionally switch to a 16 MB partition table |
| 4 | **GPIO38/40 have SD-card pull-ups** | Motor and/or BTS7960 enabled during reset/boot (~300 ms before firmware runs) | BRINGUP 2. ALT map (`FERMENTAI_PINMAP_ALT=1`), external pull-downs |
| 5 | GPIO 1, 14, 21, 38–42, 47 are broken out on the headers | Pin map unusable | BRINGUP 1 |
| 6 | GPIO39–42 (JTAG pads) behave as plain GPIO after reset | I²C/reed misbehave | eFuse default routes JTAG to USB. BRINGUP 4/5 confirm. |
| 7 | **IBT-2/BTS7960 module accepts 3.3 V logic** (many use a 74HC244 on 5 V VCC: VIH ≈ 3.5 V) | Peltier doesn't switch, or switches erratically | BRINGUP 10a with a scope. 3.3 V VCC or a 74HCT level shifter. |
| 8 | Which BTS7960 side heats (`kHeatOnChannelA`) | Heating command cools (controller drives away from the setpoint until the over/under-temp fault) | BRINGUP 10b/10c polarity test |
| 9 | Peltier current, fuse size, wire gauge, heatsink capacity | Overheating, fire risk near XPS | Datasheet of the actual module, BRINGUP 10c/10d. Hardware thermostats are mandatory. |
| 10 | 60 °C heatsink limit is appropriate for the module | Module damage or nuisance trips | Module datasheet (`configs/hardware.yaml` marks it as an assumption) |
| 11 | Control gains (hysteresis 0.5 °C, Kp 0.25/°C, PWM limit 0.8, ramp 0.1/s) suit the chamber | Oscillation or slow response | BRINGUP 10c. They are tuning constants in `device_config.h`. |
| 12 | pH interface output range and supply (often 5 V, up to 5 V out) | **ADC over-voltage damages the ESP32** | BRINGUP 7 step 1, before connecting. Divider. |
| 13 | pH response linear between pH 4–7 after the divider; drift and fouling in dough | Wrong pH | pH 10 check, periodic buffer re-checks, manual strips |
| 14 | BME680 breakout supply/pull-ups/address | Not detected | Address auto-detect (0x77/0x76). BRINGUP 5. |
| 15 | BME680 self-heating offset | Chamber temperature biased by 0.5–2 °C, so the controller regulates to a biased value | Compare with a DS18B20, set `kBme680TempOffsetC` |
| 16 | DS18B20 probe quality (clones may return 85 °C / CRC errors or lack parasitic support) | Missing or invalid readings | Firmware rejects 85/-127/CRC. BRINGUP 6. Use 3-wire power. |
| 17 | Reed switch type (NO/NC) and magnet distance | Door state inverted or flickering | BRINGUP 4. `kDoorOpenLevelHigh`, 60 ms debounce. |
| 18 | N20 motor stall current vs MOSFET and supply | Brown-out, MOSFET heating | BRINGUP 9. Separate motor supply, flyback diode. |
| 19 | ESP32 supply under camera + Wi-Fi peaks; ground bounce from the Peltier | Brown-out resets (`brownout_reset` flag), ADC noise | BRINGUP 10d, scope on 3.3 V during Peltier switching |
| 20 | Wi-Fi RSSI inside an XPS/foil chamber | Dropped telemetry/commands | Local control continues offline. Check `wifi_rssi_dbm`. Move the antenna or board outside the foil. |
| 21 | Camera through the window: glare, condensation, exposure drift | Bad height estimates on the Mac | BRINGUP 8. Fixed lighting. `CameraSettings` exposure fields. |
| 22 | Food contact of the starter probe | Food safety | Separate non-edible instrumented sample (SENSOR_WIRING) |
| 24 | ~~OneWire library supports GPIO47 on the ESP32-S3~~ **Resolved 2026-10-31:** stock OneWire 2.3.8 direct-GPIO code caps the second bank at GPIO45 and refuses output above GPIO33 (original-ESP32 rules), so GPIO47 was dead. Patched copy vendored in `firmware/lib/OneWire` (`FERMENTAI_PATCH.md`). | No DS18B20 ever found although the bus is electrically fine | Console `owtest` (manual presence pulse) vs `owsearch` (library) |
| 23 | Fans actually running (no tach/current sense in this design) | Peltier overheats with a dead fan. Only the heatsink probe and hardware thermostat catch it. | Visual check. Consider adding a tach input (contract `fan_fail` is reserved). |

## What software does NOT provide

- Protection during the ~300 ms between reset and `holdOutputsSafe()`. External pull-downs
  provide it.
- Thermal safety if the ESP32 hangs, resets or loses power. The hardware thermostats, the fuse
  and the EN pull-down cover that.
- Fan failure detection. There is no sensor for it.
- Any guarantee that a compiled pin map is electrically valid.
