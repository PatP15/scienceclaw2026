"""firmware_contract/*.json must validate against the models and match the generator."""

import json

import pytest

from fermentai.api.contract import CONTRACT_DIR, build_examples, check
from fermentai.api.schemas import (
    ControlAckIn,
    ControlAckOut,
    DesiredControl,
    ImageOut,
    TelemetryAck,
    TelemetryIn,
)

MODELS = {
    "telemetry_example.json": TelemetryIn,
    "telemetry_response_example.json": TelemetryAck,
    "command_example.json": DesiredControl,
    "command_no_motor_example.json": DesiredControl,
    "control_ack_example.json": ControlAckIn,
    "motor_ack_example.json": ControlAckIn,
    "control_ack_response_example.json": ControlAckOut,
    "image_upload_response_example.json": ImageOut,
}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_example_file_validates(name):
    data = json.loads((CONTRACT_DIR / name).read_text())
    MODELS[name].model_validate(data)


def test_examples_in_sync_with_models():
    assert set(build_examples()) == set(MODELS)
    assert check() == [], "run: uv run python -m fermentai.api.contract --write"


def test_command_example_respects_limits():
    cmd = json.loads((CONTRACT_DIR / "command_example.json").read_text())
    assert 4.0 <= cmd["temperature_setpoint_c"] <= 35.0
    assert cmd["motor"]["duration_s"] <= 10.0


def test_readme_mentions_every_device_endpoint():
    text = (CONTRACT_DIR / "README_FOR_ESP32_AGENT.md").read_text()
    for ep in ("POST /api/v1/telemetry", "GET /api/v1/chambers/{chamber_id}/desired-control",
               "POST /api/v1/control-ack", "POST /api/v1/images"):
        assert ep in text
