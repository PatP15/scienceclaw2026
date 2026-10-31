# Print sheet — hackathon_by_resin

Prepared with the Formlabs PreForm API (preform_local_api, PreForm 3.64.2.665). **Nothing has been sent to a printer.** Printing requires a person at PreForm and at the printer. All times and volumes are PreForm estimates.

**To print: 2 build(s)**, ≈ 3.60 h of printer time; resin: Clear V5 ≈ 239 mL, Black V5 ≈ 56 mL.

## Clear V5 (`FLGPCL05`) · Form 4 · 0.1 mm — job `clear_form4`

_All four Clear parts on one plate. Slabs: crumb face up, flat floor printed directly on the build platform (no supports, no raft). Hats: upright as modelled, mini-raft supports._

### Build 1 of 1: `clear_form4_build1.form`

- **Open:** `data/stl/print/clear_form4_build1.form`
- **Parts (4):** A — mild, 22 °C; B — sour, 27 °C; hat_1 — chef hat, user-supplied; hat_2 — chef hat, user-supplied
- **Orientation / supports:** A: crumb face up, directly on the build platform (no supports, no raft); B: crumb face up, directly on the build platform (no supports, no raft); hat_1: upright (as modelled), MINI_RAFT; hat_2: upright (as modelled), MINI_RAFT
- **Estimate:** 2.4 h (8654 s), 238.74 mL resin (models 222.18, raft 7.26, supports 9.3), 714 layers
- **Preview:** `data/stl/print/clear_form4_build1_iso.png`, `data/stl/print/clear_form4_build1_top.png`
- **Manifest:** `data/stl/print/clear_form4_build1_manifest.json`
- **Note:** A, B: no supports or raft — printed directly on the build platform (flat floor on the platform, crumb face up). Release with the platform flex / removal tool; the underside takes the platform texture.
- **PreForm warnings:** 
  - B: PreForm reports 2 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.
  - A: PreForm reports 1 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.
  - B: 0.0448% of its area is undersupported (slab printed without supports by design: this is overhang area under pore lips inside craters; review in PreForm)

**Clear V5 notes:**

- Clear shows everything: support touchpoints, raft marks and any trapped resin or cups are visible through the part. Sand touchpoints (wet, fine grit) and clear-coat or polish if you want glass-like faces.
- Clear V5 TDS reference: Form Wash 5 min in ≥99% IPA; post-cure 15 min at 60 °C in a Form Cure. Clear yellows slightly with longer or hotter cure (and with UV over time): for display parts, do not over-cure; check the current Clear V5 TDS.

## Black V5 (`FLGPBK05`) · Form 4 · 0.1 mm — job `black_form4`

_Both Clawd figures on one plate, PreForm auto-orient + auto-supports (full raft)._

### Build 1 of 1: `black_form4_build1.form`

- **Open:** `data/stl/print/black_form4_build1.form`
- **Parts (2):** clawd_1 — Clawd figure, user-supplied; clawd_2 — Clawd figure, user-supplied
- **Orientation / supports:** clawd_1: PreForm auto-orient (tilted), FULL_RAFT; supports touch visible surfaces; clawd_2: PreForm auto-orient (tilted), FULL_RAFT; supports touch visible surfaces
- **Estimate:** 1.2 h (4318 s), 56.2 mL resin (models 49.39, raft 4.71, supports 2.11), 429 layers
- **Preview:** `data/stl/print/black_form4_build1_iso.png`, `data/stl/print/black_form4_build1_top.png`
- **Manifest:** `data/stl/print/black_form4_build1_manifest.json`
- **PreForm warnings:** none

**Black V5 notes:**

- Black V5 TDS reference: Form Wash 5 min in ≥99% IPA; post-cure 15 min at 60 °C in a Form Cure (some TDS versions: 5 min at room temperature). Check the current sheet.

## ALTERNATIVE — Clear V5 (`FLGPCL05`) · Form 4 · 0.1 mm — job `clear_form4_split`

_Only if you prefer rafts + floor-only supports under the slabs (easier release, no platform texture). Needs more than one Clear build._

### Build 1 of 2: `clear_form4_split_build1.form`

- **Open:** `data/stl/print/clear_form4_split_build1.form`
- **Parts (3):** A — mild, 22 °C; B — sour, 27 °C; hat_1 — chef hat, user-supplied
- **Orientation / supports:** A: crumb face up, FULL_RAFT, supports only under the flat floor; B: crumb face up, FULL_RAFT, supports only under the flat floor; hat_1: upright (as modelled), MINI_RAFT
- **Estimate:** 2.39 h (8612 s), 206.22 mL resin (models 174.58, raft 24.91, supports 6.73), 714 layers
- **Preview:** `data/stl/print/clear_form4_split_build1_iso.png`, `data/stl/print/clear_form4_split_build1_top.png`
- **Manifest:** `data/stl/print/clear_form4_split_build1_manifest.json`
- **PreForm warnings:** 
  - B: PreForm reports 2 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.
  - A: PreForm reports 1 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.

### Build 2 of 2: `clear_form4_split_build2.form`

- **Open:** `data/stl/print/clear_form4_split_build2.form`
- **Parts (1):** hat_2 — chef hat, user-supplied
- **Orientation / supports:** hat_2: upright (as modelled), MINI_RAFT
- **Estimate:** 1.68 h (6044 s), 55.88 mL resin (models 47.6, raft 3.63, supports 4.65), 714 layers
- **Preview:** `data/stl/print/clear_form4_split_build2_iso.png`, `data/stl/print/clear_form4_split_build2_top.png`
- **Manifest:** `data/stl/print/clear_form4_split_build2_manifest.json`
- **PreForm warnings:** none

**Clear V5 notes:**

- Clear shows everything: support touchpoints, raft marks and any trapped resin or cups are visible through the part. Sand touchpoints (wet, fine grit) and clear-coat or polish if you want glass-like faces.
- Clear V5 TDS reference: Form Wash 5 min in ≥99% IPA; post-cure 15 min at 60 °C in a Form Cure. Clear yellows slightly with longer or hotter cure (and with UV over time): for display parts, do not over-cure; check the current Clear V5 TDS.

## How to print each build (PreForm desktop)

_Menu names as in PreForm 3.x; the Insider build may label them slightly differently._

1. PreForm › **File › Open…** › choose the `.form` above (it opens as its own job).
2. Check the job: the job-info / print-validation panel shows the warnings listed above (cups, unsupported areas). Inspect the flagged parts; do not add or move supports on the crumb faces.
3. Confirm the job setup (printer type, resin, 0.1 mm) matches the section heading — Clear and Black are different cartridges *and* need their own resin tank.
4. Click **Print** › select the Form 4 from the printer list (or its Dashboard group) › **Upload** (adds the job to the printer's queue).
5. At the printer: load the matching cartridge, its resin tank and a clean build platform › select the job on the touchscreen › **confirm** › Print.

## After printing

- Remove the build platform and release the parts (flex the platform or use the part-removal tool). Slabs printed directly on the platform need a careful, flat release.
- Wash in a Form Wash (≥99% IPA, see the resin notes above); let parts dry completely before curing; cure in a Form Cure per the resin notes.
- Clip supports after curing and sand the touchpoints. The crumb faces carry no supports; on the hats and Clawds touchpoints are on the surfaces PreForm chose.
- **Display only — not food-safe. Never place prints in contact with bread.**
