"""Virtual chef-hat automaton motor (decorative only; never coupled to the Peltier)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .limits import LIMITS


@dataclass
class MotorRun:
    command_id: str
    duration_s: float
    pattern: str
    started_at: float
    sequence_number: int | None = None
    source: str = "flat"          # "flat" (v1.0 motor directive) or "list" (v1.1 commands)

    @property
    def ends_at(self) -> float:
        return self.started_at + self.duration_s


class VirtualMotor:
    def __init__(self, max_duration_s: float = LIMITS.motor_max_duration_s):
        self.max_duration_s = max_duration_s
        self.current: MotorRun | None = None
        self.runs = 0

    @property
    def running(self) -> bool:
        return self.current is not None

    def start(self, command_id: str, duration_s: float, pattern: str, now: float, *,
              sequence_number: int | None = None, source: str = "flat") -> MotorRun | None:
        """Start a run (clamped to the local max). Returns the run it pre-empted, if any."""
        previous = self.current
        d = min(max(float(duration_s), 0.0), self.max_duration_s)
        self.current = MotorRun(command_id, d, pattern or "wiggle", now, sequence_number, source)
        self.runs += 1
        return previous

    def stop(self) -> MotorRun | None:
        previous, self.current = self.current, None
        return previous

    def update(self, now: float) -> MotorRun | None:
        """Return the run that just finished (its full duration elapsed), else None."""
        if self.current is not None and now >= self.current.ends_at:
            done, self.current = self.current, None
            return done
        return None

    def angle_deg(self, now: float) -> float:
        """Animation pose (for logs / camera overlay)."""
        if self.current is None:
            return 0.0
        t = now - self.current.started_at
        f = {"wiggle": 2.0, "nod": 0.8, "spin": 0.5}.get(self.current.pattern, 1.0)
        if self.current.pattern == "spin":
            return (360.0 * f * t) % 360.0
        return 25.0 * math.sin(2 * math.pi * f * t)
