# Checkpoint: agents / decision engine (src/fermentai/agents, data/demos, tests/test_agents_*.py)

## Status: COMPLETE (pending orchestrator review/commit)
- [x] schedules.py — 17 validated candidates, validate_schedule(), heuristic + simulator predictions (cached)
- [x] sensory.py — rule-based free-text parser with evidence spans
- [x] communication.py — Finding schema, append-only JSONL FindingsLog, challenge()
- [x] offline_policy.py — targets, anchoring on judged loaves, ranking, lead/peer decisions, lead reply
- [x] scientist.py — ScientistAgent (separate state), assess_overproofing (§5), starter_readiness
- [x] llm_adapter.py — offline | claude_cli | anthropic_api (models.list discovery), mock-tested
- [x] pipeline.py — run_taste_feedback_cycle(feedback_text, context, mode) public entry point
- [x] demos.py + data/demos/demo_0{1,2,3}_*.json (label "Replay"); regenerate: uv run python -m fermentai.agents.demos
- [x] tests: uv run pytest tests/test_agents_*.py  -> 94 passed; full suite 225 passed
- [x] real claude -p smoke test: live:claude_cli OK (~27 s, 2 calls, claude-sonnet-5-5)

## Notes for whoever resumes
- Simulator: TemperatureSchedule(breakpoints, mode="piecewise_linear"); adapter in schedules._simulator_prediction.
  If the simulator changes, regenerate demos (outputs depend on predictions).
- Pipeline writes to data/experiments/findings_log.jsonl by default; tests/benchmarks pass persist_findings=False.
- Benchmark hook: context communicate=False gives the no-communication baseline; offline_policy.build_evidence /
  rank_candidates are pure functions for many-cycle runs.
