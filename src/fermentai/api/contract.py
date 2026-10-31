"""Generate the shared contracts from the pydantic models (single source of truth).

    uv run python -m fermentai.api.contract --write           # firmware_contract/*.json examples
    uv run python -m fermentai.api.contract --write-schemas   # contracts/*.schema.json + contracts/examples/
    uv run python -m fermentai.api.contract --check           # exit 1 if anything is stale/invalid

All example values are ILLUSTRATIVE FIXTURES, not real sensor readings or model outputs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .config import REPO_ROOT
from .schemas import (
    CONTRACT_REVISION,
    AgentMessage,
    Command,
    ControlAckIn,
    ControlAckOut,
    DesiredControl,
    ExperimentIn,
    ImageOut,
    MotorDirective,
    SimulationResult,
    TasteFeedbackIn,
    TelemetryAck,
    TelemetryIn,
)

CONTRACT_DIR = REPO_ROOT / "firmware_contract"
SCHEMA_DIR = REPO_ROOT / "contracts"
EXAMPLES_DIR = SCHEMA_DIR / "examples"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# --------------------------------------------------------------------------- PLAN_MASTER fixtures
# Copied VERBATIM from PLAN_MASTER.md §4 and §13 (synthetic interface examples).
PLAN_MASTER_TELEMETRY = {
    "schema_version": "1.0",
    "device_id": "fermentai-esp32-a",
    "chamber_id": "A",
    "experiment_id": "experiment_001",
    "boot_id": "boot_a001",
    "sequence_number": 120,
    "timestamp_utc": None,
    "timestamp_quality": "unsynced",
    "device_uptime_ms": 120000,
    "chamber_temperature_c": 24.3,
    "starter_temperature_c": 23.9,
    "internal_heatsink_temperature_c": 31.2,
    "external_heatsink_temperature_c": 37.1,
    "relative_humidity_percent": 72.0,
    "pressure_pa": 101325.0,
    "gas_resistance_ohms": 105432.0,
    "ph": None,
    "ph_method": None,
    "starter_height_mm": None,
    "door_open": False,
    "peltier_mode": "heating",
    "peltier_output_fraction": 0.35,
    "temperature_setpoint_c": 25.0,
    "motor_running": False,
    "source": "simulated",
    "quality_flags": [],
}
PLAN_MASTER_COMMAND = {
    "schema_version": "1.0",
    "command_id": "cmd_001",
    "chamber_id": "A",
    "action": "animate_motor",
    "parameters": {"duration_ms": 3000},
    "issued_at_utc": "2026-10-31T15:00:00Z",
    "expires_at_utc": "2026-10-31T15:00:10Z",
}

_CMD = TypeAdapter(Command)


def _cmd(**kw) -> dict:
    base = {"schema_version": "1.0", "chamber_id": "A", "experiment_id": "experiment_001",
            "issued_at_utc": "2026-10-31T15:00:00Z", "expires_at_utc": "2026-10-31T15:01:30Z"}
    return _CMD.dump_python(_CMD.validate_python({**base, **kw}), mode="json")


def command_examples() -> dict[str, dict]:
    return {
        "set_setpoint": _cmd(command_id="6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10", action="set_setpoint",
                             parameters={"setpoint_c": 24.0, "control_mode": "auto"},
                             sequence_number=42),
        "animate_motor": _cmd(command_id="b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f",
                              action="animate_motor",
                              parameters={"duration_ms": 3000, "pattern": "wiggle"},
                              expires_at_utc="2026-10-31T15:01:00Z", sequence_number=43),
        "stop_motor": _cmd(command_id="c3d5e7f9-2b4c-4d6e-9f0a-1b2c3d4e5f60", action="stop_motor",
                           parameters={"target_command_id": "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f"},
                           expires_at_utc="2026-10-31T15:00:30Z", sequence_number=44),
        "capture_image": _cmd(command_id="d4e6f8a0-3c5d-4e7f-8a1b-2c3d4e5f6071", action="capture_image",
                              parameters={}, expires_at_utc="2026-10-31T15:01:00Z",
                              sequence_number=45),
    }


def telemetry_v11() -> dict:
    t = {**PLAN_MASTER_TELEMETRY,
         "source": "measured",
         "diagnostics": {"wifi_rssi_dbm": -58.0, "free_heap_bytes": 182344, "firmware_version": "1.1.0",
                         "last_command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
                         "sensor_health": {"bme680": "ok", "ds18b20_starter": "ok",
                                           "ds18b20_heatsink_internal": "ok",
                                           "ds18b20_heatsink_external": "ok", "ph": "missing",
                                           "camera": "ok"}},
         "field_provenance": {"chamber_temperature_c": "measured"},
         "quality_flags": ["illustrative_fixture"]}
    return TelemetryIn.model_validate(t).model_dump(mode="json", exclude_unset=True)


def ack_v11(status: str = "completed") -> dict:
    return ControlAckIn.model_validate({
        "command_id": "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f", "device_id": "fermentai-esp32-a",
        "chamber_id": "A", "status": status, "device_uptime_ms": 123456,
        "detail": "motor ran 3000 ms", "result": {"ran_ms": 3000},
    }).model_dump(mode="json", exclude_unset=True)


def desired_control_example(with_motor: bool = True) -> DesiredControl:
    cmds = command_examples()
    motor = (MotorDirective(command_id=cmds["animate_motor"]["command_id"], action="run", run=True,
                            duration_s=3.0, pattern="wiggle", sequence_number=43, ttl_s=60,
                            expires_at_utc="2026-10-31T15:01:00Z") if with_motor
             else MotorDirective(command_id=None, action="none", run=False, duration_s=0.0))
    return DesiredControl(
        command_id=cmds["set_setpoint"]["command_id"],
        sequence_number=42,
        server_epoch="3f9a1c2b7d4e",
        issued_at_utc="2026-10-31T15:00:00Z",
        expires_at_utc="2026-10-31T15:01:30Z",
        ttl_s=90,
        server_time_utc="2026-10-31T15:00:00Z",
        chamber_id="A",
        experiment_id="experiment_001",
        temperature_setpoint_c=24.0,
        control_mode="auto",
        peltier_mode_hint="heat",
        motor=motor,
        limits={"min_setpoint_c": 4.0, "max_setpoint_c": 35.0, "max_step_c_per_update": 3.0,
                "motor_max_duration_s": 10.0},
        poll_interval_s=5,
        commands=[cmds["set_setpoint"], cmds["animate_motor"]] if with_motor else [cmds["set_setpoint"]],
    )


# --------------------------------------------------------------------------- firmware_contract/
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
        "device_id": "fermentai-esp32-a",
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
    ack_control = ControlAckIn.model_validate({
        "schema_version": "1.0", "command_id": "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10",
        "chamber_id": "A", "status": "accepted", "sequence_number": 42,
        "timestamp_utc": "2026-10-31T12:00:02Z", "applied_setpoint_c": 24.0, "message": None,
    })
    ack_motor = ControlAckIn.model_validate({
        "schema_version": "1.0", "command_id": "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f",
        "chamber_id": "A", "status": "completed", "sequence_number": 43,
        "timestamp_utc": "2026-10-31T12:00:08Z", "applied_setpoint_c": None,
        "message": "ran 3.0 s",
    })
    ack_response = ControlAckOut(
        ok=True, command_id="6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10", command_type="control",
        action="set_setpoint", command_status="accepted", late=False,
        server_time_utc="2026-10-31T12:00:02Z",
    )
    image_response = ImageOut(
        image_id=17, chamber_id="A", timestamp_utc="2026-10-31T12:00:00Z", url="/api/v1/images/17",
        bytes=48211, sha256="0" * 64,
    )
    return {
        "telemetry_example.json": telemetry.model_dump(mode="json", exclude_unset=True),
        "telemetry_v1_1_example.json": telemetry_v11(),
        "telemetry_response_example.json": telemetry_ack.model_dump(mode="json"),
        "command_example.json": desired_control_example(True).model_dump(mode="json"),
        "command_no_motor_example.json": desired_control_example(False).model_dump(mode="json"),
        "control_ack_example.json": ack_control.model_dump(mode="json"),
        "motor_ack_example.json": ack_motor.model_dump(mode="json"),
        "control_ack_v1_1_example.json": ack_v11(),
        "control_ack_response_example.json": ack_response.model_dump(mode="json"),
        "image_upload_response_example.json": image_response.model_dump(mode="json"),
    }


# --------------------------------------------------------------------------- contracts/
SCHEMAS: dict[str, tuple[Any, str, str]] = {
    # file stem: (type, json-schema mode, title)
    "telemetry_v1": (TelemetryIn, "validation", "FermentAI telemetry (device -> POST /api/v1/telemetry)"),
    "command_v1": (Command, "serialization",
                   "FermentAI device command (GET /api/v1/chambers/{id}/desired-control -> commands[])"),
    "acknowledgment_v1": (ControlAckIn, "validation", "FermentAI command acknowledgment (POST /api/v1/control-ack)"),
    "agent_message_v1": (AgentMessage, "validation", "FermentAI agent-to-agent message / finding"),
    "experiment_v1": (ExperimentIn, "validation", "FermentAI experiment definition (POST /api/v1/experiments)"),
    "sensory_feedback_v1": (TasteFeedbackIn, "validation", "FermentAI sensory feedback (POST /api/v1/taste-feedback)"),
    "simulation_result_v1": (SimulationResult, "serialization",
                             "FermentAI simulation result (POST /api/v1/simulations/run, GET /simulations/{id})"),
    "desired_control_v1": (DesiredControl, "serialization",
                           "FermentAI desired-control envelope (GET /api/v1/chambers/{id}/desired-control)"),
}


def contract_examples() -> dict[str, list[tuple[str, dict]]]:
    """schema stem -> [(example file name, instance)]"""
    cmds = command_examples()
    return {
        "telemetry_v1": [("telemetry_v1.example.json", telemetry_v11()),
                         ("telemetry_plan_master_s4.json", PLAN_MASTER_TELEMETRY),
                         ("telemetry_v1_0_legacy.example.json", build_examples()["telemetry_example.json"])],
        "command_v1": [*((f"command_v1.{k}.example.json", v) for k, v in cmds.items()),
                       ("command_plan_master_s13.json", PLAN_MASTER_COMMAND)],
        "acknowledgment_v1": [("acknowledgment_v1.example.json", ack_v11()),
                              ("acknowledgment_v1_0_legacy.example.json",
                               build_examples()["control_ack_example.json"])],
        "agent_message_v1": [("agent_message_v1.example.json", AgentMessage.model_validate({
            "id": "f_0123456789abcdef", "agent_id": "scientist_A", "timestamp_utc": "2026-10-31T15:00:00Z",
            "type": "hypothesis", "content": "[scientist_A -> scientist_B] Illustrative hypothesis text.",
            "payload": {"decision_source": "offline_policy"}, "evidence_source": "simulated",
            "in_reply_to": None, "chamber_id": "A", "cycle_id": "cycle_example"}).model_dump(mode="json"))],
        "experiment_v1": [("experiment_v1.example.json", ExperimentIn.model_validate({
            "experiment_id": "exp_example_001", "title": "Illustrative warm-finish trial",
            "hypothesis": "A warmer late phase raises acidity without losing rise.",
            "schedules": {"A": [{"t_h": 0, "setpoint_c": 22.0}, {"t_h": 8, "setpoint_c": 25.0}],
                          "B": [[0, 27.0], [6, 24.0]]},
            "status": "proposed", "proposed_by": "scientist_A", "provenance": "offline_policy",
        }).model_dump(mode="json"))],
        "sensory_feedback_v1": [("sensory_feedback_v1.example.json", TasteFeedbackIn.model_validate({
            "text": "Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, but "
                    "the crumb is too dense. I'd like a more sour loaf that's still fluffy.",
            "judge_name": "Judge 1", "mode": "offline"}).model_dump(mode="json", exclude_unset=True))],
        "simulation_result_v1": [("simulation_result_v1.example.json", SimulationResult.model_validate({
            "simulation_id": 1, "status": "ok",
            "schedule": {"breakpoints": [[0.0, 27.0], [6.0, 24.0]], "mode": "piecewise_constant"},
            "result": {"summary": {"provenance": "simulated", "note": "illustrative contract fixture"},
                       "timeseries": {"columns": ["time_h", "ph"], "data": {"time_h": [0.0, 0.05],
                                                                            "ph": [None, None]}},
                       "attrs": {"note": "illustrative contract fixture, NOT model output"}},
        }).model_dump(mode="json"))],
        "desired_control_v1": [("desired_control_v1.example.json",
                                desired_control_example(True).model_dump(mode="json"))],
    }


def build_schema(stem: str) -> dict:
    tp, mode, title = SCHEMAS[stem]
    schema = TypeAdapter(tp).json_schema(mode=mode)
    first_example = contract_examples()[stem][0][1]
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"https://fermentai.local/contracts/{stem}.schema.json",
        "title": title,
        "x-contract-revision": CONTRACT_REVISION,
        "x-generated-by": "uv run python -m fermentai.api.contract --write-schemas "
                          "(source: src/fermentai/api/schemas.py)",
        **{k: v for k, v in schema.items() if k != "title"},
        "examples": [first_example],
    }


def _render(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _targets() -> dict[Path, str]:
    out = {CONTRACT_DIR / name: _render(obj) for name, obj in build_examples().items()}
    for stem in SCHEMAS:
        out[SCHEMA_DIR / f"{stem}.schema.json"] = _render(build_schema(stem))
        for name, inst in contract_examples()[stem]:
            out[EXAMPLES_DIR / name] = _render(inst)
    return out


def write(schemas: bool = False) -> list[Path]:
    written = []
    for p, text in _targets().items():
        if not schemas and p.parent != CONTRACT_DIR:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        written.append(p)
    return written


def check(directory: Path = CONTRACT_DIR) -> list[str]:
    """Names of stale firmware_contract examples (kept for the v1.0 test)."""
    return [name for name, obj in build_examples().items()
            if not (directory / name).is_file() or (directory / name).read_text() != _render(obj)]


def check_all() -> list[str]:
    problems = [str(p.relative_to(REPO_ROOT)) for p, text in _targets().items()
                if not p.is_file() or p.read_text() != text]
    for stem in SCHEMAS:
        schema = build_schema(stem)
        for name, inst in contract_examples()[stem]:
            problems += [f"{name}: {e}" for e in validate(schema, inst)]
    return problems


# --------------------------------------------------------------------------- mini validator
# A small JSON-Schema 2020-12 validator for the keyword subset pydantic emits, so fixtures are
# checked against the *generated JSON schema* (not just the pydantic model) without adding a
# dependency. tests/ also use `jsonschema` when it is installed.
_FMT_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def validate(schema: dict, inst: Any, root: dict | None = None, path: str = "$") -> list[str]:
    root = root or schema
    errs: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/$defs/"), ref
        errs += validate(root["$defs"][ref.split("/")[-1]], inst, root, path)
    if "anyOf" in schema and not any(not validate(s, inst, root, path) for s in schema["anyOf"]):
        errs.append(f"{path}: matches no anyOf branch")
    if "oneOf" in schema:
        n = sum(1 for s in schema["oneOf"] if not validate(s, inst, root, path))
        if n != 1:
            errs.append(f"{path}: matches {n} oneOf branches (need exactly 1)")
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: {inst!r} != const {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = any({
            "object": isinstance(inst, dict), "array": isinstance(inst, list),
            "string": isinstance(inst, str), "null": inst is None,
            "boolean": isinstance(inst, bool),
            "integer": isinstance(inst, int) and not isinstance(inst, bool),
            "number": isinstance(inst, (int, float)) and not isinstance(inst, bool),
        }[x] for x in types)
        if not ok:
            return errs + [f"{path}: expected {t}, got {type(inst).__name__}"]
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in inst:
                errs.append(f"{path}: missing required {r!r}")
        for k, v in inst.items():
            if k in props:
                errs += validate(props[k], v, root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected property {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += validate(schema["additionalProperties"], v, root, f"{path}.{k}")
    if isinstance(inst, list):
        prefix = schema.get("prefixItems", [])
        for i, v in enumerate(inst):
            if i < len(prefix):
                errs += validate(prefix[i], v, root, f"{path}[{i}]")
            elif isinstance(schema.get("items"), dict):
                errs += validate(schema["items"], v, root, f"{path}[{i}]")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        for kw, bad in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                        ("exclusiveMinimum", lambda a, b: a <= b), ("exclusiveMaximum", lambda a, b: a >= b)):
            if kw in schema and bad(inst, schema[kw]):
                errs.append(f"{path}: {inst} violates {kw} {schema[kw]}")
    if isinstance(inst, str):
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: does not match {schema['pattern']}")
        if schema.get("format") == "date-time" and not _FMT_DT.match(inst):
            errs.append(f"{path}: not an RFC 3339 date-time")
    return errs


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--write" in args or "--write-schemas" in args:
        for p in write(schemas="--write-schemas" in args):
            print("wrote", p.relative_to(REPO_ROOT))
    if "--check" in args or not args:
        problems = check_all()
        print("\n".join(problems) if problems else "contracts + firmware_contract examples up to date")
        sys.exit(1 if problems else 0)
