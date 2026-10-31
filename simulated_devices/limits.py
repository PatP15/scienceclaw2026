"""Local (device-side) safety limits, identical to configs/hardware.yaml `firmware_local_limits`.

The real ESP32 compiles these in; the virtual device reads them from the YAML when available and
otherwise falls back to the same compiled-in defaults. tests check the two never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARDWARE_YAML = REPO_ROOT / "configs" / "hardware.yaml"


@dataclass(frozen=True)
class LocalLimits:
    setpoint_min_c: float = 4.0
    setpoint_max_c: float = 35.0
    chamber_cutoff_high_c: float = 38.0
    chamber_cutoff_low_c: float = 2.0
    heatsink_max_c: float = 60.0
    heat_cool_dead_time_s: float = 60.0
    fan_overrun_s: float = 30.0
    sensor_stale_s: float = 30.0
    offline_hold_s: float = 1800.0
    motor_max_duration_s: float = 10.0

    def clamp_setpoint(self, value: float) -> float:
        return min(max(float(value), self.setpoint_min_c), self.setpoint_max_c)


def load_local_limits(path: Path = HARDWARE_YAML) -> LocalLimits:
    try:
        import yaml

        raw = (yaml.safe_load(path.read_text()) or {}).get("firmware_local_limits") or {}
    except (OSError, ImportError, ValueError):
        return LocalLimits()
    known = {f.name for f in fields(LocalLimits)}
    return LocalLimits(**{k: float(v) for k, v in raw.items() if k in known})


LIMITS = load_local_limits()
