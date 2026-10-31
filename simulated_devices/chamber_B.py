"""Virtual ESP32 for chamber B ("more-acidic hypothesis"). Has NO pH probe (ph is always null).

Run alone (separate process):
    uv run python -m simulated_devices.chamber_B --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a file: make the repo root importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulated_devices.device import ChamberConfig
from simulated_devices.virtual_thermal_plant import PlantParams


def config(**overrides) -> ChamberConfig:
    # Slightly different (assumed) insulation and starter so the twins are distinguishable.
    kw = dict(chamber_id="B", seed=202, plant=PlantParams(ambient_c=21.0, tau_loss_s=1500.0,
                                                           ambient_drift_amplitude_c=0.8),
              initial_starter_height_mm=24.0)
    kw.update(overrides)
    kw["has_ph_probe"] = False   # chamber B never has a pH probe
    kw["manual_ph"] = overrides.get("manual_ph")
    return ChamberConfig(**kw)


def main(argv: list[str] | None = None) -> int:
    from simulated_devices.cli import main as cli_main

    return cli_main(["--chambers", "B", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
