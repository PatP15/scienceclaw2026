"""Generate firmware_contract/*.json examples from the pydantic models (single source of truth).

    uv run python -m fermentai.api.contract --write    # regenerate files
    uv run python -m fermentai.api.contract --check    # exit 1 if files are out of date

All example values are ILLUSTRATIVE FIXTURES, not real sensor readings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import REPO_ROOT
from .schemas import (
    ControlAckIn,
    ControlAckOut,
    DesiredControl,
    ImageOut,
    MotorDirective,
    TelemetryAck,
    TelemetryIn,
)

CONTRACT_DIR = REPO_ROOT / "firmware_contract"


def build_examples() -> dict[str, dict]:
    telemetry = TelemetryIn.model_validate({
        "schema_version": "1.0",
        "chamber_id": "A",
        "experiment_id": "trial_001",
        "timestamp_utc": "2026-10-31T12:00:00Z",
        "chamber_temperature_c": 24.5,
        "starter_temperature_c": 23.9,
        "relative_humidity_percent": 72.0,
        "gas_resistance_ohms": 105432.0,
        "ph": None,
        "ph_method": None,
        "starter_height_mm": 28.0,
        "door_open": False,
        "peltier_mode": "idle",
        "temperature_setpoint_c": 24.0,
        "motor_running": False,
        "source": "simulated",
        "quality_flags": [],
        "device_id": "esp32-chamber-a",
        "firmware_version": "1.0.0",
        "uptime_s": 3600.0,
        "wifi_rssi_dbm": -58.0,
        "heatsink_temperature_c": 31.2,
        "fan_running": True,
        "peltier_duty_percent": 0.0,
        "last_command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
        "last_sequence_number": 41,
        "fault_codes": [],
    })
    telemetry_ack = TelemetryAck(
        telemetry_id=1234, chamber_id="A", received_at_utc="2026-10-31T12:00:01Z",
        server_time_utc="2026-10-31T12:00:01Z", quality_flags=[],
    )
    command = DesiredControl(
        command_id="6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
        sequence_number=42,
        server_epoch="3f9a1c2b7d4e",
        issued_at_utc="2026-10-31T12:00:00Z",
        expires_at_utc="2026-10-31T12:01:30Z",
        ttl_s=90,
        server_time_utc="2026-10-31T12:00:00Z",
        chamber_id="A",
        experiment_id="trial_001",
        temperature_setpoint_c=24.0,
        peltier_mode_hint="heat",
        motor=MotorDirective(
            command_id="b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f", action="run", run=True,
            duration_s=5.0, pattern="wiggle", sequence_number=7, ttl_s=60,
            expires_at_utc="2026-10-31T12:01:00Z",
        ),
        limits={"min_setpoint_c": 4.0, "max_setpoint_c": 35.0, "max_step_c_per_update": 3.0,
                "motor_max_duration_s": 10.0},
        poll_interval_s=5,
    )
    command_idle_motor = command.model_copy(update={"motor": MotorDirective(
        command_id=None, action="none", run=False, duration_s=0.0)})
    ack_control = ControlAckIn.model_validate({
        "schema_version": "1.0", "command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
        "chamber_id": "A", "status": "accepted", "sequence_number": 42,
        "timestamp_utc": "2026-10-31T12:00:02Z", "applied_setpoint_c": 24.0, "message": None,
    })
    ack_motor = ControlAckIn.model_validate({
        "schema_version": "1.0", "command_id": "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f",
        "chamber_id": "A", "status": "completed", "sequence_number": 7,
        "timestamp_utc": "2026-10-31T12:00:08Z", "applied_setpoint_c": None,
        "message": "ran 5.0 s",
    })
    ack_response = ControlAckOut(
        ok=True, command_id="6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10", command_type="control",
        command_status="accepted", late=False, server_time_utc="2026-10-31T12:00:02Z",
    )
    image_response = ImageOut(
        image_id=17, chamber_id="A", timestamp_utc="2026-10-31T12:00:00Z", url="/api/v1/images/17",
        bytes=48211, sha256="0" * 64,
    )
    return {
        "telemetry_example.json": telemetry.model_dump(mode="json"),
        "telemetry_response_example.json": telemetry_ack.model_dump(mode="json"),
        "command_example.json": command.model_dump(mode="json"),
        "command_no_motor_example.json": command_idle_motor.model_dump(mode="json"),
        "control_ack_example.json": ack_control.model_dump(mode="json"),
        "motor_ack_example.json": ack_motor.model_dump(mode="json"),
        "control_ack_response_example.json": ack_response.model_dump(mode="json"),
        "image_upload_response_example.json": image_response.model_dump(mode="json"),
    }


def _render(obj: dict) -> str:
    # timestamps: pydantic renders "Z" datetimes as "Z"; keep files stable and diffable.
    return json.dumps(obj, indent=2) + "\n"


def write(directory: Path = CONTRACT_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, obj in build_examples().items():
        p = directory / name
        p.write_text(_render(obj))
        out.append(p)
    return out


def check(directory: Path = CONTRACT_DIR) -> list[str]:
    stale = []
    for name, obj in build_examples().items():
        p = directory / name
        if not p.is_file() or p.read_text() != _render(obj):
            stale.append(name)
    return stale


if __name__ == "__main__":
    if "--write" in sys.argv:
        for p in write():
            print("wrote", p.relative_to(REPO_ROOT))
    else:
        stale = check()
        print("out of date: " + ", ".join(stale) if stale else "firmware_contract examples up to date")
        sys.exit(1 if stale else 0)
