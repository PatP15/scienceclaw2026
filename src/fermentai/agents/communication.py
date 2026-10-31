"""Shared append-only scientific findings log (PLAN §10) and agent message schema.

Each line of the JSONL file is one :class:`Finding`. The log only ever appends: there is no
update or delete API, and existing bytes are never rewritten. Agents read peer findings
from the log and respond with :func:`challenge` (a ``critique`` finding that points at the
original with ``in_reply_to``).

Default path: ``data/experiments/findings_log.jsonl`` (override with the
``FERMENTAI_FINDINGS_LOG`` env var or by passing ``path``). ``FindingsLog(path=None)``
gives an in-memory log with identical semantics (used by benchmarks and tests).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_PATH = REPO_ROOT / "data" / "experiments" / "findings_log.jsonl"

FindingType = Literal[
    # PLAN §10 (v1.0)
    "observation", "hypothesis", "prediction", "uncertainty",
    "proposed_measurement", "proposed_intervention", "critique", "rationale",
    # PLAN_MASTER §9.4 (added; v1.0 types remain valid)
    "observation_summary", "anomaly", "experiment_proposal", "peer_critique",
    "experiment_result", "model_update", "sensory_feedback",
]
EvidenceSource = Literal["real", "simulated", "replay", "estimated", "imported_human", "judge_feedback"]
FINDING_TYPES: tuple[str, ...] = FindingType.__args__  # type: ignore[attr-defined]
EVIDENCE_SOURCES: tuple[str, ...] = EvidenceSource.__args__  # type: ignore[attr-defined]


class Finding(BaseModel):
    """One structured agent-to-agent message / scientific finding."""

    id: str
    agent_id: str
    timestamp_utc: str
    type: FindingType
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_source: EvidenceSource
    in_reply_to: str | None = None
    chamber_id: str | None = None
    cycle_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    # PLAN_MASTER §9.4 fields (optional, additive)
    recipients: list[str] = Field(default_factory=list)
    experiment_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    claim: str | None = None
    uncertainty: str | None = None
    proposed_action: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_finding_id(agent_id: str, type_: str, content: str, timestamp_utc: str, cycle_id: str | None) -> str:
    h = hashlib.sha256(f"{cycle_id}|{agent_id}|{type_}|{timestamp_utc}|{content}".encode()).hexdigest()
    return f"f_{h[:16]}"


def new_finding(
    agent_id: str,
    type_: str,
    content: str,
    *,
    payload: dict[str, Any] | None = None,
    evidence_source: str = "estimated",
    in_reply_to: str | None = None,
    chamber_id: str | None = None,
    cycle_id: str | None = None,
    timestamp_utc: str | None = None,
    recipients: list[str] | None = None,
    experiment_id: str | None = None,
    evidence_refs: list[str] | None = None,
    claim: str | None = None,
    uncertainty: str | None = None,
    proposed_action: dict[str, Any] | None = None,
) -> Finding:
    ts = timestamp_utc or utc_now_iso()
    return Finding(
        id=make_finding_id(agent_id, type_, content, ts, cycle_id),
        agent_id=agent_id,
        timestamp_utc=ts,
        type=type_,  # type: ignore[arg-type]
        content=content,
        payload=payload or {},
        evidence_source=evidence_source,  # type: ignore[arg-type]
        in_reply_to=in_reply_to,
        chamber_id=chamber_id,
        cycle_id=cycle_id,
        recipients=list(recipients or []),
        experiment_id=experiment_id,
        evidence_refs=list(evidence_refs or []),
        claim=claim,
        uncertainty=uncertainty,
        proposed_action=proposed_action,
    )


def default_log_path() -> Path:
    env = os.environ.get("FERMENTAI_FINDINGS_LOG")
    return Path(env) if env else DEFAULT_LOG_PATH


class FindingsLog:
    """Append-only findings log. ``path=None`` -> in-memory (same semantics)."""

    def __init__(self, path: str | os.PathLike | None | Literal["default"] = "default"):
        if path == "default":
            path = default_log_path()
        self.path: Path | None = Path(path) if path is not None else None
        self._memory: list[Finding] = []
        self._ids: set[str] | None = None

    # -- writing ----------------------------------------------------------------------
    def _known_ids(self) -> set[str]:
        if self._ids is None:
            self._ids = {f.id for f in self.read()}
        return self._ids

    def append(self, finding: Finding | dict[str, Any]) -> Finding:
        """Append one finding. Re-appending an identical id is a no-op (idempotent)."""
        f = finding if isinstance(finding, Finding) else Finding.model_validate(finding)
        ids = self._known_ids()
        if f.id in ids:
            return f
        if self.path is None:
            self._memory.append(f)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(f.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            with open(self.path, "a", encoding="utf-8") as fh:  # append mode only
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        ids.add(f.id)
        return f

    def extend(self, findings: Iterable[Finding]) -> list[Finding]:
        return [self.append(f) for f in findings]

    # -- reading ----------------------------------------------------------------------
    def _all(self) -> list[Finding]:
        if self.path is None:
            return list(self._memory)
        if not self.path.exists():
            return []
        out: list[Finding] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Finding.model_validate_json(line))
                except Exception:  # noqa: BLE001 - skip corrupt lines, never rewrite them
                    continue
        return out

    def read(
        self,
        *,
        agent_id: str | None = None,
        type: str | None = None,
        cycle_id: str | None = None,
        in_reply_to: str | None = None,
        chamber_id: str | None = None,
        exclude_agent: str | None = None,
        since_utc: str | None = None,
        limit: int | None = None,
        recipient: str | None = None,
        experiment_id: str | None = None,
    ) -> list[Finding]:
        res = []
        for f in self._all():
            if recipient and recipient not in f.recipients:
                continue
            if experiment_id and f.experiment_id != experiment_id:
                continue
            if agent_id and f.agent_id != agent_id:
                continue
            if exclude_agent and f.agent_id == exclude_agent:
                continue
            if type and f.type != type:
                continue
            if cycle_id and f.cycle_id != cycle_id:
                continue
            if in_reply_to and f.in_reply_to != in_reply_to:
                continue
            if chamber_id and f.chamber_id != chamber_id:
                continue
            if since_utc and f.timestamp_utc < since_utc:
                continue
            res.append(f)
        if limit is not None:
            res = res[-limit:]
        return res

    def get(self, finding_id: str) -> Finding | None:
        for f in self._all():
            if f.id == finding_id:
                return f
        return None

    def thread(self, root_id: str) -> list[Finding]:
        """The root finding plus all (transitive) replies, in log order."""
        all_f = self._all()
        keep = {root_id}
        out = []
        for f in all_f:
            if f.id in keep or (f.in_reply_to and f.in_reply_to in keep):
                keep.add(f.id)
                out.append(f)
        return out


def challenge(
    finding: Finding,
    *,
    critic_agent_id: str,
    content: str,
    payload: dict[str, Any] | None = None,
    evidence_source: str = "estimated",
    log: FindingsLog | None = None,
    chamber_id: str | None = None,
    cycle_id: str | None = None,
    timestamp_utc: str | None = None,
    experiment_id: str | None = None,
    claim: str | None = None,
    uncertainty: str | None = None,
    proposed_action: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
) -> Finding:
    """Post a ``critique`` of a peer finding (appended to ``log`` when given)."""
    if critic_agent_id == finding.agent_id:
        raise ValueError("an agent cannot challenge its own finding")
    crit = new_finding(
        critic_agent_id, "critique", content,
        payload={**(payload or {}), "challenged_finding_type": finding.type,
                 "challenged_agent_id": finding.agent_id},
        evidence_source=evidence_source,
        in_reply_to=finding.id,
        chamber_id=chamber_id,
        cycle_id=cycle_id or finding.cycle_id,
        timestamp_utc=timestamp_utc,
        recipients=[finding.agent_id],
        experiment_id=experiment_id or finding.experiment_id,
        evidence_refs=[finding.id, *(evidence_refs or [])],
        claim=claim,
        uncertainty=uncertainty,
        proposed_action=proposed_action,
    )
    if log is not None:
        log.append(crit)
    return crit
