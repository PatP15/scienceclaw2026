"""Quality-aware decisions, §9.4 message fields, §14 example, agent state (PLAN_MASTER)."""

from datetime import datetime, timedelta

import pytest

from fermentai.agents.communication import FindingsLog
from fermentai.agents.pipeline import run_taste_feedback_cycle
from fermentai.agents.sensory import parse_feedback
from fermentai.agents.state import get_agent_state, normalize_agent_id

NOW = "2026-10-31T18:00:00Z"
PLAN14 = ("Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, but the crumb is too dense. "
          "I'd like a more sour loaf that's still fluffy.")


def ctx(**kw):
    return {"persist_findings": False, "now_utc": NOW, **kw}


def v11(chamber, heights, setpoint, *, ph_last=None, calibrated=True, source="measured", step_min=10, **extra):
    end = datetime(2026, 10, 31, 17, 58)
    rows = []
    for i, h in enumerate(heights):
        last = i == len(heights) - 1
        t = end - timedelta(minutes=step_min * (len(heights) - 1 - i))
        r = {"schema_version": "1.1", "device_id": f"fermentai-esp32-{chamber.lower()}", "chamber_id": chamber,
             "boot_id": "boot1", "sequence_number": i, "timestamp_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "timestamp_quality": "synced", "device_uptime_ms": i * step_min * 60_000,
             "starter_temperature_c": setpoint - 0.2, "chamber_temperature_c": setpoint + 0.4,
             "temperature_setpoint_c": setpoint, "starter_height_mm": h, "source": source,
             "ph": ph_last if last else None, "ph_method": "probe_sample" if (last and ph_last) else None,
             "diagnostics": {"ph_calibration_valid": calibrated} if (last and ph_last) else {}}
        r.update(extra)
        rows.append(r)
    return rows


def test_plan_master_14_structured_preference():
    r = parse_feedback(PLAN14, {"A": 22.0, "B": 27.0})
    assert r["structured_preference"] == ["More sourness", "Lower density", "Preserve good rise (airy/fluffy crumb)"]
    assert r["desired"]["sourness"]["direction"] == "increase"
    assert r["preferred_loaf"] == "B"
    assert r["loaves"]["B"]["attributes"]["density"]["score"] > 0.8
    assert r["loaves"]["A"]["attributes"]["texture"]["score"] > 0.6
    assert "lower density" in r["desired_summary"]
    out = run_taste_feedback_cycle(PLAN14, ctx(lead_agent="A"))
    assert out["hypothesis"]["proposed_by"] == "scientist_A" and out["hypothesis"]["challenged_by"] == "scientist_B"
    t = out["hypothesis"]["targets"]
    assert t["sourness"]["mode"] == "near" and t["openness"]["mode"] == "at_least"


def test_reasoning_mode_label():
    r = run_taste_feedback_cycle(PLAN14, ctx())
    assert r["reasoning_mode_label"] == "Offline Policy"
    assert r["mode_label"] == "offline:deterministic_fallback"  # unchanged
    assert r["provenance"]["reasoning_mode_label"] == "Offline Policy"
    assert r["agent_messages"][0]["payload"]["reasoning_mode_label"] == "Offline Policy"


def test_message_fields_9_4():
    r = run_taste_feedback_cycle(PLAN14, ctx(experiment_id="trial_001"))
    msgs = {f["type"]: f for f in r["findings_logged"]}
    assert {"sensory_feedback", "hypothesis", "critique", "rationale", "experiment_proposal"} <= set(msgs)
    hyp = r["agent_messages"][0]
    peer = r["agent_messages"][1]["agent_id"]
    assert hyp["recipients"] == [peer]
    assert hyp["experiment_id"] == "trial_002"
    assert msgs["sensory_feedback"]["id"] in hyp["evidence_refs"]
    assert hyp["claim"] and hyp["uncertainty"]
    assert hyp["proposed_action"]["type"] == "set_schedule" and hyp["proposed_action"]["validated_library_schedule"]
    crit = r["agent_messages"][1]
    assert crit["recipients"] == [hyp["agent_id"]] and hyp["id"] in crit["evidence_refs"]
    prop = msgs["experiment_proposal"]
    assert set(prop["proposed_action"]["schedules"]) == {"A", "B"}
    assert prop["proposed_action"]["requires_human_confirmation"] is True
    assert "operator" in prop["recipients"]


def test_no_valid_ph_is_stated_and_widens_uncertainty():
    no_tel = run_taste_feedback_cycle(PLAN14, ctx())
    lead, crit = no_tel["agent_messages"][0], no_tel["agent_messages"][1]
    assert "No valid pH measurement" in lead["payload"]["uncertainty"]
    assert "No valid pH measurement" in crit["content"]
    assert "widened by 0.05" in lead["payload"]["uncertainty"]
    # chamber B has a calibrated pH reading -> its agent says so and does not widen
    tel = {"B": v11("B", [20, 21, 22, 23], 27.0, ph_last=4.2)}
    r = run_taste_feedback_cycle(PLAN14, ctx(telemetry=tel, lead_agent="B"))
    assert r["provenance"]["ph_status"]["B"]["valid"] is True
    assert r["provenance"]["ph_status"]["A"]["valid"] is False
    assert "Latest valid pH in chamber B: 4.20" in r["agent_messages"][0]["payload"]["uncertainty"]
    u_valid = r["next_experiment"]["schedules"]["B"]["predicted"]["uncertainty"]
    u_none = no_tel["next_experiment"]["schedules"]["B"]["predicted"]["uncertainty"]
    if r["next_experiment"]["schedules"]["B"]["schedule_id"] == no_tel["next_experiment"]["schedules"]["B"]["schedule_id"]:
        assert u_none == pytest.approx(u_valid + 0.05)
    # an uncalibrated reading is rejected and reported, not used
    bad = run_taste_feedback_cycle(PLAN14, ctx(telemetry={"B": v11("B", [20, 21, 22, 23], 27.0, ph_last=4.2,
                                                                  calibrated=False)}))
    st = bad["provenance"]["ph_status"]["B"]
    assert st["valid"] is False and "uncalibrated" in st["reason"]


def test_observation_messages_only_with_telemetry():
    none = run_taste_feedback_cycle(PLAN14, ctx())
    assert not [f for f in none["findings_logged"] if f["type"] in ("observation_summary", "anomaly")]
    assert none["observations"]["A"]["latest"] is None
    tel = {"A": v11("A", [20, 21, 22.5], 22.0, external_heatsink_temperature_c=72.0)}
    r = run_taste_feedback_cycle(PLAN14, ctx(telemetry=tel))
    summ = [f for f in r["findings_logged"] if f["type"] == "observation_summary"]
    assert len(summ) == 1 and summ[0]["chamber_id"] == "A" and summ[0]["evidence_source"] == "real"
    assert summ[0]["evidence_refs"][0].startswith("telemetry:fermentai-esp32-a:boot1:")
    anomalies = [f for f in r["findings_logged"] if f["type"] == "anomaly"]
    hs = [f for f in anomalies if f["payload"]["anomaly"]["code"] == "heatsink_overtemperature"]
    assert hs and hs[0]["payload"]["anomaly"]["category"] == "equipment_safety"
    assert hs[0]["proposed_action"]["action"] == "equipment_diagnostics"
    acts = r["current_run_actions"]["A"]["additional_actions"]
    assert any(a["action"] == "equipment_diagnostics" for a in acts)
    assert any(a["action"] == "request_measurement" and "pH" in a["what"] for a in acts)


def test_simulated_values_never_fill_missing_observations():
    tel = {"B": v11("B", [20, 21, 22, 23], 27.0)}  # no pH at all
    pred = {"B": {"time_h": [0, 0.5, 1.0], "temperature_c": [27, 27, 27], "ph": [5.2, 5.0, 4.8],
                  "expansion_ratio": [1.0, 1.03, 1.06]}}
    r = run_taste_feedback_cycle(PLAN14, ctx(telemetry=tel, predictions=pred))
    latest = r["observations"]["B"]["latest"]["fields"]
    assert latest["ph"]["value"] is None and latest["ph"]["quality"] == "missing"
    dev = r["current_run_actions"]["B"]["deviations"]
    assert dev["comparable"] and dev["ph"]["n"] == 0 and "no valid pH" in dev["ph"]["note"]
    assert dev["temperature_vs_prediction"]["n"] == 4
    assert r["provenance"]["physical_observations_imputed"] is False


def test_simulated_labelled_as_measured_is_rejected():
    tel = {"B": v11("B", [20, 24, 29, 34, 38, 40, 40.5, 39, 37.5], 27.0, step_min=20, device_id="virtual-esp32-b")}
    r = run_taste_feedback_cycle(PLAN14, ctx(telemetry=tel))
    assert r["current_run_actions"]["B"]["action"] == "monitor"  # no intervention on rejected data
    codes = {f["payload"]["anomaly"]["code"] for f in r["findings_logged"] if f["type"] == "anomaly"}
    assert "simulated_labelled_as_measured" in codes


def test_low_confidence_heights_are_excluded_from_proofing_rule():
    rows = v11("B", [20, 24, 29, 34, 38, 40, 40.5, 39, 37.5], 27.0, step_min=20,
               field_provenance={"starter_height_mm": {"confidence": 0.2}})
    r = run_taste_feedback_cycle(PLAN14, ctx(telemetry={"B": rows}))
    op = r["current_run_actions"]["B"]
    assert op["status"] == "insufficient_data" and op["evidence"]["excluded_height_points"] == {"low_confidence": 9}
    good = run_taste_feedback_cycle(PLAN14, ctx(telemetry={"B": v11("B", [20, 24, 29, 34, 38, 40, 40.5, 39, 37.5],
                                                                   27.0, step_min=20)}))
    assert good["current_run_actions"]["B"]["action"] == "end_fermentation"


def test_get_agent_state(tmp_path):
    path = tmp_path / "log.jsonl"
    empty = get_agent_state("A", log_path=str(path))
    assert empty["available"] is False and empty["current_hypothesis"] is None
    tel = {"B": v11("B", [20, 21, 22, 23], 27.0, ph_last=4.2)}
    run_taste_feedback_cycle(PLAN14, {"findings_log_path": str(path), "now_utc": NOW, "telemetry": tel,
                                      "lead_agent": "A"})
    a = get_agent_state("A", log_path=str(path))
    b = get_agent_state("scientist_B", log_path=str(path))
    assert get_agent_state("b", log=FindingsLog(path))["agent_id"] == "scientist_B"
    assert a["available"] and a["current_hypothesis"]["role"] == "proposer"
    assert b["current_hypothesis"]["role"] == "critic"
    assert a["last_decision"]["schedule_id"] and b["last_decision"]["chamber"] == "B"
    assert a["prediction_state"]["schedule_id"] == a["last_decision"]["schedule_id"] or a["prediction_state"]
    assert a["peer_findings"]["count"] > 0 and a["peer_findings"]["critiques_of_my_findings"] >= 1
    assert b["last_observation"]["ph_valid"] is True and a["last_observation"] is None
    assert a["mode"]["reasoning_mode_label"] == "Offline Policy"
    for k in ("current_hypothesis", "last_decision", "prediction_state", "peer_findings", "last_observation", "mode"):
        assert k in a
    with pytest.raises(ValueError):
        normalize_agent_id("C")
