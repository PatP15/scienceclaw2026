"""Append-only findings log and message schema (fermentai.agents.communication)."""

import json

import pytest
from pydantic import ValidationError

from fermentai.agents.communication import (
    EVIDENCE_SOURCES,
    FINDING_TYPES,
    FindingsLog,
    challenge,
    new_finding,
)


def _f(agent="scientist_A", type_="observation", content="pH 4.3 at 10 h", ts="2026-10-31T12:00:00Z", **kw):
    return new_finding(agent, type_, content, timestamp_utc=ts, cycle_id="cyc_test", **kw)


def test_schema_fields_and_vocabularies():
    v10 = {"observation", "hypothesis", "prediction", "uncertainty", "proposed_measurement",
           "proposed_intervention", "critique", "rationale"}
    v94 = {"observation_summary", "hypothesis", "prediction", "anomaly", "experiment_proposal", "peer_critique",
           "experiment_result", "model_update", "sensory_feedback"}
    assert v10 <= set(FINDING_TYPES) and v94 <= set(FINDING_TYPES)  # additive: old types remain valid
    assert set(EVIDENCE_SOURCES) == {"real", "simulated", "replay", "estimated", "imported_human", "judge_feedback"}
    d = _f(payload={"ph": 4.3}, evidence_source="simulated").to_dict()
    for k in ("id", "agent_id", "timestamp_utc", "type", "content", "payload", "evidence_source", "in_reply_to"):
        assert k in d
    with pytest.raises(ValidationError):
        _f(type_="gossip")
    with pytest.raises(ValidationError):
        _f(evidence_source="vibes")


def test_append_only_file(tmp_path):
    path = tmp_path / "findings_log.jsonl"
    log = FindingsLog(path)
    log.append(_f(content="first"))
    before = path.read_bytes()
    log.append(_f(content="second", agent="scientist_B"))
    log.append(_f(content="third"))
    after = path.read_bytes()
    assert after.startswith(before)  # existing bytes never rewritten
    assert len(after.splitlines()) == 3
    assert all(json.loads(line)["schema_version"] == "1.0" for line in after.splitlines())
    assert not hasattr(log, "delete") and not hasattr(log, "update")
    # re-opening reads the same entries; duplicate ids are idempotent
    log2 = FindingsLog(path)
    assert [f.content for f in log2.read()] == ["first", "second", "third"]
    log2.append(log2.read()[0])
    assert path.read_bytes() == after


def test_read_filters_and_threads(tmp_path):
    log = FindingsLog(tmp_path / "log.jsonl")
    h = log.append(_f(type_="hypothesis", content="warm burst then cool"))
    log.append(_f(agent="scientist_B", content="B observation"))
    crit = challenge(h, critic_agent_id="scientist_B", content="overproof risk", log=log, timestamp_utc="2026-10-31T12:01:00Z")
    assert crit.in_reply_to == h.id and crit.type == "critique"
    assert crit.payload["challenged_agent_id"] == "scientist_A"
    assert [f.id for f in log.read(agent_id="scientist_B", type="critique")] == [crit.id]
    assert len(log.read(exclude_agent="scientist_A")) == 2
    assert [f.id for f in log.thread(h.id)] == [h.id, crit.id]
    assert log.get(crit.id).content == "overproof risk"
    with pytest.raises(ValueError):
        challenge(h, critic_agent_id="scientist_A", content="self", log=log)


def test_in_memory_log_has_same_semantics():
    log = FindingsLog(None)
    a = log.append(_f())
    log.append(a)
    assert len(log.read()) == 1


def test_env_override(tmp_path, monkeypatch):
    p = tmp_path / "env_log.jsonl"
    monkeypatch.setenv("FERMENTAI_FINDINGS_LOG", str(p))
    FindingsLog().append(_f())
    assert p.exists()
