# Dashboard progress checkpoint

Owner scope: src/fermentai/dashboard/ (static/, vendor.py), tests/test_dashboard_*.py, this file.
Do NOT edit api/agents/fermentation/geometry/molecular.

## Status: COMPLETE (v1). `uv run pytest tests/test_dashboard_*.py` -> 36 passed (95 with test_api_*)
- [x] Vendored plotly.min.js (plotly.py 7.1.0 -> plotly.js 4.1.1) into static/vendor/; `vendor.py` re-copies/checks
- [x] index.html, css/dashboard.css, js/{app,api,util,messages,live,twin,molecular,taste,examples}.js
- [x] tests/test_dashboard_offline.py (URL scan, no fonts/CDN, plotly matches installed pkg, no map/geo traces)
- [x] tests/test_dashboard_serve.py (GET /, crawl every asset + ES import -> 200 w/ JS MIME, JS API paths vs OpenAPI)
- [x] Live run on :8765 with scripts/simulate_device.py; headless Chrome via CDP: all 4 tabs, taste cycle,
      motor start/cancel, 3D explorer iframe (SwiftShader WebGL), SSE kill -9 / restart resume, 390/820/1180 widths.
      Every run: 0 external requests, 0 console errors. Screenshot: data/demos/dashboard_screenshot.png

## Design notes
- Served by FastAPI StaticFiles at "/" (html=True). Plain ES modules, no build. Plotly = classic deferred script.
- Token: ?token= read once -> localStorage "fermentai.token" -> stripped from URL. "Read-only"/"Write access"
  pill opens a token dialog.
- SSE: own reconnect (exp. backoff 1..30 s with jitter), resumes with ?since=<last id>, de-dupes ids.
- Colours: chamber A #3987e5, B #d95926; acetic #199e70, lactic #9085e9.
- Pipeline result rendered defensively: parse_feedback shape, comparison_with_recorded, candidate_ranking,
  hypothesis object, next_experiment.schedules{points,predicted}, current_run_actions, safety_note.
- Agent messages are Finding dicts (agent_id); API stores sender=NULL for them, so live.js reads
  /agents/all/messages and filters client-side.

## Open items / requests to other areas (see final report)
- API: map Finding `agent_id` -> sender in feedback._store_messages; expose data/demos via API; serve
  data/alchemi/results/xyz if a native (non-iframe) 3D view is wanted.

## Resume
`uv run pytest tests/test_dashboard_*.py`
Run: `FERMENTAI_DB_PATH=/tmp/x.sqlite3 FERMENTAI_API_TOKEN=t uv run uvicorn fermentai.api.app:app --port 8765`
then `FERMENTAI_API_TOKEN=t uv run python scripts/simulate_device.py --base-url http://127.0.0.1:8765 --images`
and open http://127.0.0.1:8765/?token=t
