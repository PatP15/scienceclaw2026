"""Validate firmware-serialized JSON against the backend's own Pydantic models.

The native test `test_telemetry/test_emit_contract_samples` writes real serializer output to
firmware/build_logs/contract_samples/. Run from the repo root:

    uv run python firmware/tools/validate_contract_samples.py

Exit code 0 = every sample is accepted by fermentai.api.schemas (TelemetryIn / ControlAckIn),
which are the models the live backend uses (extra="forbid", allow_inf_nan=False).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fermentai.api.schemas import ControlAckIn, TelemetryIn

SAMPLES = Path(__file__).resolve().parent.parent / "build_logs" / "contract_samples"


def main() -> int:
    files = sorted(SAMPLES.glob("*.json"))
    if not files:
        print(f"no samples in {SAMPLES}; run `pio test -e native` first")
        return 2
    failed = 0
    for f in files:
        model = TelemetryIn if f.name.startswith("telemetry") else ControlAckIn
        try:
            model.model_validate_json(f.read_text())
            keys = len(json.loads(f.read_text()))
            print(f"OK    {model.__name__:<12} {f.name} ({keys} keys)")
        except Exception as exc:  # pydantic.ValidationError
            failed += 1
            print(f"FAIL  {model.__name__:<12} {f.name}\n{exc}")
    print(f"{len(files) - failed}/{len(files)} samples valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
