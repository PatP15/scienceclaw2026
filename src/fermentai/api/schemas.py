"""Pydantic models for the FermentAI REST API and the frozen ESP32 firmware contract v1.0.

The JSON files in firmware_contract/ are generated from these models
(`uv run python -m fermentai.api.contract --write`) and checked by tests/test_api_contract.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = ("1.0",)

ChamberId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,16}$")]
ShortStr = Annotated[str, StringConstraints(max_length=64)]
# Provenance of a telemetry record. PLAN.md section 18 / AGENTS.md.
TelemetrySource = Literal["real", "simulated", "replay"]
PeltierMode = Literal["idle", "heat", "cool", "switching", "fault", "off"]
PeltierHint = Literal["heat", "cool", "hold", "idle", "unknown"]
MotorPattern = Literal["wiggle", "nod", "spin"]
AckStatus = Literal["accepted", "rejected", "completed", "failed", "cancelled", "expired"]


def _check_schema_version(v: str) -> str:
    if v not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported schema_version {v!r}; this server accepts "
            f"{', '.join(SUPPORTED_SCHEMA_VERSIONS)} "
            "(see firmware_contract/README_FOR_ESP32_AGENT.md)"
        )
    return v


class _Strict(BaseModel):
    """Unknown fields are rejected (422) so firmware typos surface immediately."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# ============================================================================ device -> server


class TelemetryIn(_Strict):
    schema_version: str = Field(description='Must be "1.0".')
    chamber_id: ChamberId
    experiment_id: ShortStr | None = None
    timestamp_utc: datetime | None = Field(
        default=None, description="Device time (ISO-8601, Z). Null if the device has no clock."
    )
    chamber_temperature_c: float | None = None
    starter_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    gas_resistance_ohms: float | None = Field(
        default=None, description="BME680 raw gas resistance. NOT a CO2 measurement."
    )
    ph: float | None = None
    ph_method: ShortStr | None = Field(
        default=None, description="e.g. probe_immersed | probe_sample | strip | manual"
    )
    starter_height_mm: float | None = None
    door_open: bool | None = None
    peltier_mode: PeltierMode | None = None
    temperature_setpoint_c: float | None = Field(
        default=None, description="Setpoint the device is ACTUALLY using (after local limits)."
    )
    motor_running: bool | None = None
    source: TelemetrySource
    quality_flags: list[ShortStr] = Field(default_factory=list)
    # ---- optional diagnostics (all nullable; part of v1.0) ----
    device_id: ShortStr | None = None
    firmware_version: ShortStr | None = None
    uptime_s: float | None = None
    wifi_rssi_dbm: float | None = None
    heatsink_temperature_c: float | None = None
    fan_running: bool | None = None
    peltier_duty_percent: float | None = None
    last_command_id: ShortStr | None = None
    last_sequence_number: int | None = None
    fault_codes: list[ShortStr] = Field(default_factory=list)

    _v = field_validator("schema_version")(_check_schema_version)


class ControlAckIn(_Strict):
    schema_version: str
    command_id: ShortStr
    chamber_id: ChamberId
    status: AckStatus
    sequence_number: int | None = None
    timestamp_utc: datetime | None = None
    applied_setpoint_c: float | None = Field(
        default=None, description="Control acks: setpoint actually applied after local limits."
    )
    message: Annotated[str, StringConstraints(max_length=300)] | None = None

    _v = field_validator("schema_version")(_check_schema_version)


# ============================================================================ server -> device


class TelemetryAck(BaseModel):
    ok: bool = True
    telemetry_id: int
    chamber_id: str
    received_at_utc: str
    server_time_utc: str
    quality_flags: list[str]


class MotorDirective(BaseModel):
    """Decorative chef-hat motor. Separate command type; never affects the Peltier."""

    command_id: str | None = Field(description="Ack this id separately; null when action=none.")
    action: Literal["run", "stop", "none"]
    run: bool
    duration_s: float = Field(ge=0, description="<= motor max_duration_s (10 s).")
    pattern: MotorPattern | None = None
    sequence_number: int | None = None
    ttl_s: int | None = Field(default=None, description="Seconds of validity from receipt.")
    expires_at_utc: str | None = None


class DesiredControl(BaseModel):
    schema_version: str = SCHEMA_VERSION
    command_type: Literal["control"] = "control"
    command_id: str
    sequence_number: int
    server_epoch: str = Field(description="Changes only if the server DB is reset.")
    issued_at_utc: str
    expires_at_utc: str
    ttl_s: int = Field(description="Remaining validity in seconds, measured from receipt.")
    server_time_utc: str
    chamber_id: str
    experiment_id: str | None = None
    temperature_setpoint_c: float
    peltier_mode_hint: PeltierHint
    motor: MotorDirective
    limits: dict[str, float] = Field(description="Informational; device enforces its own.")
    poll_interval_s: int


class ControlAckOut(BaseModel):
    ok: bool
    command_id: str
    command_type: str
    command_status: str
    late: bool
    server_time_utc: str


# ============================================================================ dashboard / agents


class MotorCommandIn(_Strict):
    """POST /chambers/{id}/motor. extra=forbid: setpoint/Peltier fields are rejected (422)."""

    duration_s: float | None = Field(default=None, gt=0)
    pattern: MotorPattern = "wiggle"
    reason: Annotated[str, StringConstraints(max_length=200)] = "operator"
    source: ShortStr = "dashboard"


class SetpointIn(_Strict):
    setpoint_c: float
    source: ShortStr = "operator"
    rationale: Annotated[str, StringConstraints(max_length=1000)] = ""


class SetpointResult(BaseModel):
    chamber_id: str
    requested_c: float | None
    previous_c: float
    applied_c: float
    changed: bool
    clamped: bool
    step_limited: bool
    rejected: bool
    reason: str
    source: str
    rationale: str
    at_utc: str


class ScheduleStep(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    t_h: float = Field(ge=0, description="Hours from experiment start.")
    setpoint_c: float


class ExperimentIn(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    experiment_id: ShortStr | None = None
    title: Annotated[str, StringConstraints(max_length=200)] = "Untitled experiment"
    hypothesis: str | None = None
    objective: str | None = None
    schedules: dict[str, list[ScheduleStep]] = Field(
        default_factory=dict, description='Chamber id -> steps, e.g. {"A": [{"t_h":0,"setpoint_c":22}]}'
    )
    status: Literal["proposed", "approved", "running", "completed", "abandoned"] = "proposed"
    proposed_by: ShortStr = "operator"
    provenance: ShortStr = "imported_human"


class TasteFeedbackIn(_Strict):
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    loaf_a_notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    loaf_b_notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    judge_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    mode: Literal["live", "offline"] = "offline"
    apply_schedule: bool | None = Field(
        default=None, description="Override hardware.yaml taste_feedback.apply_first_schedule_step."
    )
    trigger_motors: bool = True


class SimulationRunIn(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    chamber_id: ChamberId | None = None
    label: ShortStr | None = None
    schedule: list[ScheduleStep] | None = None
    duration_h: float | None = Field(default=None, gt=0, le=72)
    options: dict[str, Any] = Field(default_factory=dict, description="Passed to simulate().")


class ImageOut(BaseModel):
    ok: bool = True
    image_id: int
    chamber_id: str
    timestamp_utc: str
    url: str
    bytes: int
    sha256: str
