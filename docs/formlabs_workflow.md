# Formlabs workflow: crumb-chamber specimens → PreForm API → prepared `.form`

**What ran (live, 2026-10-31; re-run after the chamber STLs were regenerated):** a real PreFormServer 3.64.2.665 (the headless PreForm from
Formlabs, Developer-ID-signed by Formlabs Inc. and notarized) on this Mac, driven by the
official Formlabs Python SDK. It imported `crumb_chamber_A.stl` and `crumb_chamber_B.stl`,
oriented, supported and laid them out on one Form 4 build platform in Grey V5 at 0.1 mm, ran 7
printability checks, calculated print time and resin volume, rendered previews, and saved `.form`
files. It also reopened each saved file through the API to confirm it loads. **No print job was
submitted.** Every HTTP call is in `data/stl/print/preform_api_transcript.json`, and the server's
own log is in `data/stl/print/preformserver.log`.

> **Pitch-safe wording:** "Prepared and validated with the Formlabs PreForm API; printing
> requires human confirmation."
>
> Do **not** say "printed", "sliced and sent to the printer", or "Formlabs verified the
> morphology". The geometry is a simulated hypothesis (`data/stl/chain_summary.json`). PreForm
> checked that it is printable, not that it is right.

## 1. Software (exact versions)

| Component | Version / source |
|---|---|
| PreForm API | v1.0.0-alpha (OpenAPI spec `https://developer.formlabs.com/reference/openapi-v1.yml`, bundled in the SDK) |
| Python SDK | `formlabs-sdk` 1.0.0a0.post2 + generated client `formlabs-preform-api` 1.0.0a0. **Not on PyPI.** Installed from GitHub per the Formlabs docs: `uv add "formlabs-sdk @ git+https://github.com/Formlabs/formlabs-api-python@v1.0.0-alpha.post2"` |
| PreFormServer | 3.64.2.665, macOS arm64. Public direct download, no login or EULA click: `https://downloads.formlabs.com/PreFormServer/Release/3.64.2/PreForm_Server_mac-arm64_3.64.2_release_releaser_665_161434.zip` (zip sha256 `6ca339e7…c098e6`). All releases: https://formlabs.com/support/Formlabs-API-downloads-and-release-notes/ |
| Install location | `~/.cache/fermentai/preform-server/3.64.2/PreFormServer/PreFormServer.app` (outside the repo; override with `FERMENTAI_PREFORM_SERVER`) |

Other ways to get a server: the PreForm desktop Insider build (File › Start PreForm Server), or on
Linux `docker run --rm -p 127.0.0.1:44388:44388 formlabs/preform-server:release-latest --port 44388`.
The **Formlabs Web API** (Dashboard: fleet and printer status, group queues) needs a Formlabs
account and developer credentials (`FORMLABS_CLIENT_ID` / `FORMLABS_CLIENT_SECRET` from
https://dashboard.formlabs.com/#developer). It is documented here only and is not used or
required.

## 2. Running it

```bash
uv run python scripts/prepare_print.py status                  # is a live server available?
uv run python scripts/prepare_print.py prepare                 # live if possible, else mock
uv run python scripts/prepare_print.py prepare --backend live --update-fixture
uv run python scripts/prepare_print.py prepare --backend mock --out /tmp/mock   # no binary needed
uv run python scripts/prepare_print.py prepare --orientation preform_auto --no-per-chamber \
    --out data/stl/print/alternative_preform_auto            # PreForm's own orientation
```

`prepare` starts PreFormServer itself: `--port 44388`, telemetry off, stdout to
`preformserver.log`, and it waits for `READY FOR INPUT`. If a PreForm server is already
listening on the port, it attaches to that one instead. Either way, the server is shut down at the
end. All SDK traffic goes through `RecordingProxy` (`src/fermentai/formlabs/replay.py`), which
logs each request and response and also **refuses `/v1/scene/print/`** unless the human gate in
§7 was passed.

## 3. Pipeline and the exact endpoints used

All calls are `POST` with a JSON body unless marked otherwise. Long-running calls go through
`?async=true`, and the SDK polls `GET /v1/operation/get/`.

| # | Endpoint | Purpose |
|---|---|---|
| 0 | `GET /v1/` | health check / server version |
| 1 | `/v1/print-setting/validate/` | Form 4 + `FLGPGR05` + 0.1 mm + `DEFAULT` is in PreForm's catalog |
| 2 | `/v1/scene/create/` | Job Setup: `machine_type=FORM-4-0`, `material_code=FLGPGR05`, `layer_thickness_mm=0.1` |
| 3 | `/v1/scene/model/import/` | each STL, `units=MILLIMETERS`, `repair_behavior=ERROR` (never silently repair) |
| 4 | `/v1/scene/model/list/` | snapshot of orientation / bbox / supports / in-bounds (before, after auto-orient, final) |
| 5 | `/v1/scene/model/auto-orient/` | PreForm's suggestion (recorded in the manifest) |
| 6 | `/v1/scene/model/orient/` | enforce open face up: `z_direction=[0,0,1]`, `x_direction=[1,0,0]` |
| 7 | `/v1/scene/model/auto-support/` | `raft_type=FULL_RAFT`; other settings are PreForm defaults for the material |
| 8 | `/v1/scene/model/auto-layout/` | both models on one platform, 5 mm spacing (rotation about Z only) |
| 9 | `/v1/scene/model/validate/{in-bounds,manifoldness,supportedness,minima,cups,interferences,thin-features}/` | printability checks (read-only) |
| 10 | `/v1/scene/get/` | `material_usage.volume_ml`, `layer_count`, build volume |
| 11 | `/v1/scene/calculate-print-time/` | full print-time calculation. `estimate-print-time` returns `ESTIMATION_NOT_SUPPORTED` on Form 4 |
| 12 | `/v1/scene/metadata/` | per-model / raft volumes, material name, PreForm build |
| 13 | `/v1/scene/render/` | PNG previews (iso, top) |
| 14 | `/v1/scene/save/` | `.form` file for a human to open in PreForm |
| 15 | `/v1/scene/open/`, `/v1/scene/delete/` | reopen the saved `.form` to verify it, then discard that copy |
| — | `/v1/scene/print/` | **never called.** Only reachable through `submit` (§7) |

The run does this once for A+B on one platform, then once each for A and B alone.

## 4. Printer and resin choice

**Form 4, Grey Resin V5 (`FLGPGR05`), 0.1 mm layers, `DEFAULT` print setting.**

- **Fit:** PreForm's catalog gives the Form 4 a 200 × 125 × 210 mm build volume, so both 80 × 80 ×
  12 mm slabs fit flat, side by side, crumb face up. A Form 3/3+ (145 × 145 mm) cannot hold both
  flat. The Form 4L (`--profile form4l-grey-v5`) also works but is unnecessary.
- **Resin:** Grey is Formlabs' standard matte resin for showing surface relief. The pore craters
  read clearly in photos and under display lights. White V5 (`--profile form4-white-v5`) looks
  more like crumb but shows less shading.
- **Layer height:** 0.1 mm is the default balance of speed and detail. The thinnest designed wall
  is about 1.2 mm (more than 10 layers), and PreForm's thin-feature check found no walls under
  0.5 mm.

## 5. Orientation and supports for an open-faced crumb slab

The STL's crumb face (the pores cut by the "bread knife": the scientific surface) is the +Z face
at z = 12 mm. The solid floor is the flat z = 0 face, with at least 4 mm of solid material under
every pore.

**Default: open face up.** The model's +Z is set to the scene's +Z, so the floor faces the build
platform.

- Raft and supports attach only to the flat floor, which is hidden when the specimen is
  displayed. The top-down render (`*_top.png`) shows no supports on either crumb face.
- Craters open away from the platform. Every pore opens to the cut face, and the mesh validation
  found no enclosed cavities, so no drain holes are needed.
- `percent_unsupported = 0` and `minima_count = 0` for both models. Both are manifold and in
  bounds, with no collisions and no thin walls.
- **Reported, not hidden:** PreForm's cup check flags **1 cup on A and 2 on B** (current STLs; 1 and 3 on the previous geometry). These are
  pockets under overhanging pore lips, which can add peel suction. Resin can still drain from
  them because they open to the face. They appear as `printability_warnings` in the manifest.
  Look at them in PreForm before printing.

**Alternative: PreForm's own auto-orient**, saved in `data/stl/print/alternative_preform_auto/`.
PreForm tilts each slab about 9–12°. That gives fewer cups (A 0, B 1) but needs more supports and
takes longer. The crumb faces stay support-free in this layout too.

| Live result (A+B, Form 4, Grey V5, 0.1 mm) | Open face up (default) | PreForm auto-orient |
|---|---|---|
| Print time (`calculate-print-time`) | **1.36 h** (4892 s) | 1.62 h (5822 s) |
| Resin total (`scene.material_usage`) | **150.3 mL** | 160.0 mL |
| of which: models / raft / supports | 126.9 / 21.34 / 2.06 mL | 126.9 / 21.47 / 11.63 mL |
| Layers | 185 | 361 |
| Cups (A, B) | 1, 2 | 0, 1 |

Single-chamber builds (open face up): A takes 1.06 h and 76.2 mL; B takes 1.05 h and 74.1 mL.
Print times vary by a few seconds between runs because auto-layout placement varies. Resin
volumes are stable. All of these are PreForm **estimates** for the prepared scene (provenance
`estimated`), not measurements of a finished print.

## 6. Outputs (`data/stl/print/`)

| File | What it is |
|---|---|
| `crumb_chambers_AB_form4-grey-v5.form` | both specimens on one Form 4 platform, ready to open in PreForm |
| `crumb_chamber_{A,B}_form4-grey-v5.form` | each specimen alone |
| `*_iso.png`, `*_top.png` | PreForm-rendered previews (`/v1/scene/render/`) |
| `print_manifest.json` | provenance: STL sha256 values (matching `chain_summary.json`), printer, material, layer, orientation (PreForm's suggestion and the final orientation), support and layout parameters, all check results, estimates, `.form`/PNG sha256 values, API/SDK/server versions, code signature, `job_submitted: false` |
| `preform_api_transcript.json` | every HTTP request/response of the live run (repo paths scrubbed to `<REPO>`) |
| `preformserver.log` | PreFormServer's stdout (`READY FOR INPUT`, each request received, shutdown) |
| `alternative_preform_auto/` | the same outputs for PreForm's auto-orient |

`source` in the manifest is `"preform_local_api"` for live runs. For mock runs it starts with
`"mock — …"`, `provenance` is `replay`, and the `.form`/`.png` files are clearly marked
placeholders.

## 7. Printing: human-gated only

```bash
FERMENTAI_FORMLABS_CONFIRM_PRINT=yes uv run python scripts/prepare_print.py submit \
    --form data/stl/print/crumb_chambers_AB_form4-grey-v5.form --printer <serial|IP|queue-id> \
    --i-confirm-print
```

`submit` always prints a summary first. It exits with code 2 without connecting to anything
unless **both** the `--i-confirm-print` flag **and** `FERMENTAI_FORMLABS_CONFIRM_PRINT=yes` are
present, and a printer is named. Even with both, it uses `print_now=False`: the job goes into the
printer's queue, and a person starts it at the printer after loading the Grey V5 cartridge, tank
and platform. The mock server never prints.

## 8. Post-processing

The Grey V5 technical data sheet's reference parts were washed for **5 min in ≥99% IPA in a
Form Wash**. Published TDS versions give different post-cure conditions (5 min at room
temperature, and 15 min at 60 °C in a Form Cure), so check the current TDS or Form Wash/Cure
settings (https://formlabs.com/support/Form-Wash-Time-Settings/) before curing. Let the parts dry
completely before curing. Remove the raft and supports from the floor side after curing. The
crumb face needs no support cleanup. Use gloves until the parts are fully cured.

## 9. Food-contact caveat

The prints are **display objects only**. Standard Formlabs resins (Grey/White V5) are not
food-safe. Never place a printed crumb specimen in contact with bread, dough or starter, and never
put one in a fermentation chamber. They sit next to the real loaf cross-section for visual
comparison.

## 10. Any parts: job files (the six-part hackathon print)

```bash
uv run python scripts/prepare_print.py prepare --job data/stl/print/jobs/hackathon_six.yaml \
    --backend live --port 44390
uv run python scripts/prepare_print.py prepare --part ~/model.stl:2 --part slab.stl:1:open_face_up
```

A job file lists parts in **priority order**, each with `count`, `orientation` (`open_face_up` or
`preform_auto`), `repair` and `label`. `chamber: A` pulls the hash and validation from
`chain_summary.json`. All copies are imported into one scene, oriented and supported, then laid
out. If they do not all fit (`auto-layout` fails with `OPERATION_FAILED`), the lowest-priority
copy is deleted and the layout retried. This is Formlabs' own "layout across builds" pattern.
Deferred copies start the next build. Every attempt is recorded in the manifest under
`layout.attempts`. **Parts are never scaled.** User files are imported strictly first; PreForm's
repair is used only if PreForm refuses the mesh, and that is recorded per copy under
`inputs[].import`.

**User parts (`data/stl/external/`, copied unchanged from `~/Downloads/`; see
`parts_report.json`):**
- `hat.stl`: 67.9 × 42.7 × 64.9 mm, so millimetres are plausible. It is a chef's hat: a hollow band
  with 0.8 mm walls, open at the bottom, under a solid puff. Volume 47.1 cm³.
- `clawd.stl`: 45.8 × 22.5 × 33.0 mm, so millimetres are plausible. It is a blocky solid with one
  through-hole and walls of 2 mm or more. Volume 24.4 cm³.
- Neither file is watertight as supplied, because of 62 and 32 zero-area triangles respectively.
  Without those triangles each is one watertight solid with the same volume. PreForm imported
  both under its strict `repair_behavior=ERROR` setting, so no repair was applied.

**Live result, Form 4 / Grey V5 / 0.1 mm:** 2 builds is the minimum, because A + B + one hat does
not fit on one platform.

| Build | `.form` | Parts | Time | Resin | Layers | PreForm warnings |
|---|---|---|---|---|---|---|
| 1 | `hackathon_job1_form4-grey-v5.form` | crumb A (mild, 22 °C), crumb B (sour, 27 °C) | 1.36 h | 150.3 mL | 185 | cups: A 1, B 2 |
| 2 | `hackathon_job2_form4-grey-v5.form` | 2× hat, 2× Clawd (PreForm auto-orient, tilted) | 2.30 h | 165.1 mL | 772 | cups: 1 per hat; 0.06% undersupported per hat |

`data/stl/print/PRINT_SHEET.md` is the one-page operator sheet: the file to open, parts,
estimates, warnings, the PreForm desktop click path, and wash/cure notes.
`hackathon_jobs.json` summarises the builds. Each build has its own manifest. On the
auto-oriented parts (hat, Clawd), support touchpoints land on visible surfaces.

**PreForm desktop and the headless server together:** with the PreForm Insider desktop app open,
the headless PreFormServer started and ran normally on port 44390, then shut down. The desktop
app kept running. The desktop app serves the API only after **File › Start PreForm Server**,
which uses port 44388 by default. Use a different `--port` for the headless server, or attach
to the desktop app on 44388: `prepare` attaches to any server already answering on `--port`, and
the scenes then appear in the desktop window. To do it all by hand in the desktop app, open each
`.form` (File › Open), which already has orientation, supports and layout in place.

## 11. Mock vs live in tests

- `tests/test_formlabs_mock.py`: **mock.** The real SDK talks to `MockPreFormServer`, which
  replays `src/fermentai/formlabs/fixtures/preform_3.64.2_session.json` (a recorded live session).
  No binary is needed.
- `tests/test_formlabs_manifest.py`: manifest provenance rules, plus a check that the committed
  **live** manifest matches the files on disk (sha256).
- `tests/test_formlabs_submit.py`: the print gate refuses without both confirmations; CLI help.
- `tests/test_formlabs_parts.py`: job files, STL inspection, and the six-part job through the
  **mock** (replays `fixtures/preform_3.64.2_job_hackathon.json`, recorded live).
- `tests/test_formlabs_live.py`: **live**, opt-in (`FERMENTAI_FORMLABS_LIVE=1`). It starts
  PreFormServer and prepares chamber B for real.
