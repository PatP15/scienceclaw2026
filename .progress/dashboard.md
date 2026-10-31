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

## Open items for other areas
- API: /molecular/summary -> HTTP 500 (NaN in analysis_summary.json; jsonable() the summaries).
- API/agents: build_context passes `recent_telemetry` (reduced keys, no received_at_utc/v1.1 fields) but the
  pipeline reads `context["telemetry"]` -> live cycles always report "no telemetry" observations.
- API: no route for data/benchmarks/ (dashboard uses a synced snapshot) or data/alchemi/environment/report.md
  (GPU cost hard-coded with source note in molecular.js RUN_REPORT).

## Resume
`uv run python -m fermentai.dashboard.vendor` (re-copy plotly + snapshots), `--check`;
`uv run pytest tests/test_dashboard_*.py`.
Run: `FERMENTAI_HOST=127.0.0.1 FERMENTAI_PORT=8765 FERMENTAI_DB_PATH=/tmp/x.sqlite3 FERMENTAI_API_TOKEN=t
scripts/start_demo.sh --with-simulated-devices --sim-images`, open http://127.0.0.1:8765/?token=t
