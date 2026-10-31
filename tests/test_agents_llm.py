"""Live-mode LLM adapter: mocked backends only (no paid API calls, no real CLI calls)."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from fermentai.agents import llm_adapter as L
from fermentai.agents.llm_adapter import LLMAdapter
from fermentai.agents.pipeline import run_taste_feedback_cycle

PLAN_EXAMPLE = ("Loaf A was mild and fluffy. Loaf B was more sour but a little dense. "
                "I'd like a stronger sour flavor without losing the airy texture.")
CTX = {"persist_findings": False, "now_utc": "2026-10-31T18:00:00Z"}

LEAD_OUT = {
    "observation_summary": "LLM: A mild/airy, B sour/dense.",
    "hypothesis_statement": "LLM hypothesis: a hot burst then cool hold adds acid without overproofing.",
    "mechanism": "LLM mechanism: LAB favored at 30-32 C; cool tail slows yeast.",
    "own_schedule_id": "warm_then_cool_32_18",
    "peer_suggestion_schedule_id": "cool_retard_24_16",
    "peer_suggestion_reason": "Test the acetic route in parallel.",
    "prediction": "LLM prediction (estimated).",
    "uncertainty": "LLM uncertainty: uncalibrated model.",
    "proposed_measurement": "LLM: starter height every 10 min.",
    "rationale": "LLM rationale.",
}
CRITIC_OUT = {
    "critique": "LLM critique: the 32 C phase risks overproofing; the cool tail must start on time.",
    "agrees_with_hypothesis": False,
    "concerns": ["overproof risk", "single judge"],
    "alternative_hypothesis": "LLM alternative: acetic share drives perceived sourness.",
    "own_schedule_id": "cool_retard_24_16",
    "accepted_peer_suggestion": True,
    "decision_change_note": "LLM: I adopted the suggestion.",
    "prediction": "LLM critic prediction.",
    "uncertainty": "LLM critic uncertainty.",
    "proposed_measurement": "LLM critic measurement.",
    "rationale": "LLM critic rationale.",
}


class FakeModels:
    def __init__(self, models=None, fail=False):
        self._models = models if models is not None else [
            SimpleNamespace(id="claude-haiku-9", created_at="2026-09-01T00:00:00Z"),
            SimpleNamespace(id="claude-sonnet-old", created_at="2025-01-01T00:00:00Z"),
            SimpleNamespace(id="claude-sonnet-new", created_at="2026-08-01T00:00:00Z"),
            SimpleNamespace(id="claude-opus-newest", created_at="2026-10-01T00:00:00Z"),
        ]
        self.fail = fail

    def list(self, **kw):
        if self.fail:
            raise RuntimeError("listing down")
        return iter(self._models)


class FakeMessages:
    def __init__(self, lead=LEAD_OUT, critic=CRITIC_OUT, raw=None):
        self.lead, self.critic, self.raw = lead, critic, raw
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        schema = kw["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        is_critic = "critique" in schema["properties"]
        text = self.raw if self.raw is not None else json.dumps(self.critic if is_critic else self.lead)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class FakeClient:
    def __init__(self, **kw):
        self.models = FakeModels(**{k: v for k, v in kw.items() if k in ("models", "fail")})
        self.messages = FakeMessages(**{k: v for k, v in kw.items() if k in ("lead", "critic", "raw")})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FERMENTAI_LLM_MODE", "ANTHROPIC_API_KEY", "FERMENTAI_ANTHROPIC_MODEL",
              "FERMENTAI_ANTHROPIC_MODEL_FALLBACK", "FERMENTAI_CLAUDE_CLI"):
        monkeypatch.delenv(k, raising=False)
    L._MODEL_CACHE.clear()


def test_live_without_configuration_falls_back():
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX), mode="live")
    assert r["mode_label"].startswith("offline:deterministic_fallback (live unavailable: ")
    assert "FERMENTAI_LLM_MODE=offline" in r["mode_label"]
    assert r["provenance"]["decision_source"] == "offline_policy"
    # identical decisions to plain offline mode
    off = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX))
    assert r["recommended_schedules"] == off["recommended_schedules"]


def test_live_api_without_key_falls_back(monkeypatch):
    monkeypatch.setenv("FERMENTAI_LLM_MODE", "anthropic_api")
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX), mode="live")
    assert r["mode_label"] == "offline:deterministic_fallback (live unavailable: ANTHROPIC_API_KEY not set)"


def test_live_cli_missing_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("FERMENTAI_LLM_MODE", "claude_cli")
    monkeypatch.setenv("FERMENTAI_CLAUDE_CLI", str(tmp_path / "no-such-claude"))
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX), mode="live")
    assert "live unavailable: claude CLI not found" in r["mode_label"]


def test_unknown_mode_falls_back():
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX), mode="turbo")
    assert r["mode_label"].startswith("offline:deterministic_fallback (live unavailable: unknown mode")


def test_live_api_success_with_model_discovery():
    client = FakeClient()
    adapter = LLMAdapter("anthropic_api", client=client)
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert r["mode_label"] == "live:anthropic_api"
    assert r["provenance"]["decision_source"] == "llm"
    assert r["provenance"]["llm"]["model"] == "claude-sonnet-new"  # newest Sonnet, not newest overall
    assert "models.list" in r["provenance"]["llm"]["model_selection"]
    calls = client.messages.calls
    assert len(calls) == 2 and all(c["model"] == "claude-sonnet-new" for c in calls)
    # the two agents get different system prompts and the critic sees the proposer's message
    assert calls[0]["system"] != calls[1]["system"]
    assert "peer_message" in calls[1]["messages"][0]["content"]
    assert "peer_message" not in calls[0]["messages"][0]["content"]
    lead_id = r["hypothesis"]["proposed_by"][-1]
    peer_id = "A" if lead_id == "B" else "B"
    assert r["next_experiment"]["schedules"][peer_id]["schedule_id"] == "cool_retard_24_16"
    msgs = r["agent_messages"]
    assert "LLM hypothesis" in msgs[0]["content"] and "LLM critique" in msgs[1]["content"]
    assert msgs[1]["in_reply_to"] == msgs[0]["id"]
    # schedule ids are constrained to the validated library
    enum = calls[0]["output_config"]["format"]["schema"]["properties"]["own_schedule_id"]["enum"]
    assert "warm_then_cool_32_18" in enum and len(enum) >= 12


def test_model_listing_failure_uses_configurable_fallback(monkeypatch):
    monkeypatch.setenv("FERMENTAI_ANTHROPIC_MODEL_FALLBACK", "my-fallback-model")
    adapter = LLMAdapter("anthropic_api", client=FakeClient(fail=True))
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert r["mode_label"] == "live:anthropic_api"
    assert r["provenance"]["llm"]["model"] == "my-fallback-model"
    assert r["provenance"]["llm"]["model_selection"].startswith("fallback")


def test_pinned_model(monkeypatch):
    monkeypatch.setenv("FERMENTAI_ANTHROPIC_MODEL", "pinned-x")
    adapter = LLMAdapter("anthropic_api", client=FakeClient())
    assert adapter.resolve_api_model() == "pinned-x"


@pytest.mark.parametrize("kw,needle", [
    ({"raw": "this is not json"}, "LLMError"),
    ({"lead": dict(LEAD_OUT, own_schedule_id="hold_40C_forever")}, "unknown schedule id"),
    ({"lead": {k: v for k, v in LEAD_OUT.items() if k != "rationale"}}, "schema validation"),
])
def test_invalid_llm_output_falls_back(kw, needle):
    adapter = LLMAdapter("anthropic_api", client=FakeClient(**kw))
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert r["mode_label"].startswith("offline:deterministic_fallback (live unavailable: ")
    assert needle in r["mode_label"]
    assert r["provenance"]["decision_source"] == "offline_policy"
    # nothing from the failed live attempt leaks into the findings
    assert not any("LLM" in f["content"] for f in r["findings_logged"])


def test_live_cli_success_and_env_isolation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-cli")
    seen = []

    def fake_run(cmd, input, capture_output, text, timeout, cwd, env):
        seen.append({"cmd": cmd, "env": env, "input": input})
        schema = json.loads(cmd[cmd.index("--json-schema") + 1])
        out = CRITIC_OUT if "critique" in schema["properties"] else LEAD_OUT
        payload = {"type": "result", "subtype": "success", "is_error": False, "result": "",
                   "structured_output": out, "modelUsage": {"claude-sonnet-x": {}}, "duration_ms": 1234}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    adapter = LLMAdapter("claude_cli", runner=fake_run)
    monkeypatch.setattr(LLMAdapter, "cli_path", staticmethod(lambda: "/usr/bin/claude"))
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert r["mode_label"] == "live:claude_cli"
    assert len(seen) == 2
    for call in seen:
        assert call["cmd"][:2] == ["/usr/bin/claude", "-p"]
        assert "--output-format" in call["cmd"] and "json" in call["cmd"]
        assert "ANTHROPIC_API_KEY" not in call["env"]  # never mix subscription and API billing
    assert r["provenance"]["llm"]["model"] == "claude-sonnet-x"


def test_cli_error_result_falls_back(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"is_error": True, "result": "rate limited"}), stderr="")

    monkeypatch.setattr(LLMAdapter, "cli_path", staticmethod(lambda: "/usr/bin/claude"))
    adapter = LLMAdapter("claude_cli", runner=fake_run)
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert "live unavailable: LLMError: claude CLI reported an error: rate limited" in r["mode_label"]


def test_cli_timeout_falls_back(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(LLMAdapter, "cli_path", staticmethod(lambda: "/usr/bin/claude"))
    adapter = LLMAdapter("claude_cli", runner=fake_run, timeout_s=1)
    r = run_taste_feedback_cycle(PLAN_EXAMPLE, dict(CTX, llm_adapter=adapter), mode="live")
    assert "timed out" in r["mode_label"]


def test_schemas_are_strict():
    ids = ["a", "b"]
    for sch in (L.lead_schema(ids), L.critic_schema(ids)):
        assert sch["additionalProperties"] is False
        assert set(sch["required"]) == set(sch["properties"])
