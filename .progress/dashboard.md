# Dashboard progress checkpoint

Owner scope: src/fermentai/dashboard/ (static/, vendor.py), tests/test_dashboard_*.py, this file.
Do NOT edit api/agents/fermentation/geometry/molecular.

## Status: v2 (PLAN_MASTER §11/§14, contract v1.1) COMPLETE, not committed
`uv run pytest tests/test_dashboard_*.py tests/test_api_*.py` -> 141 passed, 1 skipped.
Verified live against `scripts/start_demo.sh --with-simulated-devices --sim-images` (port 8765) with
headless Chrome over CDP: every view, demo_04 live cycle (motors issued->accepted->completed·ran on A and B),
recorded replay, molecular viewers. 0 external requests. Screenshots: data/demos/dashboard_{screenshot,live,
twin,molecular,taste}.png

## v2 changes
- Header: LIVE / REPLAY / SIMULATION indicator derived from each chamber's telemetry `source`
  (measured->LIVE "real · physical", simulated->SIMULATION, replay->REPLAY) + colour bar; reasoning pill
  "Online Agent" / "Offline Policy" from `reasoning_mode_label` (mode_label in tooltip); SSE pill "Connected".
- Live cards: device_id, boot, seq, uptime, fw; "device clock unsynced" (timestamp_utc null -> receipt time);
  per-value field_provenance mini badges; pressure, Peltier output fraction, motor state, rise estimate,
  last valid update; heatsinks in an "Equipment safety — not fermentation evidence" strip; diagnostics
  (RSSI, heap, sensor_health badges, flags); missing -> "unavailable". Agent box from GET /agents/{id}/state.
  Pending commands from state.pending_commands. Motor track issued -> accepted -> completed · ran (acks/`ran`).
- Taste: Replay dropdown from GET /api/v1/demos (examples.js deleted); "Show recorded replay" renders the
  stored demo result without API writes; "Human preference:" block (structured_preference); observations[c]
  (quality counts, down-weighted/missing, pH validity, deviations, anomalies).
- Molecular: provenance (GPU, toolkit, checkpoint, 159 opts, 72 MD, cross-check, cost from report.md excerpt),
  caveat, association chart+table, deprotonation table, MD H-bonds vs T chart (md_summary.csv), coupling report
  (data/predictions/molecular_coupling_report.json), 4 iframe viewers, H-bond table.
  Falls back to /molecular/files/analysis_summary.json (NaN-tolerant parse) because /molecular/summary 500s.
- Twin: baseline vs simulation-derived crumb specimens + table (chain_summary.json), evidence-chain strip,
  IN-SILICO benchmark panel from static/snapshots/benchmarks/ (synced by vendor.py; no API route).

## v3: display vocabulary (display mapping only; stored/API values untouched)
util.js normProv(p, ctx) / PROV_LABEL / provBadge / deviceBadge:
measured (green) | computed (teal: "real: ALCHEMI…", aimnet, preform_local_api) | modeled (purple: "simulated"
model outputs) | simulated device (amber: telemetry/image source "simulated", ctx "device") | replay (grey) |
estimated (olive: policy, fallback, assumption, alchemi-informed) | manual (human) (pink).
Mode indicator LIVE/REPLAY/SIMULATION unchanged (colours green/grey/amber). Tests: tests/test_dashboard_provenance.py
(runs util.js in Node >= 22), test_every_named_import_is_exported (static import/export check).

## v4: kitchen-brigade display names (display only; ids unchanged, raw id in tooltips)
util.js chefName/chefLabel/brigadeText; taste.js cookLabel (exported for tests).
scientist_A/B -> Sous Chef A/B; orchestrator steps -> Head Chef (reads notes / sets the goal / calls the plan /
dispatches); candidate_ranking -> "Line cooks · candidate simulations" (chosen = "plated · Station X");
judge -> Guest; chamber subtitle "Station A/B"; motor button subtitle "Hat bobs · new assignment"; legend line.
Taste flow is now 12 steps (added goal + cooks). Tests: test_kitchen_brigade_display_names, test_brigade_legend_*.

## v5: visual polish pass (CSS polish layer at end of dashboard.css + small JS structure changes)
Theme #15171a/#1e2126, cream #f3efe6, crust #b86b3a (large headings/dividers only), accent #d9895a; 4-step type
scale (12/14/18/36 px) via --fs-*; one gutter --gap 12px; inline SVG brigade icons (util.icon); emptyCard();
Live: one hero number + secondary row, provenance dots (badge only when field != record source), compact safety
line, device drawer (identity+diagnostics), no-device empty state; Taste: conversation avatars, A left / B right,
reasoning details folded, "plated" kitchen ticket. Before/after: data/demos/{before,after}_*.png,
data/demos/polish_contact_sheet.png.

## v6: chat panel + USB link chip
js/chat.js (ChatPanel, understoodChips): "Talk to Sous Chef A/B" above the form; POST/GET/DELETE /api/v1/chat,
SSE `chat`; session in localStorage fermentai.chat.session; hand-off (stage handoff/done) plays the 12-step reveal
from `persisted` (taste-feedback response shape) or `handoff_result`; Head Chef summary bubble; degrades to
"Chat not available yet" (route detected via /openapi.json; 404/503 at runtime). Mode toggle shared (event
fermentai:taste-mode). live.js: GET /api/v1/bridge/status every 5 s -> "USB · …" chip; real linked device ->
LIVE/MEASURED only if the latest telemetry is from that device_id AND not source=simulated (else warning chip).
Test harness for the not-yet-wired chat routes: scratchpad chat_harness.py (not in repo).
Contract test allows /api/v1/chat* as optional until the API ships the routes (remove from `optional` then).

## v7: desktop target (Mac screen, 1440x900 .. 1920x1080)
Live tab = Station A | Station B | "Kitchen pass" column (latest Sous Chef conversation + experiment plan, sticky)
at >= 1280 px; bigger hero (--fs-display 3rem); container queries keep station cards tidy; camera frame uncropped.
Loopback + server default_token_in_use -> auto write access with the documented dev token (pill "Write access
(this Mac)"), dropped on 401/403, opt-out via "Forget token". iPad/touch wording removed. Mode toggle labels
Online Agent / Offline Policy. Screenshots are 1440x900 @2x.

## v8: lab grid + lab detail (#lab/<id>) + Kitchen pass; terse
Tabs: Live Laboratories · Kitchen pass (tab id still "taste") · Digital Twin · Molecular.
live.js rewritten around SENSORS catalogue: a sensor is shown only if the latest telemetry from the current device
has a value (big number + 60-min line); door = icon + open timeline; hat icon wiggles while thinking (chat/cycle
pending, SSE chat sous/head chef, motor issued/accepted) and bobs while motor_running. One provenance badge per card.
Detail: full-width Plotly charts per reporting sensor, camera frame only if from this device (stale_image_from_other_device
or device_id/source-kind mismatch -> "No frame from this device yet"), collapsed device drawer ("not fitted: …",
safety strip only if a heatsink reports), agent box with "why", per-station ChatPanel ({station}) whose hand-off plays
the 12-step reveal inside #lab-reveal (TastePanel.placeSteps). Kitchen pass: plan-history tickets, latest A↔B,
replays, fallback form collapsed. Real stack verified on :8765 with FERMENTAI_FINDINGS_LOG/FERMENTAI_CHAT_SESSIONS
pointed at scratch files (keeps data/experiments clean).

## v9: Molecular Explorer as six answer cards (js/molecular.js, css/molecular.css)
Header strip "COMPUTED · NVIDIA ALCHEMI + AIMNet2 · RTX A6000 · N optimisations · M MD runs" (campaign 1 counts from
analysis_summary.json + campaign-2 counts parsed from /molecular/environment report_md). Cards (question, inline-SVG
picture, one number, one line; everything else in "Details"): 1 acid strength (pKa axis, measured dots, GPU lactic pKa
anchored on measured acetic, by n from c2 thermo_cycles dd_boltzmann / RT ln10), 2 trust tiles (acetic dimer vs
literature range; ΔpKa sign/magnitude), 3 water (c2_association_by_n.csv mean ΔF/water over n + c2_md_long_summary.csv
H-bonds at T nearest 22 °C), 4 Gln-Gln proxy, 5 coupling report (pKa gap, lactic potency, max |ΔpH|), 6 lazy 3D viewer.
Card CSS lives in css/molecular.css (separate file: dashboard.css is edited concurrently). Old tables moved to Details.
Tests: tests/test_dashboard_molecular.py (no hard-coded result values, lazy viewer, stylesheet linked).
Screenshots: data/demos/molecular_before.png / molecular_after.png (headless Chrome 1440x900, --force-prefers-reduced-motion).

## Open items for other areas
- API: /molecular/summary -> HTTP 500 (NaN in analysis_summary.json; jsonable() the summaries).
- API/agents: build_context passes `recent_telemetry` (reduced keys, no received_at_utc/v1.1 fields) but the
  pipeline reads `context["telemetry"]` -> live cycles always report "no telemetry" observations.
- API: no route for data/benchmarks/ (dashboard uses a synced snapshot). (report.md now via /molecular/environment.)

## Resume
`uv run python -m fermentai.dashboard.vendor` (re-copy plotly + snapshots), `--check`;
`uv run pytest tests/test_dashboard_*.py`.
Run: `FERMENTAI_HOST=127.0.0.1 FERMENTAI_PORT=8765 FERMENTAI_DB_PATH=/tmp/x.sqlite3 FERMENTAI_API_TOKEN=t
scripts/start_demo.sh --with-simulated-devices --sim-images`, open http://127.0.0.1:8765/?token=t
