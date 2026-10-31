# FermentAI Node: product concept mockup

What the FermentAI chamber electronics could look like as a product: one retrofit module that
drops into any fermentation space (a home proofing box, a restaurant cabinet, a bakery retarder)
and brings every sensor the hackathon chambers use, plus the chef-hat automaton.

**This is a concept rendering, not hardware that exists.** See "Real today vs concept" below.

- `sensor_module_mockup.png` (3200 × 2160): hero view + callout views + spec strip, in the **black** colourway (current)
- `sensor_module_context.png` (1640 × 1040): in-context vignette (proofing box + phone tile), black
- `sensor_module_cap_variants.png` (1640 × 760): cap options, dark walnut (chosen) vs anodised charcoal
- `sensor_module_mockup_cream.png`, `sensor_module_context_cream.png`: the first cream colourway, kept for comparison
- `*.svg`: the vector sources for every PNG
- `assets/*.svg`: text-free illustrations (hero A/B, callouts A/B, proofing box, cap variants) used by the canvas
- `canvas/`: the design-canvas artboards (`.dc.html`) and index, mirrored from the Claude design
  artifact <https://claude.ai/artifact/GxTEVat8DfByxUbL4xYFA3> (private until shared). Their
  `/_blob/…` image URLs resolve only inside that artifact.
- `make_mockup.py`: the generator. Everything is vector shapes, gradients and blur shadows. There
  are no photos and no logos.

```bash
python3 data/design/make_mockup.py                 # SVG + PNG via headless Chrome
python3 data/design/make_mockup.py --assets-png    # also rasterise assets/*.svg (for canvas upload)
uv run --with cairosvg python data/design/make_mockup.py   # fallback when Chrome is absent (no blur shadows)
```

## Concept

A 120 × 80 × 35 mm soft-cornered bar, wall-mounted inside the chamber, facing the dough jar.

| Element | Design intent |
|---|---|
| Camera eye (left) | OV2640 behind smoked glass, ringed by a frosted fixed-colour ring light (constant exposure for rise tracking), ringed by the **status ring** |
| Status ring | Glows in the dashboard chamber colour: A `#1f77b4`, B `#ff7f0e`. Breathes while heating, pulses when the door opens |
| E-ink strip | Temperature, pH and rise with a small rise sparkline. Readable with the chamber light off, near-zero power, no glare in camera frames |
| Air vent grille | Staggered perforations in front of the BME680 (temp / RH / pressure / VOC gas resistance), thermally separated from the logic board |
| Walnut cap | The top 13 mm is solid oiled walnut. The ESP32-S3 2.4 GHz antenna sits under it: wood is RF-transparent, unlike a metal lid |
| Chef-hat automaton | Sits on a charcoal turntable on the walnut top. An N20 gear motor bobs it when the chamber's agent starts a new experiment |
| Underside ports (engraved) | `T1` starter DS18B20 (food-safe silicone sleeve, 1 m), `T2` aux DS18B20 (heatsink / second jar / ambient), `DOOR` reed pod + magnet tag, `PELTIER · FAN` 6-pin locking port to an external power pod, `USB-C` 20 W PD power + service |
| Right side | BNC pH port (isolated interface inside) with a tethered **shorting cap** that doubles as a 0 mV electronics check before buffer calibration |
| Back | Charcoal magnetic plate (4 magnets) + 2 keyholes, or VHB tape |

**Colourways.** The current one is **black**: a matte charcoal PC shell (about `#1f2124`, with a top-left
highlight on the face and a lit chamfer so it still reads as a form), a dark walnut cap, a smoked lens
with a smoked-silver frosted ring light, black cables and probe sleeve, a white chef hat, and a
**light** e-ink strip so the numbers stay readable. An anodised-charcoal cap is shown as the
alternative (`black_anodised`). The original **cream** colourway (matte cream PC + oiled walnut +
charcoal) is kept as `*_cream.png`. In every colourway the only accent is the chamber-colour status
ring. Light comes from the top left, with soft wall shadows down-right (softer for the dark body
on the light page). Leader lines have a light halo so they stay legible where they cross the
dark body.

**Specs shown (targets, not measurements):** 120 × 80 × 35 mm · IP54 splash · 5–40 °C ·
Wi-Fi 2.4 GHz (ESP32-S3) · USB-C 20 W · 1 m probe cables.

A deliberate safety choice: the Peltier current never passes through the module. The module sends
PWM/enable signals and runs a thermal cut-off loop to an external BTS7960 power pod. That matches
the prototype's rule that high-current branches are separately protected (PLAN_MASTER §5.4).

## Real today vs concept

| Function | Hackathon prototype (real, `firmware/docs/`) | In this concept |
|---|---|---|
| MCU / network | Aideepen ESP32-S3-CAM, Wi-Fi to the local Mac backend | same SoC, own PCB, antenna under walnut |
| Camera | on-board OV2640 behind the chamber window, separate LED strip | same sensor, integrated frosted ring light |
| Starter temp | DS18B20 on OneWire GPIO47, generic stainless probe (**not** certified food-safe; used in a non-edible sample jar) | T1 jack, food-safe silicone sleeve (would need certification) |
| Heatsink temps | 2 × DS18B20 on the same bus, assigned by ROM | T2 jack, one aux probe |
| Air T/RH/P/gas | BME680 on I²C 41/42. Chamber A currently fits an ENS160+AHT21 instead | BME680 behind the vent |
| pH | chamber A only: analog interface board → divider → GPIO1, two-point calibration in NVS | BNC port on every unit, shorting cap |
| Door | NO reed switch on GPIO39, magnet on the door | plug-in reed pod + magnet tag |
| Peltier + fans | BTS7960 on GPIO14/21/40, fans separately powered. **High-power outputs disabled until commissioning** | external power pod on the 6-pin port |
| Chef hat | N20 motor via MOSFET on GPIO38 | same mechanism under a turntable |
| Display / status ring / e-ink / IP54 / enclosure / walnut / magnets | **none exist** | concept only |

The numbers on the e-ink strip and phone (27 °C, pH 3.9, rise 1.8×; A: 22 °C, pH 4.4, 1.4×)
are illustrative and not measurements. The provisional GPIO map is unverified on the physical
board (see `firmware/docs/GPIO_MAP.md`).

## Iterating

Every visual parameter lives in `make_mockup.py`. The page palette is `C` and the material colourways are `THEMES`
(`cream`, `black`, `black_anodised`; one run renders black + cream). Body size and radius
(`W_MM`, `H_MM`, `R_MM`, `CAP_MM`), oblique depth `DX`/`DY`, the part functions
(`camera_eye`, `eink`, `grille`, `hat`, `probe`) and label tables (`LEFT_LABELS`,
`RIGHT_LABELS`, `PORT_LEGEND`, `SPECS`) are all there. After changing the art, run with
`--assets-png`, re-upload the PNGs to the canvas artifact and repoint the `/_blob/…` URLs in
`canvas/*.dc.html`. Copy changes only need the canvas: every label there is live HTML text.
