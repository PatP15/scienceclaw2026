# AGENTS.md — conventions for everyone (human or agent) working in this repo

Read `PLAN.md` (the full spec) and `README.md` first. `docs/context-revised-hackathon-plan.md` is extra background.

## Layout
Python 3.12 monorepo managed with `uv`. Package lives in `src/fermentai/`; the repo root is the
`fermentai/` root described in PLAN.md §14. Run everything via `uv run ...` from the repo root.

- `uv sync` — create/refresh the venv (`.venv/`) and `uv.lock`.
- `uv run pytest tests/` — all tests. Tests for an area go in `tests/test_<area>_*.py`.
- `uv run python scripts/<name>.py` — CLI entry points.

## Ownership (parallel work)
Work is split by directory. Only edit files inside the paths you were assigned. If you need
something from another area that doesn't exist yet, write a small, clearly-marked stub/interface
*inside your own area* and note the dependency in your report; do not edit the other area.
Shared files (`pyproject.toml`, `README.md`, `AGENTS.md`) are edited only by the orchestrator —
if you need a new dependency, say so in your report (you may add it locally with `uv add` to test,
and the orchestrator will reconcile).

## Non-negotiables (from PLAN.md §18)
- Never fabricate ALCHEMI outputs, physical measurements, sensory observations or benchmark wins.
- Every data artifact and every dashboard value carries a provenance label:
  `real` | `simulated` | `replay` | `estimated` | `imported_human`.
- No secrets in the repo. `.env` is gitignored; `.env.example` documents variables.
- Nothing the LLM or judge text produces can bypass thermal/electrical safety checks.
- Dashboard assets are vendored locally — no CDN, no cloud fonts.
- Do not commit; the orchestrator commits after verifying.

## Config
YAML in `configs/`. Parameters carry units, a literature reference (or "assumption"), and an
uncertainty range where meaningful.
