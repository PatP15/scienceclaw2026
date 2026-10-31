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

## Round 2 (PLAN_MASTER §9.1-9.4, §14) — COMPLETE, not committed
- [x] observation.py: normalize_observation / normalize_history / summarize_history / compare_to_prediction (obs-1.0)
- [x] quality-aware agents: ScientistAgent.observe() uses the adapter; overproof rule uses only quality-ok heights;
      stale/unverifiable latest -> status stale_data, no intervention; pH statement + ±0.05 widening when no valid pH
- [x] §9.4 Finding types + recipients/experiment_id/evidence_refs/claim/uncertainty/proposed_action (additive)
- [x] observation_summary / anomaly / sensory_feedback / experiment_proposal messages emitted
- [x] state.py get_agent_state("A"|"B"|"scientist_A"|"scientist_B")
- [x] §14 example -> structured_preference ["More sourness","Lower density","Preserve good rise (airy/fluffy crumb)"]; demo_04
- [x] reasoning_mode_label ("Online Agent" | "Offline Policy"); mode_label unchanged
- [x] tests: tests/test_agents_*.py -> 131 passed; full suite 360 passed, 1 skipped
- Intentional test changes: pipeline telemetry fixture now fresh + correct chamber ids (old one was 6-10 h stale and
  chamber "X", now correctly rejected); judge interpretation is a sensory_feedback message (was observation);
  FINDING_TYPES test checks superset.

## Round 3 — Sous Chef chat front door — COMPLETE, not committed
- [x] src/fermentai/agents/chat.py: SousChefChat, chat_turn(session_id, text, context=None, mode="offline"), chat_reset
- [x] deterministic state machine greeting->collecting->confirming->handoff->done (max 2 clarifying questions)
- [x] llm_adapter.LLMAdapter.chat(): wording only (strict schema reply + extracted_* fields); no handoff field
- [x] hand-off runs run_taste_feedback_cycle and logs sensory_feedback tagged "via chat"
- [x] sessions: memory + append-only data/experiments/chat_sessions.jsonl (context chat_sessions_path / FERMENTAI_CHAT_SESSIONS)
- [x] tests/test_agents_chat.py (12) ; agent tests 143 passed ; full suite 533 passed, 1 skipped, 1 xfailed

## Round 4 — chat is per lab — COMPLETE, not committed
- context["sous_chef"] = station (A|B); flow: greet+this loaf -> <=3 follow-ups (sourness, crumb, aroma/crust/else; skip answered)
  -> "Did you also taste Station X's loaf? Want to compare?" -> (other loaf + "which did you prefer, and why?") -> wish
  -> read-back/confirm -> hand-off. "this one"/"the other one"/"Station X" mapped to loaf letters.
- understood gains station, compared_other, preferred_loaf (+ preference_basis, preference_reason)
- sensory: "still"/"same" preserve markers are clause-local in desire clauses; new "Keep the current sourness" preference
- tests/test_agents_chat.py rewritten for the per-station flow (15 tests); agent tests 146 passed
