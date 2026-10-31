# Checkpoint: experimental design / benchmark (src/fermentai/design, scripts/benchmark_agents.py, data/benchmarks, docs/benchmark_results.md, tests/test_design_*.py)

## Status: COMPLETE (pending orchestrator review/commit)
- [x] world.py (hidden worlds, fast forward model, CRN noise, held-out set, redundancy)
- [x] fitting.py (multi-experiment MAP + Laplace; adapter around calibrate() conventions; robust posterior)
- [x] agent_adapter.py (offline_policy evidence on calibrated params; exact match at defaults, tested)
- [x] optimizer.py (EIG, batch-conditional EIG, scale-matched lambda = 0.863)
- [x] benchmark.py (4 arms: agent|info x independent|collaborative; metrics; summarize)
- [x] scripts/benchmark_agents.py (parallel run, CSV/JSON/PNG)
- [x] full run: 30 well-specified + 30 misspecified worlds, 297 s on 10 workers -> data/benchmarks/
- [x] docs/benchmark_results.md (setup, definitions, results, interpretation, limitations)
- [x] tests: uv run pytest tests/test_design_*.py -> 17 passed (~15 s); ruff clean

## Headline (well-specified, n=30, collaborative - independent after 8 experiments)
- Communication changes choices in ~3.9/4 rounds; within-round duplicates 3.1-3.5 -> 0.
- Deployed policy: pooled info +0.68 nats [0.39, 0.96]; regret no difference; time-to-objective -0.4 rounds.
- Info-aware selector: pooled info -0.13 nats [-0.18, -0.08] (independent slightly better by the end).
- Agent-level gains (+1.5-2.3 nats, lower held-out RMSE) are mostly data sharing.

## Resume / next ideas
uv run python scripts/benchmark_agents.py ; uv run pytest tests/test_design_*.py
- Possible follow-ups: history-aware exclusion (rank_candidates exclude_ids), lambda/budget/noise sweeps,
  heterogeneous agent priors, LLM-mode arm.

## Notes
- No edits outside owned paths. agents/ hook replaced by design/agent_adapter.py (replicates
  schedules._simulator_prediction with params; test guards drift).
- calibrate() handles one schedule only -> design/fitting.py does the joint multi-schedule fit.
- Full-suite failures seen during this work (not in design/): test_api_contract examples sync,
  test_api_telemetry quality flag, test_agents_pipeline append-only log — other owners' in-progress edits.
