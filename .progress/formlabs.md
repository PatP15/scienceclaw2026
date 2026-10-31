# Formlabs PreForm API integration — checkpoint

## Done
- Research: PreForm API v1.0.0-alpha (https://developer.formlabs.com/api-reference/, OpenAPI at
  /reference/openapi-v1.yml). Official SDK `formlabs-sdk` (GitHub only, NOT on PyPI):
  `formlabs-sdk @ git+https://github.com/Formlabs/formlabs-api-python@v1.0.0-alpha.post2`
  -> installs formlabs-sdk 1.0.0a0.post2 + formlabs-preform-api 1.0.0a0 (wheel from downloads.formlabs.com).
  Added with `uv add` (pyproject.toml dependency + [tool.uv.sources] git pin) — orchestrator to reconcile.
- PreFormServer 3.64.2 (mac-arm64) is a PUBLIC direct download, no login/EULA click:
  https://downloads.formlabs.com/PreFormServer/Release/3.64.2/PreForm_Server_mac-arm64_3.64.2_release_releaser_665_161434.zip
  zip sha256 6ca339e7b0f7db9c6823e0b54a10aa6dcb54b067ce8dc07b5de5b9b428c098e6; unzipped to
  ~/.cache/fermentai/preform-server/3.64.2/PreFormServer/PreFormServer.app (codesign: Developer ID
  Application: Formlabs Inc. (KVPE3R79SR), notarized).
- src/fermentai/formlabs/: client.py (locate/start/stop server, health, open_preform live|mock|auto),
  replay.py (RecordingProxy = wire transcript of live calls + print guard; MockPreFormServer = HTTP replay),
  prepare.py (pipeline, profiles, submit_print human gate), manifest.py, cli.py,
  fixtures/preform_3.64.2_session.json (recorded live session = mock fixture).
- scripts/prepare_print.py (status | prepare | submit).
- LIVE runs succeeded (source "preform_local_api"): data/stl/print/ (default, open face up) and
  data/stl/print/alternative_preform_auto/ (PreForm auto-orient, for comparison).
  Form 4 / Grey V5 / 0.1 mm: 1.36 h, 150.24 mL (models 127.05, raft 21.14, supports 2.04), 185 layers.
  .form re-open round trip OK. PreForm flags cups A=1, B=3 (reported as warnings).

## Also done (final)
- geometry/formlabs_preform.py is now a thin re-export of fermentai.formlabs.
- Tests: tests/test_formlabs_{mock,manifest,submit}.py = 25 passed / 1 skipped (live is opt-in);
  FERMENTAI_FORMLABS_LIVE=1 tests/test_formlabs_live.py passed against the real server.
- docs/formlabs_workflow.md written (endpoints, choice rationale, orientation comparison, post-processing,
  food-contact caveat, pitch-safe wording).
- Every JSON under data/stl/print/ carries top-level "provenance" (transcripts "real: ...", manifests
  "estimated"); tests/integration/test_live_stack.py::test_data_provenance_audit passes.

## Next (optional)
- A human opens data/stl/print/crumb_chambers_AB_form4-grey-v5.form in PreForm, reviews the 1+3 flagged
  cups, chooses open-face-up vs alternative_preform_auto, and prints from PreForm (or `submit` with both gates).
- Orchestrator: decide whether formlabs-sdk stays a hard dependency (git source) or moves to an optional
  extra; data/stl/print/ is ~15 MB (mostly .form files).

## How to resume
- `uv run python scripts/prepare_print.py status`
- `uv run python scripts/prepare_print.py prepare --backend live --update-fixture` (regenerates outputs + fixture)
- `uv run python scripts/prepare_print.py prepare --backend mock --out /tmp/x` (no binary needed)
- `uv run pytest tests/test_formlabs_*.py`
