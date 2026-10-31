"""Telemetry/ack payload shape vs. firmware_contract/telemetry_example.json and contract modes."""

import json
from pathlib import Path

import pytest

from simulated_devices.network_client import (
    V10_TELEMETRY_FIELDS,
    V11_TELEMETRY_EXTRAS,
    ContractState,
    mentions_v11_extra,
)

from .conftest import Mono, flat_response, make_device

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "firmware_contract" / "telemetry_example.json").read_text())


def test_v10_field_set_matches_frozen_example():
    assert set(V10_TELEMETRY_FIELDS) == set(EXAMPLE)


def test_v10_payload_has_exact_example_fields_and_validates():
    d = make_device("A")
    d.handle_desired_control(flat_response("c1", 1, 24.0))
    payload = ContractState("v1.0").shape_telemetry(d.build_telemetry())
    assert set(payload) == set(EXAMPLE)
    for k, v in EXAMPLE.items():  # same JSON types where both are non-null
        if v is not None and payload[k] is not None:
            assert isinstance(payload[k], type(v)) or (isinstance(v, float) and isinstance(payload[k], int)), k
    from fermentai.api.schemas import TelemetryIn

    TelemetryIn.model_validate(payload)
    assert payload["source"] == "simulated" and "simulated_device" in payload["quality_flags"]
    assert payload["peltier_mode"] in ("idle", "heat", "cool", "switching", "fault", "off")


def test_v11_payload_superset_and_provenance():
    d = make_device("A", has_ph_probe=True)
    full = ContractState("v1.1").shape_telemetry(d.build_telemetry())
    assert set(EXAMPLE) <= set(full)
    assert set(V11_TELEMETRY_EXTRAS) <= set(full)
    assert full["schema_version"] == "1.0"
    assert full["device_id"] == "fermentai-esp32-a"
    assert full["timestamp_utc"] is None and full["timestamp_quality"] == "unsynced"
    assert full["source"] == "simulated" and full["quality_flags"][0] == "simulated_device"
    assert set(full["diagnostics"]) == {"wifi_rssi_dbm", "free_heap_bytes", "firmware_version",
                                        "last_command_id", "sensor_health"}
    assert full["diagnostics"]["firmware_version"] == "virtual-0.1"
    assert full["field_provenance"] and set(full["field_provenance"].values()) == {"simulated"}
    assert "chamber_temperature_c" in full["field_provenance"] and "ph" in full["field_provenance"]
    assert 0.0 <= full["peltier_output_fraction"] <= 1.0
    assert full["ph"] is not None and full["ph_method"] == "probe_sample"
    seq1 = full["sequence_number"]
    assert d.build_telemetry()["sequence_number"] == seq1 + 1
    try:  # validates against the backend model if it already speaks v1.1
        from fermentai.api.schemas import TelemetryIn

        if "boot_id" in TelemetryIn.model_fields:
            TelemetryIn.model_validate(full)
    except ImportError:
        pass


def test_synced_clock_flag():
    d = make_device("A", synced_clock=True)
    t = d.build_telemetry()
    assert t["timestamp_utc"] and t["timestamp_utc"].endswith("Z") and t["timestamp_quality"] == "synced"


def test_manual_ph_provenance_and_chamber_b_has_no_ph():
    a = make_device("A", manual_ph=4.12)
    t = a.build_telemetry()
    assert t["ph"] == 4.12 and t["ph_method"] == "manual"
    assert t["field_provenance"]["ph"] == "manual"
    assert t["field_provenance"]["chamber_temperature_c"] == "simulated"
    assert a.build_telemetry()["ph"] is None            # one-shot reading
    from simulated_devices import chamber_B

    b = make_device("B", has_ph_probe=False)
    cfg = chamber_B.config(has_ph_probe=True)            # B forces it off
    assert cfg.has_ph_probe is False
    tb = b.build_telemetry()
    assert tb["ph"] is None and "ph" not in tb["field_provenance"]
    assert tb["device_id"] == "fermentai-esp32-b"


def test_ack_shapes():
    full = {"schema_version": "1.0", "command_id": "x", "chamber_id": "A", "status": "completed",
            "sequence_number": 1, "timestamp_utc": None, "applied_setpoint_c": None,
            "message": None, "detail": "ran 3.0 s", "device_id": "fermentai-esp32-a",
            "device_uptime_ms": 1234, "result": {"ran_ms": 3000}}
    v10 = ContractState("v1.0").shape_ack(full)
    assert set(v10) == {"schema_version", "command_id", "chamber_id", "status", "sequence_number",
                        "timestamp_utc", "applied_setpoint_c", "message"}
    assert v10["message"] == "ran 3.0 s"
    v11 = ContractState("v1.1").shape_ack(full)
    assert v11["device_id"] == "fermentai-esp32-a" and v11["device_uptime_ms"] == 1234
    assert v11["detail"] == "ran 3.0 s"
    from fermentai.api.schemas import ControlAckIn

    ControlAckIn.model_validate(v10)


def test_fallback_detection_and_reprobe():
    body = {"detail": [{"type": "extra_forbidden", "loc": ["body", "boot_id"],
                        "msg": "Extra inputs are not permitted", "input": "x"}]}
    assert mentions_v11_extra(body) == "boot_id"
    assert mentions_v11_extra({"detail": [{"type": "missing", "loc": ["body", "source"]}]}) is None
    clock = Mono(0.0)
    cs = ContractState("v1.1", reprobe_s=100, clock=clock)
    assert cs.handle_422(body) is True and cs.current() == "v1.0"
    clock.t = 101
    assert cs.current() == "v1.1"
    strict = ContractState("v1.1", fallback=False)
    assert strict.handle_422(body) is False and strict.current() == "v1.1"
    with pytest.raises(ValueError):
        ContractState("v2")
