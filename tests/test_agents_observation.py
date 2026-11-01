"""Structured observation adapter (fermentai.agents.observation; PLAN_MASTER §9.1-9.2)."""

import json
from pathlib import Path

import pytest

from fermentai.agents.observation import (
    QUALITIES,
    ChamberObservation,
    compare_to_prediction,
    normalize_history,
    normalize_observation,
    summarize_history,
)

REPO = Path(__file__).resolve().parents[1]
NOW = "2026-10-31T18:00:00Z"

# PLAN_MASTER §4 synthetic v1.1 interface example (not a real observation)
V11 = {
    "schema_version": "1.0", "device_id": "fermentai-esp32-a", "chamber_id": "A", "experiment_id": "experiment_001",
    "boot_id": "boot_a001", "sequence_number": 120, "timestamp_utc": None, "timestamp_quality": "unsynced",
    "device_uptime_ms": 120000, "chamber_temperature_c": 24.3, "starter_temperature_c": 23.9,
    "internal_heatsink_temperature_c": 31.2, "external_heatsink_temperature_c": 37.1,
    "relative_humidity_percent": 72.0, "pressure_pa": 101325.0, "gas_resistance_ohms": 105432.0, "ph": None,
    "ph_method": None, "starter_height_mm": None, "door_open": False, "peltier_mode": "heating",
    "peltier_output_fraction": 0.35, "temperature_setpoint_c": 25.0, "motor_running": False,
    "source": "simulated", "quality_flags": [],
}


def rec(**kw):
    base = {"schema_version": "1.1", "device_id": "fermentai-esp32-a", "chamber_id": "A", "boot_id": "b1",
            "sequence_number": 1, "timestamp_utc": "2026-10-31T17:58:00Z", "timestamp_quality": "synced",
            "device_uptime_ms": 1000, "starter_temperature_c": 24.0, "chamber_temperature_c": 24.6,
            "temperature_setpoint_c": 24.0, "starter_height_mm": 30.0, "source": "measured", "quality_flags": []}
    base.update(kw)
    return base


def q(o: ChamberObservation, name: str) -> str:
    return o.fields[name].quality


def test_v11_example_roles_and_quality():
    o = normalize_observation(V11, "A")
    assert o.record_source == "simulated" and o.time_basis == "uptime_only"
    assert set(QUALITIES) == {"ok", "stale", "missing", "invalid", "uncalibrated", "low_confidence", "wrong_chamber",
                              "unsynced_time"}
    f = o.fields
    assert f["starter_temperature"].role == "controlled_variable" and f["starter_temperature"].quality == "ok"
    assert f["chamber_temperature"].role == "environment"
    for hs in ("internal_heatsink_temperature", "external_heatsink_temperature"):
        assert f[hs].role == "equipment_safety" and not f[hs].usable_as_evidence  # never fermentation evidence
    assert f["gas_resistance"].quality == "uncalibrated" and "NOT a CO2" in f["gas_resistance"].note
    assert f["motor_running"].role == "decorative" and not f["motor_running"].usable_as_evidence
    assert f["peltier_mode"].value == "heat" and f["peltier_mode"].role == "control_input"
    assert q(o, "ph") == "missing" and q(o, "starter_height") == "missing"
    assert f["ph"].value is None  # missing stays missing (no simulated fill)
    assert o.derived["tracking_error_c"] == pytest.approx(-1.1)
    for fr in f.values():
        assert set(fr.model_dump()) >= {"value", "unit", "source", "quality", "note"}
    # judged against "now", an uptime-only reading has unverifiable age
    o2 = normalize_observation(V11, "A", now_utc=NOW)
    assert q(o2, "starter_temperature") == "unsynced_time"


def test_v10_firmware_contract_example():
    data = json.loads((REPO / "firmware_contract" / "telemetry_example.json").read_text())
    o = normalize_observation(data, "A")
    assert o.time_basis == "device_utc" and o.record_source == "simulated"
    assert q(o, "heatsink_temperature") == "low_confidence"  # v1.0 single heatsink: placement unknown
    assert o.fields["peltier_output_fraction"].value == 0.0  # from peltier_duty_percent
    assert o.device_uptime_ms == 3_600_000
    assert normalize_observation(dict(data, source="real"), "A").record_source == "measured"


@pytest.mark.parametrize("field,value,quality", [
    ("starter_temperature_c", 85.0, "invalid"),       # DS18B20 power-on value
    ("starter_temperature_c", -127.0, "invalid"),     # DS18B20 disconnected
    ("starter_temperature_c", "warm", "invalid"),
    ("relative_humidity_percent", 120.0, "invalid"),
    ("starter_height_mm", 400.0, "invalid"),
    ("starter_height_mm", -2.0, "invalid"),
    ("peltier_output_fraction", 1.7, "invalid"),
])
def test_invalid_values(field, value, quality):
    name = {"starter_temperature_c": "starter_temperature", "relative_humidity_percent": "relative_humidity",
            "starter_height_mm": "starter_height", "peltier_output_fraction": "peltier_output_fraction"}[field]
    o = normalize_observation(rec(**{field: value}), "A", NOW)
    assert q(o, name) == quality
    assert not o.fields[name].usable_as_evidence


def test_stale_latest_and_receipt_time():
    old = normalize_observation(rec(timestamp_utc="2026-10-31T16:00:00Z"), "A", NOW)
    assert q(old, "starter_temperature") == "stale" and old.age_s == 7200
    unsynced = normalize_observation(rec(timestamp_utc=None, timestamp_quality="unsynced",
                                         received_at_utc="2026-10-31T17:59:30Z"), "A", NOW)
    assert unsynced.time_basis == "server_receipt" and q(unsynced, "starter_temperature") == "ok"
    none = normalize_observation(rec(timestamp_utc=None, device_uptime_ms=None), "A", NOW)
    assert none.time_basis == "none" and q(none, "starter_temperature") == "unsynced_time"
    assert any(a.code == "no_timestamp" for a in none.anomalies)


def test_wrong_chamber_and_simulated_labelled_measured():
    wc = normalize_observation(rec(chamber_id="B"), "A", NOW)
    assert q(wc, "starter_temperature") == "wrong_chamber"
    assert any(a.code == "wrong_chamber" and a.severity == "critical" for a in wc.anomalies)
    stub = normalize_observation(rec(device_id="virtual-esp32-a"), "A", NOW)
    assert q(stub, "starter_temperature") == "invalid"
    assert any(a.code == "simulated_labelled_as_measured" for a in stub.anomalies)
    mixed = normalize_observation(rec(ph=4.2, ph_method="probe_sample", field_provenance={"ph": {"source": "simulated"}}),
                                  "A", NOW)
    assert q(mixed, "ph") == "invalid"
    manual = normalize_observation(rec(ph=4.2, ph_method="manual", field_provenance={"ph": {"source": "manual"}}), "A", NOW)
    assert manual.fields["ph"].source == "manual"


@pytest.mark.parametrize("extra,quality", [
    ({"ph": 4.1, "ph_method": None}, "uncalibrated"),
    ({"ph": 4.1, "ph_method": "probe_sample"}, "uncalibrated"),
    ({"ph": 4.1, "ph_method": "probe_sample", "diagnostics": {"ph_calibration_valid": True}}, "ok"),
    ({"ph": 4.1, "ph_method": "probe_immersed", "diagnostics": {"ph_calibration_valid": True},
      "quality_flags": ["ph_drift"]}, "invalid"),
    ({"ph": 4.1, "ph_method": "strip"}, "low_confidence"),
    ({"ph": 9.0, "ph_method": "probe_sample", "diagnostics": {"ph_calibration_valid": True}}, "invalid"),
    ({"ph": 4.1, "ph_method": "probe_sample", "diagnostics": {"ph_calibration_valid": True},
      "quality_flags": ["ph_uncalibrated"]}, "uncalibrated"),
])
def test_ph_requires_method_and_calibration(extra, quality):
    assert q(normalize_observation(rec(**extra), "A", NOW), "ph") == quality


def test_camera_height_confidence_and_door():
    low = normalize_observation(rec(field_provenance={"starter_height_mm": {"confidence": 0.3}}), "A", NOW)
    assert q(low, "starter_height") == "low_confidence" and low.fields["starter_height"].confidence == 0.3
    door = normalize_observation(rec(door_open=True), "A", NOW)
    assert q(door, "starter_height") == "low_confidence"
    assert any(a.category == "disturbance" for a in door.anomalies)
    flagged = normalize_observation(rec(quality_flags=["height_estimate_low_confidence"]), "A", NOW)
    assert q(flagged, "starter_height") == "low_confidence"


def test_heatsink_is_equipment_safety_only():
    o = normalize_observation(rec(external_heatsink_temperature_c=72.0), "A", NOW)
    a = next(a for a in o.anomalies if a.code == "heatsink_overtemperature")
    assert a.category == "equipment_safety" and a.severity == "critical"
    assert "Not evidence about fermentation" in a.message
    assert not o.fields["external_heatsink_temperature"].usable_as_evidence


def test_control_anomalies():
    o = normalize_observation(rec(starter_temperature_c=27.5, temperature_setpoint_c=24.0, peltier_mode="heat"), "A", NOW)
    codes = {a.code for a in o.anomalies}
    assert {"setpoint_tracking_error", "control_direction_mismatch"} <= codes
    bad_sp = normalize_observation(rec(temperature_setpoint_c=40.0), "A", NOW)
    assert any(a.code == "setpoint_out_of_bounds" for a in bad_sp.anomalies)


def test_history_dedupe_order_and_uptime_axis():
    rows = [rec(sequence_number=s, timestamp_utc=None, timestamp_quality="unsynced", device_uptime_ms=s * 600_000,
                starter_height_mm=20 + s) for s in (3, 1, 2, 2)]
    hist = normalize_history(rows, "A")
    assert [o.sequence_number for o in hist] == [1, 2, 3]
    assert [o.derived["elapsed_h"] for o in hist] == pytest.approx([0.0, 1 / 6, 2 / 6], abs=1e-3)
    assert hist[0].derived["elapsed_basis"] == "uptime"
    summ = summarize_history(hist)
    assert summ["n"] == 3 and summ["ph_valid"] is False and "no pH" in summ["ph_reason"]


def test_history_staleness_applies_to_latest_only():
    rows = [rec(sequence_number=i, timestamp_utc=f"2026-10-31T1{i}:00:00Z") for i in range(3)]  # 10:00..12:00
    hist = normalize_history(rows, "A", NOW)
    assert q(hist[0], "starter_temperature") == "ok"  # history point remains usable as a time series
    assert q(hist[-1], "starter_temperature") == "stale"
    assert summarize_history(hist)["latest_stale"] is True


def test_compare_to_prediction_never_imputes():
    rows = [rec(sequence_number=i, timestamp_utc=f"2026-10-31T17:{28 + 10 * i:02d}:00Z", starter_temperature_c=25.5,
                temperature_setpoint_c=24.0, starter_height_mm=20.0 * (1 + 0.1 * i),
                ph=(5.1 if i == 3 else None), ph_method=("probe_sample" if i == 3 else None),
                diagnostics=({"ph_calibration_valid": True} if i == 3 else {})) for i in range(4)]
    hist = normalize_history(rows, "A", NOW)
    pred = {"time_h": [0, 0.5, 1.0], "temperature_c": [24.0, 24.0, 24.0], "ph": [5.2, 4.8, 4.3],
            "expansion_ratio": [1.0, 1.6, 2.2]}
    dev = compare_to_prediction(hist, pred)
    assert dev["comparable"] and dev["time_basis"] == "utc"
    assert dev["temperature_vs_prediction"]["bias"] == pytest.approx(1.5)
    assert "starter_not_following_planned_temperature" in dev["flags"]
    assert dev["ph"]["n"] == 1 and dev["ph"]["latest"] == pytest.approx(5.1 - 4.8, abs=1e-6)  # t = 0.5 h
    assert "acidifying_slower_than_predicted" in dev["flags"]
    assert dev["expansion"]["n"] == 4 and "rising_slower_than_predicted" in dev["flags"]
    # nothing was filled in: records without pH still have no pH
    assert [o.fields["ph"].value for o in hist[:3]] == [None, None, None]
    no_ph = compare_to_prediction(normalize_history(rows[:2], "A", NOW), pred)
    assert no_ph["ph"]["n"] == 0 and "no valid pH" in no_ph["ph"]["note"]
    assert compare_to_prediction([], pred)["comparable"] is False
    assert compare_to_prediction(hist, None)["comparable"] is False


def test_garbage_record_does_not_crash():
    o = normalize_observation({"chamber_id": None, "starter_temperature_c": float("nan"), "door_open": "yes"}, "A")
    assert q(o, "starter_temperature") == "invalid" and q(o, "door_open") == "invalid"
    assert normalize_history(None) == [] and normalize_history([None, 3]) == []
