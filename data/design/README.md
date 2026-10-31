# FermentAI Node: product concept mockup

What the FermentAI chamber electronics could look like as a product: one small retrofit unit
that drops into any fermentation space (a home proofing box, a restaurant cabinet, a bakery
retarder) and brings every sensor the hackathon chambers use. The current design (v3) is a
**friendly character** styled after the Clawd mascot (`data/stl/external/clawd.stl`: a blocky
terracotta critter with two tall eyes, little arms and four feet), wearing the chef hat.

**This is a concept rendering, not hardware that exists.** See "Real today vs concept" below.

## Files

| File | What |
|---|---|
| `sensor_module_mockup.png` (3200 × 2160) | **current**: hero ("hi!", winking) + friendly callouts + spec strip |
| `sensor_module_context.png` (1640 × 1040) | **current**: the Node on the shelf of a proofing box next to the dough jar, plus the phone tile |
| `sensor_module_moods.png` (1640 × 760) | watching (A, blue) · watching (B, orange) · thinking (wink, hat bobbing) |
| `sensor_module_mockup_cream.png`, `sensor_module_context_cream.png` | v1: cream bar with a walnut cap |
| `sensor_module_mockup_black.png`, `sensor_module_context_black.png`, `sensor_module_cap_variants_black.png` | v2: charcoal bar, dark walnut vs anodised cap |
| `*.svg` | vector source of every PNG |
| `assets/*.svg` | text-free illustrations used by the design canvas (hero A/B × open/wink, callouts A/B, context box, moods) |
| `canvas/` | mirror of the Claude design canvas <https://claude.ai/artifact/GxTEVat8DfByxUbL4xYFA3> (private until shared). Its `/_blob/…` image URLs resolve only inside that artifact |
| `make_mockup.py` | the generator: vector shapes, gradients and blur shadows only, no photos, no logos |

```bash
python3 data/design/make_mockup.py                 # all SVG + PNG (headless Chrome); v1/v2 are re-rendered too
python3 data/design/make_mockup.py --assets-png    # also rasterise assets/*.svg (for canvas upload)
uv run --with cairosvg python data/design/make_mockup.py   # fallback without Chrome (no blur shadows)
```

## Concept (v3): the Node is the character

Same 120 × 80 × 35 mm body as before, now with very round corners (24 mm radius), little arms
and four feet. Every face feature is a real sensor or port:

| Feature | Hardware |
|---|---|
| Chef hat | the automaton. An N20 gear motor bobs it when the chamber's agent starts a new experiment. It sits in a soft clay socket |
| Left eye | the camera. OV2640 behind a smoked lens, with a soft fixed-colour ring light for constant exposure |
| Right eye | the status LED ring in the chamber colour (A `#1f77b4`, B `#ff7f0e`). It **winks** (closes to an arc) while the agent is thinking |
| Smile | the air vent. A dotted arc of perforations in front of the BME680 (temperature, humidity, pressure, VOC gas resistance) |
| Apron | a cream apron with an e-ink pocket showing temperature, pH and rise. Readable with the chamber light off |
| Right arm ("pH hand") | the BNC pH port, with a tethered shorting cap for a 0 mV check before buffer calibration |
| Tail | the T1 starter-probe cable (food-safe silicone sleeve, 1 m). Stored curled up, with the probe in a soft cap as the tail tip |
| Feet | four rubber feet. It stands on a shelf, or hangs on a wall from its magnetic back (4 magnets + 2 keyholes) |
| Underside, between the feet | engraved `T1`, `T2` (aux probe: heatsink, second jar, ambient), `DOOR` (reed pod + magnet tag), `PELTIER · FAN` (6-pin port to an external BTS7960 power pod), `USB-C` 20 W PD |
| Inside | ESP32-S3 + 2.4 GHz Wi-Fi to the local Mac backend |

**Materials:** soft-touch terracotta PC (Claude clay), a cream apron panel, smoked-glass eyes, a
white hat, and soft taupe cables. Accents are pastel: blush cheeks, plus the chamber-colour status
eye as the only saturated colour. Light comes from the top left. Shadows are soft contact shadows
on a light floor. The callout board has eight friendly labels.

A deliberate safety choice carries over from v1: Peltier current never passes through the Node.
It sends PWM/enable signals and runs a thermal cut-off loop to an external power pod, which
matches the prototype's rule that high-current branches are separately protected
(PLAN_MASTER §5.4).

Specs shown are **targets, not measurements**: 120 × 80 × 35 mm body · IP54 splash · 5–40 °C ·
Wi-Fi 2.4 GHz (ESP32-S3) · USB-C 20 W · 1 m probe cables.

## Real today vs concept

| Function | Hackathon prototype (real, `firmware/docs/`) | In this concept |
|---|---|---|
| MCU / network | Aideepen ESP32-S3-CAM, Wi-Fi to the local Mac backend | same SoC on its own PCB |
| Camera | on-board OV2640 behind the chamber window, separate LED strip | left eye, integrated ring light |
| Starter temp | DS18B20 on OneWire GPIO47, generic stainless probe (**not** certified food-safe; used in a non-edible sample jar) | T1 "tail" probe with a food-safe sleeve (would need certification) |
| Heatsink temps | 2 × DS18B20 on the same bus, assigned by ROM | T2 jack, one aux probe |
| Air T/RH/P/gas | BME680 on I²C 41/42. Chamber A currently fits an ENS160+AHT21 instead | BME680 behind the smile vent |
| pH | chamber A only: analog interface board → divider → GPIO1, two-point calibration in NVS | BNC "pH hand" on every unit, shorting cap |
| Door | NO reed switch on GPIO39, magnet on the door | plug-in reed pod + magnet tag |
| Peltier + fans | BTS7960 on GPIO14/21/40, fans separately powered. **High-power outputs disabled until commissioning** | external power pod on the 6-pin port |
| Chef hat | N20 motor via MOSFET on GPIO38 | same mechanism under the hat socket |
| Status eye / wink / e-ink apron / IP54 / enclosure / magnets / feet | **none exist** | concept only |

The numbers on the apron and phone (27 °C, pH 3.9, rise 1.8×; A: 22 °C, pH 4.4, 1.4×) are
illustrative and not measurements. The provisional GPIO map is unverified on the physical board
(see `firmware/docs/GPIO_MAP.md`). The character styling follows the Clawd mascot model supplied in
`data/stl/external/`.

## Iterating

Everything lives in `make_mockup.py`:
- **v3 character:** `CH` colours, `BODY_R`, `LEGS`, `EYE_L`/`EYE_R`, `char_eyes`, `char_face`
  (apron, blush, smile vent), `char_hero` (oblique view + "hi!" bubble), `char_front`,
  `char_bottom`, `char_back`, callout tables `CLEFT`/`CRIGHT`/`CUNDER`/`CBACK`, and `MOODS`.
- **v1/v2:** use `THEMES` (`cream`, `black`, `black_anodised`).

After changing the art, run with `--assets-png`, re-upload the PNGs to the canvas artifact and
repoint the `/_blob/…` URLs in `canvas/*.dc.html`. Copy-only changes need just the canvas: every
label there is live HTML text.
