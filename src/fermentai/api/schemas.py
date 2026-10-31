"""Pydantic models for the FermentAI REST API and the shared contracts (v1.0, extended to v1.1).

These models are the single source of truth for:
  * firmware_contract/*.json examples   (`python -m fermentai.api.contract --write`)
  * contracts/*.schema.json (JSON Schema 2020-12) (`python -m fermentai.api.contract --write-schemas`)
Both are checked by tests/test_api_contract.py.

Contract v1.1 is strictly ADDITIVE on top of v1.0: every v1.0 payload remains valid and
`schema_version` stays "1.0" on the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
CONTRACT_REVISION = "1.1"

ChamberId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,16}$")]
# str + custom validator (clear 422 message) but published as const "1.0" in the JSON schemas.
SchemaVersion = Annotated[str, Field(json_schema_extra={"const": "1.0"})]
ShortStr = Annotated[str, StringConstraints(max_length=64)]
# Record-level provenance of telemetry. "measured" is canonical for physical devices (v1.1);
# "real" is the v1.0 spelling and is accepted as an alias (stored as "measured").
TelemetrySource = Literal["measured", "real", "simulated", "replay"]
# Per-field provenance (v1.1), e.g. manual pH inside an otherwise measured record.
FieldProvenance = Literal["measured", "simulated", "replay", "manual", "estimated"]
SensorHealth = Literal["ok", "missing", "stale", "invalid", "uncalibrated"]
TimestampQuality = Literal["synced", "unsynced", "estimated"]
# v1.0 short forms + v1.1 long forms ("heating"/"cooling" as in PLAN_MASTER §4).
PeltierMode = Literal["idle", "heat", "cool", "heating", "cooling", "switching", "fault", "off"]
PeltierHint = Literal["heat", "cool", "hold", "idle", "unknown"]
MotorPattern = Literal["wiggle", "nod", "spin"]
ControlMode = Literal["auto", "hold", "idle"]
CommandAction = Literal["set_setpoint", "animate_motor", "stop_motor", "capture_image", "noop"]
# v1.0 statuses + v1.1 statuses ("cancelled" is v1.0-only but still accepted).
AckStatus = Literal["accepted", "rejected", "completed", "failed", "cancelled", "expired"]
MOTOR_MAX_DURATION_MS = 10_000


def _check_schema_version(v: str) -> str:
    if v not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported schema_version {v!r}; this server accepts "
            f"{', '.join(SUPPORTED_SCHEMA_VERSIONS)} "
            "(see contracts/README.md and firmware_contract/README_FOR_ESP32_AGENT.md)"
        )
    return v


class _Strict(BaseModel):
    """Unknown fields are rejected (422) so firmware typos surface immediately."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# ============================================================================ device -> server


class Diagnostics(BaseModel):
    """v1.1 optional device diagnostics. Extra keys are allowed here (diagnostic-only)."""

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    wifi_rssi_dbm: float | None = None
    free_heap_bytes: int | None = Field(default=None, ge=0)
    firmware_version: ShortStr | None = None
    last_command_id: ShortStr | None = None
    sensor_health: dict[ShortStr, SensorHealth] = Field(
        default_factory=dict,
        description="sensor name (e.g. bme680, ds18b20_starter, ph) or telemetry field -> health",
    )


class TelemetryIn(_Strict):
    schema_version: SchemaVersion = Field(description='Must be "1.0".')
    chamber_id: ChamberId
    experiment_id: ShortStr | None = None
    timestamp_utc: datetime | None = Field(
        default=None,
        description="Device wall-clock time (ISO-8601, Z). null until the device clock is synced; "
        "the server never invents it (it stores received_at_utc separately).",
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
    source: TelemetrySource = Field(
        description='Record provenance: "measured" for physical devices ("real" accepted as alias).'
    )
    quality_flags: list[ShortStr] = Field(default_factory=list)
    # ---- v1.0 optional diagnostics ----
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
    # ---- v1.1 optional fields (PLAN_MASTER §4) ----
    boot_id: ShortStr | None = Field(default=None, description="Random id generated at each boot.")
    sequence_number: int | None = Field(
        default=None, ge=0,
        description="Monotonic per boot. (device_id, boot_id, sequence_number) deduplicates retries.",
    )
    timestamp_quality: TimestampQuality | None = None
    device_uptime_ms: int | None = Field(default=None, ge=0)
    internal_heatsink_temperature_c: float | None = None
    external_heatsink_temperature_c: float | None = None
    pressure_pa: float | None = None
    peltier_output_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    diagnostics: Diagnostics | None = None
    field_provenance: dict[ShortStr, FieldProvenance] = Field(
        default_factory=dict, description="telemetry field name -> provenance of that one value"
    )

    _v = field_validator("schema_version")(_check_schema_version)


class ControlAckIn(_Strict):
    """Accepts the v1.0 shape (schema_version, message, applied_setpoint_c, ...) and the v1.1
    shape (device_id, device_uptime_ms, detail, result)."""

    schema_version: SchemaVersion = "1.0"
    command_id: ShortStr
    chamber_id: ChamberId
    status: AckStatus
    device_id: ShortStr | None = None
    device_uptime_ms: int | None = Field(default=None, ge=0)
    detail: Annotated[str, StringConstraints(max_length=300)] | None = None
    result: dict[str, Any] | None = Field(
        default=None, description='e.g. {"applied_setpoint_c": 24.0} or {"ran_ms": 3000}'
    )
    # v1.0 fields
    sequence_number: int | None = None
    timestamp_utc: datetime | None = None
    applied_setpoint_c: float | None = Field(
        default=None, description="Control acks: setpoint actually applied after local limits."
    )
    message: Annotated[str, StringConstraints(max_length=300)] | None = None

    _v = field_validator("schema_version")(_check_schema_version)


# ============================================================================ commands (v1.1)


class SetSetpointParams(_Strict):
    setpoint_c: float
    control_mode: ControlMode = "auto"


class AnimateMotorParams(_Strict):
    duration_ms: int = Field(gt=0, le=MOTOR_MAX_DURATION_MS)
    pattern: MotorPattern = "wiggle"


class StopMotorParams(_Strict):
    target_command_id: ShortStr | None = Field(
        default=None, description="animate_motor command being cancelled (if any)."
    )


class CaptureImageParams(_Strict):
    pass


class NoopParams(_Strict):
    pass


class _CommandBase(_Strict):
    schema_version: SchemaVersion = SCHEMA_VERSION
    command_id: ShortStr
    chamber_id: ChamberId
    experiment_id: ShortStr | None = None
    issued_at_utc: str
    expires_at_utc: str
    sequence_number: int | None = Field(
        default=None, description="Monotonic per chamber across ALL actions; process ascending."
    )

    _v = field_validator("schema_version")(_check_schema_version)


class SetSetpointCommand(_CommandBase):
    action: Literal["set_setpoint"]
    parameters: SetSetpointParams


class AnimateMotorCommand(_CommandBase):
    action: Literal["animate_motor"]
    parameters: AnimateMotorParams


class StopMotorCommand(_CommandBase):
    action: Literal["stop_motor"]
    parameters: StopMotorParams = Field(default_factory=StopMotorParams)


class CaptureImageCommand(_CommandBase):
    action: Literal["capture_image"]
    parameters: CaptureImageParams = Field(default_factory=CaptureImageParams)


class NoopCommand(_CommandBase):
    action: Literal["noop"]
    parameters: NoopParams = Field(default_factory=NoopParams)


Command = Annotated[
    SetSetpointCommand | AnimateMotorCommand | StopMotorCommand | CaptureImageCommand | NoopCommand,
    Field(discriminator="action"),
]


# ============================================================================ server -> device


class TelemetryAck(BaseModel):
    ok: bool = True
    telemetry_id: int
    chamber_id: str
    received_at_utc: str
    server_time_utc: str
    quality_flags: list[str]
    duplicate: bool = Field(default=False, description="v1.1: already stored (retry); not re-stored.")


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
    schema_version: SchemaVersion = SCHEMA_VERSION
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
    control_mode: ControlMode = "auto"
    peltier_mode_hint: PeltierHint
    motor: MotorDirective
    limits: dict[str, float] = Field(description="Informational; device enforces its own.")
    poll_interval_s: int
    commands: list[Command] = Field(
        default_factory=list,
        description="v1.1: pending (not yet acked, unexpired) commands, ascending sequence_number.",
    )


class ControlAckOut(BaseModel):
    ok: bool
    command_id: str
    command_type: str
    action: str | None = None
    command_status: str
    late: bool
    server_time_utc: str


# ============================================================================ dashboard / agents


class MotorCommandIn(_Strict):
    """POST /chambers/{id}/motor. extra=forbid: setpoint/Peltier fields are rejected (422)."""

    duration_s: float | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    pattern: MotorPattern = "wiggle"
    reason: Annotated[str, StringConstraints(max_length=200)] = "operator"
    source: ShortStr = "dashboard"

    @model_validator(mode="after")
    def _one_duration(self):
        if self.duration_s is not None and self.duration_ms is not None:
            raise ValueError("give duration_s or duration_ms, not both")
        return self


class SetpointIn(_Strict):
    setpoint_c: float
    source: ShortStr = "operator"
    rationale: Annotated[str, StringConstraints(max_length=1000)] = ""
    control_mode: ControlMode | None = Field(default=None, description="None = leave unchanged.")


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
    control_mode: ControlMode = "auto"


def _step_from_pair(v: Any) -> Any:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return {"t_h": v[0], "setpoint_c": v[1]}
    return v


def _steps_from_obj(v: Any) -> Any:
    if isinstance(v, dict):  # pipeline style {"points": [[t, T], ...], "name": ...}
        return v.get("points") or v.get("steps") or []
    return v


class ScheduleStep(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    t_h: float = Field(ge=0, description="Hours from experiment start.")
    setpoint_c: float


ChamberSchedule = Annotated[
    list[Annotated[ScheduleStep, BeforeValidator(_step_from_pair)]],
    BeforeValidator(_steps_from_obj),
]


class ExperimentIn(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: SchemaVersion = SCHEMA_VERSION
    experiment_id: ShortStr | None = None
    title: Annotated[str, StringConstraints(max_length=200)] = "Untitled experiment"
    hypothesis: str | None = None
    objective: str | None = None
    schedules: dict[str, ChamberSchedule] = Field(
        default_factory=dict,
        description='Chamber id -> steps, e.g. {"A": [{"t_h":0,"setpoint_c":22}]}; '
        '[t_h, setpoint_c] pairs and {"points": [...]} objects are accepted.',
    )
    status: Literal["proposed", "approved", "running", "completed", "abandoned"] = "proposed"
    proposed_by: ShortStr = "operator"
    provenance: ShortStr = "imported_human"

    _v = field_validator("schema_version")(_check_schema_version)


class TasteFeedbackIn(_Strict):
    schema_version: SchemaVersion = SCHEMA_VERSION
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    loaf_a_notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    loaf_b_notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    judge_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    mode: Literal["live", "offline"] = "offline"
    apply_schedule: bool | None = Field(
        default=None, description="Override hardware.yaml taste_feedback.apply_first_schedule_step."
    )
    trigger_motors: bool = True

    _v = field_validator("schema_version")(_check_schema_version)


FindingType = Literal["observation", "hypothesis", "prediction", "uncertainty",
                      "proposed_measurement", "proposed_intervention", "critique", "rationale"]
EvidenceSource = Literal["real", "simulated", "replay", "estimated", "imported_human", "judge_feedback"]


class AgentMessage(_Strict):
    """Structured agent-to-agent message / finding (mirrors fermentai.agents.communication.Finding)."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    id: ShortStr
    agent_id: ShortStr = Field(description="e.g. scientist_A")
    timestamp_utc: str
    type: FindingType
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_source: EvidenceSource
    in_reply_to: ShortStr | None = None
    chamber_id: ChamberId | None = None
    cycle_id: ShortStr | None = None
    to_agent_id: ShortStr | None = Field(default=None, description="Optional explicit recipient.")

    _v = field_validator("schema_version")(_check_schema_version)


class ChatIn(_Strict):
    """POST /api/v1/chat: one guest message to the kitchen brigade (Sous Chef A/B, Head Chef)."""

    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    mode: Literal["live", "offline"] = "offline"
    sous_chef: Literal["A", "B"] | None = None
    judge_name: Annotated[str, StringConstraints(max_length=100)] | None = None


class SimulationRunIn(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    chamber_id: ChamberId | None = None
    label: ShortStr | None = None
    schedule: list[ScheduleStep] | None = None
    duration_h: float | None = Field(default=None, gt=0, le=72)
    options: dict[str, Any] = Field(default_factory=dict, description="Passed to simulate().")


class SimulationSchedule(BaseModel):
    breakpoints: list[tuple[float, float]] = Field(description="[[t_h, temp_c], ...]")
    mode: Literal["piecewise_constant", "piecewise_linear"] = "piecewise_constant"


class SimulationTimeseries(BaseModel):
    columns: list[str]
    data: dict[str, list[Any]]


class SimulationPayload(BaseModel):
    summary: dict[str, Any] | None = None
    timeseries: SimulationTimeseries | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class SimulationResult(BaseModel):
    """Response of POST /simulations/run. MODEL OUTPUT ONLY (provenance = simulated)."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    simulation_id: int
    status: Literal["ok", "error"]
    provenance: Literal["simulated"] = "simulated"
    schedule: SimulationSchedule
    result: SimulationPayload


class ImageOut(BaseModel):
    ok: bool = True
    image_id: int
    chamber_id: str
    timestamp_utc: str
    url: str
    bytes: int
    sha256: str
