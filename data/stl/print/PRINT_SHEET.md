# Print sheet — hackathon

Prepared with the Formlabs PreForm API (preform_local_api, PreForm 3.64.2.665). **Nothing has been sent to a printer.** Printing requires a person at PreForm and at the printer.

Printer **Form 4**, resin **Grey V5** (`FLGPGR05`), **0.1 mm** layers. All times and volumes are PreForm estimates.

**2 build(s)** — total ≈ 3.66 h printing, ≈ 315 mL resin.

## Build 1: `hackathon_job1_form4-grey-v5.form`

- **Open:** `data/stl/print/hackathon_job1_form4-grey-v5.form`
- **Parts (2):** A — mild, 22 °C; B — sour, 27 °C
- **Orientation:** A, B: crumb face up, supports only under the flat floor; hat_1, hat_2, clawd_1, clawd_2: PreForm auto-orient (tilted); supports touch visible surfaces
- **Estimate:** 1.36 h (4889 s), 150.3 mL resin (models 126.9, raft 21.34, supports 2.06), 185 layers
- **Preview:** `data/stl/print/hackathon_job1_form4-grey-v5_iso.png`, `data/stl/print/hackathon_job1_form4-grey-v5_top.png`
- **Manifest:** `data/stl/print/hackathon_job1_form4-grey-v5_manifest.json`
- **Warnings:** 
  - A: PreForm reports 1 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.
  - B: PreForm reports 2 cup(s) — concave pockets under overhanging pore lips that can add peel suction. All pores open to the cut face (mesh validation: no enclosed cavities), so resin drains in the wash; a human should review them in PreForm.

## Build 2: `hackathon_job2_form4-grey-v5.form`

- **Open:** `data/stl/print/hackathon_job2_form4-grey-v5.form`
- **Parts (4):** hat_1 — chef hat, user-supplied; hat_2 — chef hat, user-supplied; clawd_1 — Clawd figure, user-supplied; clawd_2 — Clawd figure, user-supplied
- **Orientation:** hat_1, hat_2, clawd_1, clawd_2: PreForm auto-orient (tilted); supports touch visible surfaces
- **Estimate:** 2.3 h (8285 s), 165.08 mL resin (models 144.51, raft 14.85, supports 5.73), 772 layers
- **Preview:** `data/stl/print/hackathon_job2_form4-grey-v5_iso.png`, `data/stl/print/hackathon_job2_form4-grey-v5_top.png`
- **Manifest:** `data/stl/print/hackathon_job2_form4-grey-v5_manifest.json`
- **Warnings:** 
  - hat_2: PreForm reports 1 cup(s) — pockets that can trap resin or add peel suction; review in PreForm (drain holes or a different orientation may be needed).
  - hat_1: PreForm reports 1 cup(s) — pockets that can trap resin or add peel suction; review in PreForm (drain holes or a different orientation may be needed).
  - hat_2: 0.0625% of its area is undersupported
  - hat_1: 0.0625% of its area is undersupported

## How to print each build (PreForm desktop)

_Menu names as in PreForm 3.x; the Insider build may label them slightly differently._

1. PreForm › **File › Open…** › choose the `.form` above (it opens as its own job).
2. Check the job: the job-info / print-validation panel shows the warnings listed above (cups, unsupported areas). Inspect the flagged parts; do not add or move supports on the crumb faces.
3. Confirm the job setup reads **Form 4 · Grey V5 · 0.1 mm** (Job Setup / printer selector at the top of the window).
4. Click **Print** › select the Form 4 from the printer list (or its Dashboard group) › **Upload** (adds the job to the printer's queue).
5. At the printer: load the Grey V5 cartridge, a Form 4 resin tank and a clean build platform › select the job on the touchscreen › **confirm** › Print.

## After printing

- Remove the build platform and release the parts (flex the platform or use the part-removal tool).
- **Wash:** Form Wash, ≥99% IPA, **5 min** (Grey V5 TDS reference). Let dry completely (≥30 min, or compressed air).
- **Cure:** Form Cure per the current Grey V5 TDS (published versions list 5 min at room temperature and 15 min at 60 °C). Check the current sheet.
- Clip supports after curing and sand the touchpoints. The crumb faces carry no supports; on auto-oriented parts (hat, Clawd) touchpoints are on visible surfaces.
- **Display only — not food-safe. Never place prints in contact with bread.**
