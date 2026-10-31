"""Read-only agent state for ``GET /api/v1/agents/{id}/state`` (PLAN_MASTER §4, §9).

State is reconstructed from the shared append-only findings log, so it reflects exactly what
each agent has published (no hidden coordinator state):

    get_agent_state("A")            # or "B", "scientist_A", "scientist_B"
    get_agent_state("A", log_path="data/experiments/findings_log.jsonl")
"""

from __future__ import annotations

from typing import Any

from .communication import Finding, FindingsLog

OBJECTIVES = {
    "A": "Mild hypothesis: relatively mild acidity with sufficient expansion (initial 22 °C).",
    "B": "More-acidic hypothesis: increased acidification and detectable flavor differences (initial 27 °C).",
}
LABEL_BY_SOURCE = {"llm": "Online Agent", "offline_policy": "Offline Policy"}


def normalize_agent_id(agent_id: str) -> tuple[str, str]:
    """Return (agent_id, chamber_id); accepts "A", "b", "scientist_A", "Scientist-B"."""
    raw = str(agent_id or "").strip()
    ch = raw.split("_")[-1].split("-")[-1].upper() if raw else ""
    if ch not in ("A", "B"):
        raise ValueError(f"unknown agent id {agent_id!r} (expected A, B, scientist_A or scientist_B)")
    return f"scientist_{ch}", ch


def _last(findings: list[Finding], pred) -> Finding | None:
    for f in reversed(findings):
        if pred(f):
            return f
    return None


def _brief(f: Finding | None, n: int = 240) -> dict[str, Any] | None:
    if f is None:
        return None
    return {"id": f.id, "type": f.type, "agent_id": f.agent_id, "timestamp_utc": f.timestamp_utc,
            "experiment_id": f.experiment_id, "content": f.content[:n] + ("…" if len(f.content) > n else "")}


def get_agent_state(agent_id: str, *, log: FindingsLog | None = None, log_path: str | None = None) -> dict[str, Any]:
    aid, ch = normalize_agent_id(agent_id)
    peer = f"scientist_{'B' if ch == 'A' else 'A'}"
    lg = log if log is not None else FindingsLog(log_path if log_path is not None else "default")
    allf = lg.read()
    mine = [f for f in allf if f.agent_id == aid]
    peers = [f for f in allf if f.agent_id == peer]
    state: dict[str, Any] = {
        "agent_id": aid, "chamber_id": ch, "objective": OBJECTIVES[ch],
        "available": bool(mine), "n_findings": len(mine),
        "last_activity_utc": mine[-1].timestamp_utc if mine else None,
        "last_cycle_id": mine[-1].cycle_id if mine else None,
        "source": {"findings_log": str(lg.path) if lg.path else "in-memory"},
    }
    # current hypothesis: own proposal, or the alternative it advanced as critic
    h = _last(mine, lambda f: f.type == "hypothesis" or (f.type in ("critique", "peer_critique")
                                                           and f.payload.get("alternative_hypothesis")))
    if h is not None:
        hyp = h.payload.get("hypothesis") or {}
        state["current_hypothesis"] = {
            "statement": hyp.get("statement") or h.payload.get("alternative_hypothesis") or h.claim,
            "mechanism": hyp.get("mechanism"),
            "role": h.payload.get("role", "proposer" if h.type == "hypothesis" else "critic"),
            "finding_id": h.id, "timestamp_utc": h.timestamp_utc, "experiment_id": h.experiment_id,
        }
    else:
        state["current_hypothesis"] = None
    # last decision for its own chamber
    d = _last(mine, lambda f: (f.type == "rationale" and f.payload.get("final_decision"))
              or (f.type == "proposed_intervention" and f.payload.get("scope") == "next_experiment"))
    if d is not None:
        dec = d.payload.get("final_decision") or d.payload
        state["last_decision"] = {k: dec.get(k) for k in ("schedule_id", "name", "points", "route")} | {
            "chamber": ch, "finding_id": d.id, "timestamp_utc": d.timestamp_utc, "experiment_id": d.experiment_id,
            "revised_after_critique": bool(d.payload.get("revised")) if d.type == "rationale" else None}
    else:
        state["last_decision"] = None
    run = _last(mine, lambda f: f.type == "proposed_intervention" and f.payload.get("scope") == "current_run")
    state["last_current_run_proposal"] = ({k: run.payload.get(k) for k in ("status", "action", "suggested_setpoint_c",
                                                                           "rationale", "requires_human_confirmation")}
                                          | {"finding_id": run.id} if run else None)
    # prediction state
    p = _last(mine, lambda f: f.type == "prediction")
    state["prediction_state"] = ({"summary": p.content, "finding_id": p.id, "timestamp_utc": p.timestamp_utc,
                                  **{k: p.payload.get(k) for k in ("schedule_id", "sourness", "openness", "rise_progress",
                                                                   "overproof_risk", "final_ph", "uncertainty",
                                                                   "provenance")}} if p else None)
    # peer findings
    addressed = [f for f in peers if aid in (f.recipients or []) or f.in_reply_to in {m.id for m in mine}]
    state["peer_findings"] = {
        "peer_agent_id": peer, "count": len(peers), "addressed_to_me": len(addressed),
        "critiques_of_my_findings": len([f for f in peers if f.in_reply_to in {m.id for m in mine}]),
        "latest": _brief(peers[-1]) if peers else None,
    }
    # last observation quality summary
    o = _last(mine, lambda f: f.type in ("observation_summary", "observation") and "observation_quality" in f.payload)
    if o is not None:
        q = o.payload.get("observation_quality") or {}
        op = o.payload.get("overproofing") or {}
        state["last_observation"] = {
            "finding_id": o.id, "timestamp_utc": o.timestamp_utc, "evidence_source": o.evidence_source,
            "n_records": q.get("n"), "sources": q.get("sources"), "latest_age_s": q.get("latest_age_s"),
            "latest_stale": q.get("latest_stale"), "usable_fields": q.get("latest_usable"),
            "ph_valid": q.get("ph_valid"), "ph_reason": q.get("ph_reason"),
            "anomalies": [{"code": a.get("code"), "severity": a.get("severity"), "category": a.get("category")}
                          for a in q.get("anomalies", [])],
            "proofing_status": op.get("status"), "proofing_action": op.get("action"),
        }
    else:
        state["last_observation"] = None
    anomalies = [f for f in mine if f.type == "anomaly" and (not mine or f.cycle_id == mine[-1].cycle_id)]
    state["open_anomalies"] = [_brief(f) for f in anomalies]
    # reasoning mode of the latest decision
    m = _last(mine, lambda f: f.payload.get("decision_source") in LABEL_BY_SOURCE)
    if m is not None:
        src = m.payload["decision_source"]
        state["mode"] = {"decision_source": src,
                         "reasoning_mode_label": m.payload.get("reasoning_mode_label") or LABEL_BY_SOURCE[src],
                         "finding_id": m.id}
    else:
        state["mode"] = {"decision_source": None, "reasoning_mode_label": None, "finding_id": None}
    state["recent_findings"] = [_brief(f, 160) for f in mine[-5:]]
    return state
