# GPIO map: ESP32-S3-CAM (PROVISIONAL)

**Status: provisional. None of these pins has been checked on the physical Aideepen board.**
The firmware compiles, but compile success is not proof of electrical compatibility.

Every pin number lives in `include/board_config.h`. That file is the only place pins are named,
and its `static_assert`s refuse to build if a peripheral pin lands on a camera, flash/PSRAM,
USB, UART0 or strapping pin.

## 0. What the analysis is based on

| Source | What it gives | Verified? |
|---|---|---|
| Freenove `Freenove_ESP32_S3_WROOM_Board` repo, `camera_pins.h`, `CAMERA_MODEL_ESP32S3_EYE` | OV2640 pin set (table 1) | **Vendor document only.** The Aideepen "ESP32-S3-CAM" is assumed to be a Freenove-layout clone. This has not been confirmed. |
| Freenove ESP32-S3-WROOM starter-kit docs, "Notes for GPIO" | camera pins (same set), OPI PSRAM uses GPIO35–37, SD card on GPIO38–40 (1-bit SDMMC), USB 19/20, strapping pins 0/3/45/46 | Vendor document only |
| Espressif ESP32-S3 datasheet / ESP32-S3-WROOM-1 datasheet (general knowledge) | GPIO26–32 are SPI flash/PSRAM; 33–37 are lost with octal PSRAM; ADC2 is unusable while Wi-Fi is on; strapping functions | Datasheet, module variant not yet identified |
| A physical board in hand, a multimeter or scope, and the schematic | — | **Not done** |

## 1. Camera (OV2640), Freenove / ESP32-S3-EYE set

| Signal | GPIO | Signal | GPIO |
|---|---|---|---|
| XCLK | 15 | Y9 (D7) | 16 |
| SIOD (SCCB SDA) | 4 | Y8 (D6) | 17 |
| SIOC (SCCB SCL) | 5 | Y7 (D5) | 18 |
| VSYNC | 6 | Y6 (D4) | 12 |
| HREF | 7 | Y5 (D3) | 10 |
| PCLK | 13 | Y4 (D2) | 8 |
| PWDN | not connected (-1) | Y3 (D1) | 9 |
| RESET | not connected (-1) | Y2 (D0) | 11 |

- The camera uses GPIO 4–13 and 15–18.
- XCLK uses LEDC channel 0 / timer 0. The Peltier PWM uses LEDC channels 2/3 (timer 1), so they never share a timer.
- SCCB (4/5) is separate from the sensor I²C bus (41/42). The camera driver uses its own I²C port.

## 2. Pins that are reserved or unavailable

| GPIO | Why | Use? |
|---|---|---|
| 0 | Strapping (boot mode); BOOT button | no |
| 3 | Strapping (JTAG signal source) | avoid |
| 45 | Strapping (VDD_SPI voltage). Wrong level at reset can brick flash access. | no |
| 46 | Strapping (boot mode / ROM log) | no |
| 19, 20 | Native USB D-/D+. GPIO20 (D+) has a USB pull-up at reset. | no |
| 26–32 | SPI flash + PSRAM bus | no |
| 33–37 | Octal flash/PSRAM bus (35–37 are taken by OPI PSRAM on N8R8/N16R8) | no |
| 43, 44 | UART0 TX/RX (programming/log via the CH340/CH343 port) | no |
| 38, 39, 40 | microSD in 1-bit SDMMC mode (Freenove: CMD=38, CLK=39, D0=40) | **conflict, see §3** |
| 2 | Freenove on-board LED (unverified for Aideepen) | spare |
| 48 | On-board RGB LED on some S3 boards (unverified) | spare |
| 47, 48 | Powered from VDD_SPI on 1.8 V-PSRAM module variants ("R8V"/"R16V") | **see §3, row OneWire** |

Candidate free pins once the camera, PSRAM and USB are excluded: **1, 2, 14, 21, 38–42, 47, 48**.
GPIO38–40 are free only if the SD slot is unused and has no pull-ups that matter. GPIO3, 45 and 46
are strapping pins and are avoided.

## 3. Proposed assignments (PLAN_MASTER §5.3) and conflict analysis

| Function | GPIO | Electrical need | Conflicts found | Verdict |
|---|---|---|---|---|
| I²C SDA (BME680/SHT31) | 41 | open-drain, pull-up (on most breakouts) | JTAG MTDI after reset. Free because JTAG goes through USB by default. | **OK on paper.** Verify the pin is broken out on the header. |
| I²C SCL | 42 | as above | JTAG MTMS, as above | **OK on paper** |
| DS18B20 OneWire | 47 | 3.3 V, 4.7 kΩ pull-up | GPIO47 is in the VDD_SPI domain. On a **1.8 V-PSRAM module variant it is a 1.8 V pin**, and a 3.3 V pull-up would over-volt it. On N8R8/N16R8 (3.3 V) it is fine. | **Conditional.** Read the module marking first. If it is a "V" variant, move OneWire to GPIO2 or 21. |
| Door reed switch | 39 | input + pull-up | SD-card CLK. A board pull-up on the SD line is harmless for a switch to GND. An inserted SD card would conflict. | **OK if the SD slot stays empty.** |
| pH analog (A only) | 1 | ADC1_CH0, ≤ 3.1 V at 11 dB | None. ADC1 works with Wi-Fi on; GPIO1 is not a strapping pin. | **OK on paper.** The input voltage must be limited, see SENSOR_WIRING. |
| Peltier RPWM (dir A) | 14 | PWM out, must be LOW at reset | None found (not camera/strap/SD) | **OK on paper.** Add an external 10 kΩ pull-down. |
| Peltier LPWM (dir B) | 21 | PWM out, must be LOW at reset | None found | **OK on paper.** Add an external 10 kΩ pull-down. |
| Peltier enable (R_EN+L_EN) | 40 | must be LOW at reset | **SD-card D0.** If the board has the usual 10 kΩ SD pull-up, the BTS7960 is **enabled during reset/boot**, before firmware runs. | **UNRESOLVED.** Measure (BRINGUP step 2). If HIGH at reset, use `-DFERMENTAI_PINMAP_ALT=1`. |
| Motor MOSFET gate | 38 | must be LOW at reset | **SD-card CMD.** A board pull-up would switch the motor **on during reset/boot**. | **UNRESOLVED.** Same check. If HIGH, use the ALT map. |

### Alternative map (`-DFERMENTAI_PINMAP_ALT=1`)

| Function | ALT GPIO | Notes |
|---|---|---|
| Peltier enable | 2 | Freenove LED to GND through a resistor acts as a weak pull-down (good). The LED will light when enabled. Verify. |
| Motor gate | 48 | Not a strapping pin. If it drives a WS2812 data-in on this board, the LED shows garbage (cosmetic). Verify the reset level. |

In both maps the power outputs **must** have external pull-downs: 10 kΩ on BTS7960 EN/RPWM/LPWM
and 100 kΩ on the MOSFET gate. That keeps them off in the ~300 ms between reset and
`holdOutputsSafe()`.

## 4. PLAN_MASTER §5.3 checklist

| Step | Status |
|---|---|
| 1. Locate the exact board schematic/pinout | **Not done.** Only the Freenove docs were used. Get the Aideepen listing/schematic, or photograph the board and its silkscreen. |
| 2. Identify the camera GPIOs | Done **from vendor docs** (table 1). Not confirmed on hardware. |
| 3. Identify flash, PSRAM, SD, USB and boot-pin conflicts | Done on paper (tables 2–3). Two unresolved SD conflicts (38, 40) and one module-variant condition (47). |
| 4. Verify pin exposure on the physical board | **Not done.** GPIO 1, 14, 21, 38–42 and 47 must actually be on the headers. |
| 5. Check voltage levels and boot behaviour | **Not done.** See BRINGUP step 2 (reset-level measurement). |
| 6. Resolve conflicts | Partially. The ALT map is prepared, and the choice depends on step 5. |
| 7. Record the result here | Fill in §5 after bring-up. |

## 5. Bring-up results (fill in)

| Item | Measured value / observation | Date | By |
|---|---|---|---|
| Module marking (e.g. ESP32-S3-WROOM-1 N16R8) | | | |
| PSRAM detected (`status` → psram > 0) | | | |
| Camera init OK with table 1 | | | |
| GPIO38 level during reset (RST held) / after boot | | | |
| GPIO40 level during reset / after boot | | | |
| GPIO14 / GPIO21 level during reset | | | |
| GPIO47 pull-up voltage (must be 3.3 V domain) | | | |
| Pins 1, 14, 21, 38, 39, 40, 41, 42, 47 present on the header | | | |
| Pin map chosen (default / ALT) | | | |
