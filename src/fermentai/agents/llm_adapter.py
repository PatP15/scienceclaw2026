"""Configurable LLM adapter for live mode (PLAN §9).

Backends (env ``FERMENTAI_LLM_MODE``):

* ``offline``        no LLM; the pipeline uses the deterministic fallback policy.
* ``claude_cli``     shells out to ``claude -p`` (Claude Code CLI, subscription-backed when
                     signed in). ``ANTHROPIC_API_KEY`` is removed from the child environment
                     so the subscription and API-key billing modes are never mixed.
                     Host-side only: this must never run on (or be called from) an ESP32.
* ``anthropic_api``  ``anthropic`` SDK with ``ANTHROPIC_API_KEY`` (billed separately). The
                     model id is discovered at runtime via ``client.models.list()``: the newest
                     Sonnet-class model wins. ``FERMENTAI_ANTHROPIC_MODEL`` pins a model;
                     ``FERMENTAI_ANTHROPIC_MODEL_FALLBACK`` is used only if listing fails.

Both backends return JSON constrained by a JSON schema (schedule ids are an ``enum`` of the
validated library) and are validated with pydantic. Any failure raises; the pipeline then
falls back to the offline policy and says so in ``mode_label``.

Other env vars: ``FERMENTAI_CLAUDE_CLI`` (path to the CLI, default ``claude`` on PATH),
``FERMENTAI_CLAUDE_CLI_MODEL`` (alias passed to ``--model``, default ``sonnet``),
``FERMENTAI_LLM_TIMEOUT_S`` (per call, default 120).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from .offline_policy import LeadDecision, PeerDecision

LLM_MODES = ("offline", "claude_cli", "anthropic_api")
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_API_MODEL_FALLBACK = "claude-sonnet-5-5"  # used ONLY if models.list() fails; override via env
DEFAULT_CLI_MODEL_ALIAS = "sonnet"  # CLI alias that resolves to the newest Sonnet; not a pinned version


class LLMUnavailable(RuntimeError):
    """Backend not configured / not installed / no credentials."""


class LLMError(RuntimeError):
    """Backend call failed or returned invalid output."""


# ---------------------------------------------------------------------------
# Prompts (kept in this module on purpose)
# ---------------------------------------------------------------------------
COMMON_RULES = """\
Rules you must follow:
- You may choose temperature schedules ONLY by id from `candidate_library`. You never emit raw
  temperatures and you never control power, Peltiers or setpoints directly: the local API and the
  ESP32 firmware validate and clamp every command, and a human confirms when to end/bake.
- Do not claim that any temperature guarantees a flavor. Distinguish pH, total acidity, the
  lactic/acetic balance and perceived sourness.
- Predictions in the context are model estimates (see `prediction_source`); label them as such and
  never invent measurements or sensory observations.
- Watch for overproofing: holding a warm setpoint for a long time can push a small dough past peak
  rise (denser crumb). Prefer ending at the rise plateau or stepping down.
- Your chamber data arrive as quality-annotated observations (`your_observations`). Use only fields
  with quality "ok" as evidence; treat low_confidence/uncalibrated values as weak hints and never use
  stale, invalid, missing, wrong_chamber or unsynced_time values. Never fill a missing physical
  observation with a simulated value.
- Sensor meanings: starter temperature is the controlled variable; chamber air temperature is the
  environment (thermal lag); heatsink temperatures are equipment safety only, never evidence about
  microbes; humidity is environment; BME680 gas resistance is an uncalibrated headspace response, NOT
  CO2; camera height is an expansion estimate, not CO2; door events are disturbances; Peltier mode and
  setpoint are control inputs; the motor is decorative.
- If `ph_status.valid` is false, say explicitly that no valid pH measurement exists for your chamber and
  widen your stated uncertainty accordingly.
- Write concise, concrete scientific prose: 1-3 sentences per text field.
"""

LEAD_SYSTEM = """\
You are {agent_id}, an independent AI food scientist that owns sourdough fermentation chamber
{chamber} in FermentAI. Scientist {peer} owns the other chamber; you keep separate state and
communicate only through structured messages in a shared findings log.
""" + COMMON_RULES

LEAD_TASK = """\
A judge just tasted the loaves. Using ONLY your own context below, propose the next experiment:
pick `own_schedule_id` for your chamber {chamber}, suggest `peer_suggestion_schedule_id` for
chamber {peer} (ideally testing a different mechanism so the two arms are not redundant), and
state a falsifiable hypothesis, a prediction, your uncertainty, the measurement you want, and your
rationale. Return only JSON matching the schema.

Your context (JSON):
{context}
"""

CRITIC_SYSTEM = """\
You are {agent_id}, an independent AI food scientist that owns sourdough fermentation chamber
{chamber} in FermentAI. You have just read a proposal from {peer_agent} (field `peer_message`).
You keep your own state and must reason from YOUR context, not simply agree.
""" + COMMON_RULES

CRITIC_TASK = """\
Critique the peer's proposal independently: name concrete risks or weaknesses, give an alternative
hypothesis, then decide `own_schedule_id` for YOUR chamber {chamber}. Accept the peer's suggestion
for your chamber only if your own reasoning supports it. In `decision_change_note`, say whether and
how the peer's message changed your decision. Return only JSON matching the schema.

Your context (JSON):
{context}
"""


CHAT_SYSTEM = """\
You are Sous Chef {chamber} ({agent_id}), a friendly kitchen scientist in the FermentAI brigade. You are
chatting with a Guest who just tasted two sourdough loaves (Loaf A and Loaf B). The Head Chef (the
planning pipeline) turns the tasting into the next experiment; line cooks are candidate simulations.
Rules:
- Write the next chat message in plain, warm, concise language (1-3 sentences, no jargon, no lists).
- You MUST carry out `next_action` exactly as described: ask the question in `question_draft` when one
  is given (keep its meaning, end with a question mark), or reflect back `reflection_draft` and ask the
  Guest to confirm, or acknowledge the hand-off. Do not ask about anything else.
- You do not decide when to hand off, and you never say an experiment has started: the operator confirms
  every plan and the hardware validates every command.
- Never invent tasting notes. Only fill the extracted_* fields with what the Guest actually said in the
  latest message (empty string if nothing).
Return only JSON matching the schema.
"""

CHAT_TASK = """\
Structured chat state (JSON):
{state}
"""


class ChatReply(BaseModel):
    reply: str
    extracted_loaf_a: str
    extracted_loaf_b: str
    extracted_wish: str


def chat_schema() -> dict[str, Any]:
    return _string_schema(ChatReply, [])


def _string_schema(model: type[BaseModel], ids: list[str]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        ann = field.annotation
        if ann is bool:
            props[name] = {"type": "boolean"}
        elif getattr(ann, "__origin__", None) is list:
            props[name] = {"type": "array", "items": {"type": "string"}}
        else:
            props[name] = {"type": "string"}
    for key in ("own_schedule_id", "peer_suggestion_schedule_id"):
        if key in props:
            props[key] = {"type": "string", "enum": list(ids)}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def lead_schema(ids: list[str]) -> dict[str, Any]:
    return _string_schema(LeadDecision, ids)


def critic_schema(ids: list[str]) -> dict[str, Any]:
    return _string_schema(PeerDecision, ids)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j < 0:
            raise LLMError("no JSON object in model output")
        text = text[i:j + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc


def configured_mode(override: str | None = None) -> str:
    mode = (override or os.environ.get("FERMENTAI_LLM_MODE") or "offline").strip().lower()
    return mode


_MODEL_CACHE: dict[str, tuple[str, str]] = {}


class LLMAdapter:
    """One adapter instance per cycle. ``client``/``runner`` are injectable for tests."""

    def __init__(self, mode: str | None = None, *, timeout_s: float | None = None, client: Any = None,
                 runner: Callable[..., Any] | None = None):
        self.mode = configured_mode(mode)
        self.timeout_s = float(timeout_s or os.environ.get("FERMENTAI_LLM_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        self._client = client
        self._injected_client = client is not None
        self._runner = runner or subprocess.run
        self.model: str | None = None
        self.model_selection: str | None = None
        self.calls: list[dict[str, Any]] = []

    # -- availability -----------------------------------------------------------------
    def check_available(self) -> tuple[bool, str]:
        if self.mode not in LLM_MODES:
            return False, f"unknown FERMENTAI_LLM_MODE={self.mode!r} (expected one of {', '.join(LLM_MODES)})"
        if self.mode == "offline":
            return False, "FERMENTAI_LLM_MODE=offline (set claude_cli or anthropic_api for live inference)"
        if self.mode == "anthropic_api":
            if self._client is None:
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    return False, "ANTHROPIC_API_KEY not set"
                try:
                    import anthropic  # noqa: F401
                except ImportError:
                    return False, "anthropic SDK not installed"
            return True, "ok"
        cli = self.cli_path()
        if cli is None and self._runner is subprocess.run:
            return False, "claude CLI not found on PATH (set FERMENTAI_CLAUDE_CLI)"
        return True, "ok"

    @staticmethod
    def cli_path() -> str | None:
        explicit = os.environ.get("FERMENTAI_CLAUDE_CLI")
        if explicit:
            return explicit if os.path.exists(explicit) or shutil.which(explicit) else None
        return shutil.which("claude")

    def describe(self) -> dict[str, Any]:
        return {"backend": self.mode, "model": self.model, "model_selection": self.model_selection,
                "calls": self.calls}

    # -- anthropic_api ----------------------------------------------------------------
    def _api_client(self):
        if self._client is not None:
            return self._client
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")
        import anthropic

        self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout_s, max_retries=1)
        return self._client

    def resolve_api_model(self) -> str:
        if self.model:
            return self.model
        pinned = os.environ.get("FERMENTAI_ANTHROPIC_MODEL")
        if pinned:
            self.model, self.model_selection = pinned, "pinned via FERMENTAI_ANTHROPIC_MODEL"
            return pinned
        cache_key = "api"
        if cache_key in _MODEL_CACHE and not self._injected_client:
            self.model, self.model_selection = _MODEL_CACHE[cache_key]
            return self.model
        client = self._api_client()
        try:
            models = list(client.models.list(limit=100))
            sonnets = [m for m in models if "sonnet" in str(getattr(m, "id", "")).lower()]
            pool = sonnets or models
            if not pool:
                raise LLMError("models.list() returned no models")
            best = max(pool, key=lambda m: (str(getattr(m, "created_at", "")), str(m.id)))
            sel = ("newest Sonnet-class model from models.list()" if sonnets
                   else "no Sonnet-class model listed; newest listed model")
            self.model, self.model_selection = str(best.id), sel
        except Exception as exc:  # noqa: BLE001 - listing failure -> configurable fallback
            fb = os.environ.get("FERMENTAI_ANTHROPIC_MODEL_FALLBACK", DEFAULT_API_MODEL_FALLBACK)
            self.model = fb
            self.model_selection = f"fallback FERMENTAI_ANTHROPIC_MODEL_FALLBACK (models.list failed: {type(exc).__name__})"
        if not self._injected_client and not self.model_selection.startswith("fallback"):
            _MODEL_CACHE[cache_key] = (self.model, self.model_selection)
        return self.model

    def _call_api(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        client = self._api_client()
        model = self.resolve_api_model()
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as exc:
            raise LLMError(f"Anthropic API call failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        stop = getattr(resp, "stop_reason", None)
        if stop in ("refusal", "max_tokens"):
            raise LLMError(f"Anthropic API stop_reason={stop}")
        texts = [getattr(b, "text", "") for b in getattr(resp, "content", []) if getattr(b, "type", "") == "text"]
        if not texts:
            raise LLMError("Anthropic API returned no text block")
        self.calls.append({"backend": "anthropic_api", "model": model, "stop_reason": stop})
        return _extract_json(texts[0])

    # -- claude_cli -------------------------------------------------------------------
    def _call_cli(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        cli = self.cli_path() or "claude"
        alias = os.environ.get("FERMENTAI_CLAUDE_CLI_MODEL", DEFAULT_CLI_MODEL_ALIAS)
        cmd = [cli, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--system-prompt", system, "--model", alias, "--no-session-persistence",
               "--strict-mcp-config", "--disable-slash-commands", "--tools", ""]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # never mix billing modes
        try:
            proc = self._runner(cmd, input=user, capture_output=True, text=True, timeout=self.timeout_s,
                                cwd=tempfile.gettempdir(), env=env)
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.timeout_s:g} s") from exc
        except OSError as exc:
            raise LLMUnavailable(f"claude CLI could not start: {exc}") from exc
        if proc.returncode != 0:
            raise LLMError(f"claude CLI exit {proc.returncode}: {(proc.stderr or proc.stdout or '')[:200]}")
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError("claude CLI did not return JSON (is --output-format json supported?)") from exc
        if isinstance(obj, list):  # some versions stream a list of events
            obj = next((o for o in reversed(obj) if isinstance(o, dict) and o.get("type") == "result"), {})
        if obj.get("is_error"):
            raise LLMError(f"claude CLI reported an error: {str(obj.get('result'))[:200]}")
        used = obj.get("modelUsage") or {}
        self.model = ", ".join(sorted(used)) if isinstance(used, dict) and used else f"alias:{alias}"
        self.model_selection = f"claude CLI --model {alias} (alias resolves to the newest model of that class)"
        self.calls.append({"backend": "claude_cli", "model": self.model, "duration_ms": obj.get("duration_ms"),
                           "session_id": obj.get("session_id")})
        data = obj.get("structured_output")
        if isinstance(data, dict):
            return data
        result = obj.get("result")
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            return _extract_json(result)
        raise LLMError("claude CLI result had no structured output")

    # -- public -----------------------------------------------------------------------
    def _structured(self, system: str, user: str, schema: dict[str, Any], model: type[BaseModel],
                    ids: list[str]) -> BaseModel:
        ok, reason = self.check_available()
        if not ok:
            raise LLMUnavailable(reason)
        raw = self._call_api(system, user, schema) if self.mode == "anthropic_api" else self._call_cli(system, user, schema)
        try:
            obj = model.model_validate(raw)
        except ValidationError as exc:
            raise LLMError(f"LLM output failed schema validation: {exc.errors()[:2]}") from exc
        for key in ("own_schedule_id", "peer_suggestion_schedule_id"):
            if hasattr(obj, key) and getattr(obj, key) not in ids:
                raise LLMError(f"LLM chose unknown schedule id {getattr(obj, key)!r} for {key}")
        return obj

    def lead(self, agent_id: str, chamber: str, context: dict[str, Any], ids: list[str]) -> LeadDecision:
        peer = "B" if chamber == "A" else "A"
        system = LEAD_SYSTEM.format(agent_id=agent_id, chamber=chamber, peer=peer)
        user = LEAD_TASK.format(chamber=chamber, peer=peer, context=json.dumps(context, indent=1, default=str))
        return self._structured(system, user, lead_schema(ids), LeadDecision, ids)  # type: ignore[return-value]

    def chat(self, agent_id: str, chamber: str, state: dict[str, Any]) -> ChatReply:
        """Word the Sous Chef's next chat message from the deterministic chat state."""
        system = CHAT_SYSTEM.format(agent_id=agent_id, chamber=chamber)
        user = CHAT_TASK.format(state=json.dumps(state, indent=1, default=str))
        out = self._structured(system, user, chat_schema(), ChatReply, [])
        if not out.reply.strip() or len(out.reply) > 800:  # type: ignore[union-attr]
            raise LLMError("chat reply empty or too long")
        return out  # type: ignore[return-value]

    def critique(self, agent_id: str, chamber: str, peer_agent: str, context: dict[str, Any],
                 ids: list[str]) -> PeerDecision:
        system = CRITIC_SYSTEM.format(agent_id=agent_id, chamber=chamber, peer_agent=peer_agent)
        user = CRITIC_TASK.format(chamber=chamber, context=json.dumps(context, indent=1, default=str))
        return self._structured(system, user, critic_schema(ids), PeerDecision, ids)  # type: ignore[return-value]
