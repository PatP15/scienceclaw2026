"""Virtual ESP32 for chamber A ("mild hypothesis"). The only chamber that may carry a pH probe.

Run alone (separate process):
    uv run python -m simulated_devices.chamber_A --base-url http://127.0.0.1:8000 [--ph]
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a file: make the repo root importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulated_devices.device import ChamberConfig
from simulated_devices.virtual_thermal_plant import PlantParams


def config(**overrides) -> ChamberConfig:
    kw = dict(chamber_id="A", seed=101, plant=PlantParams(ambient_c=21.0, tau_loss_s=1800.0),
              initial_starter_height_mm=25.0)
    kw.update(overrides)
    return ChamberConfig(**kw)


def main(argv: list[str] | None = None) -> int:
    from simulated_devices.cli import main as cli_main

    return cli_main(["--chambers", "A", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
