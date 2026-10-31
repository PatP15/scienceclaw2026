"""firmware_contract/*.json and contracts/*.schema.json must match the pydantic models, and every
fixture (incl. the verbatim PLAN_MASTER §4/§13 examples) must validate against the JSON schemas."""

import json

import pytest
from pydantic import TypeAdapter

from fermentai.api.contract import (
    CONTRACT_DIR,
    EXAMPLES_DIR,
    JSON_SCHEMA_DIALECT,
    PLAN_MASTER_COMMAND,
    PLAN_MASTER_TELEMETRY,
    SCHEMA_DIR,
    SCHEMAS,
    build_examples,
    check,
    check_all,
    contract_examples,
    validate,
)
from fermentai.api.schemas import (
    Command,
    ControlAckIn,
    ControlAckOut,
    DesiredControl,
    ImageOut,
    TelemetryAck,
    TelemetryIn,
)

MODELS = {
    "telemetry_example.json": TelemetryIn,
    "telemetry_v1_1_example.json": TelemetryIn,
    "telemetry_response_example.json": TelemetryAck,
    "command_example.json": DesiredControl,
    "command_no_motor_example.json": DesiredControl,
    "control_ack_example.json": ControlAckIn,
    "motor_ack_example.json": ControlAckIn,
    "control_ack_v1_1_example.json": ControlAckIn,
    "control_ack_response_example.json": ControlAckOut,
    "image_upload_response_example.json": ImageOut,
}
REQUIRED_SCHEMAS = ["telemetry_v1", "command_v1", "acknowledgment_v1", "agent_message_v1",
                    "experiment_v1", "sensory_feedback_v1", "simulation_result_v1"]


@pytest.mark.parametrize("name", sorted(MODELS))
def test_example_file_validates(name):
    data = json.loads((CONTRACT_DIR / name).read_text())
    MODELS[name].model_validate(data)


def test_examples_in_sync_with_models():
    assert set(build_examples()) == set(MODELS)
    assert check() == [], "run: uv run python -m fermentai.api.contract --write"


def test_contracts_dir_in_sync_and_fixtures_valid():
    assert check_all() == [], "run: uv run python -m fermentai.api.contract --write-schemas"


@pytest.mark.parametrize("stem", REQUIRED_SCHEMAS)
def test_required_schema_files(stem):
    schema = json.loads((SCHEMA_DIR / f"{stem}.schema.json").read_text())
    assert schema["$schema"] == JSON_SCHEMA_DIALECT
    assert schema["examples"] and not validate(schema, schema["examples"][0])
    assert (SCHEMA_DIR / "README.md").read_text().count(f"{stem}.schema.json") >= 1


def test_plan_master_fixtures_verbatim_and_valid():
    tel = json.loads((EXAMPLES_DIR / "telemetry_plan_master_s4.json").read_text())
    cmd = json.loads((EXAMPLES_DIR / "command_plan_master_s13.json").read_text())
    assert tel == PLAN_MASTER_TELEMETRY and cmd == PLAN_MASTER_COMMAND
    TelemetryIn.model_validate(tel)
    TypeAdapter(Command).validate_python(cmd)
    assert validate(json.loads((SCHEMA_DIR / "telemetry_v1.schema.json").read_text()), tel) == []
    assert validate(json.loads((SCHEMA_DIR / "command_v1.schema.json").read_text()), cmd) == []


@pytest.mark.parametrize("bad", [
    {"parameters": {"duration_ms": 10001}},          # motor bound
    {"action": "set_gpio"},                          # unknown action
    {"parameters": {"setpoint_c": 30}},              # setpoint smuggled into animate_motor
    {"schema_version": "2.0"},
])
def test_command_schema_rejects(bad):
    schema = json.loads((SCHEMA_DIR / "command_v1.schema.json").read_text())
    inst = {**PLAN_MASTER_COMMAND, **bad}
    assert validate(schema, inst), inst
    with pytest.raises(ValueError):
        TypeAdapter(Command).validate_python(inst)


def test_with_jsonschema_library_if_installed():
    jsonschema = pytest.importorskip("jsonschema")
    for stem in SCHEMAS:
        schema = json.loads((SCHEMA_DIR / f"{stem}.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        for _, inst in contract_examples()[stem]:
            jsonschema.Draft202012Validator(schema).validate(inst)


def test_command_example_respects_limits():
    cmd = json.loads((CONTRACT_DIR / "command_example.json").read_text())
    assert 4.0 <= cmd["temperature_setpoint_c"] <= 35.0
    assert cmd["motor"]["duration_s"] <= 10.0
    assert all(c["parameters"].get("duration_ms", 0) <= 10000 for c in cmd["commands"])


def test_readme_mentions_every_device_endpoint():
    text = (CONTRACT_DIR / "README_FOR_ESP32_AGENT.md").read_text()
    for ep in ("POST /api/v1/telemetry", "GET /api/v1/chambers/{chamber_id}/desired-control",
               "POST /api/v1/control-ack", "POST /api/v1/images", "X-Device-Id", "commands"):
        assert ep in text
