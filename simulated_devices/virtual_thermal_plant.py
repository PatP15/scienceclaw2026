"""Controller-test thermal model of one Peltier chamber + the local firmware controller.

!!! NOT A VALIDATED PHYSICAL SIMULATION !!!
This is a lumped first-order model with hand-picked (assumed) time constants, built only so the
local controller logic (hysteresis, PI output, dead time on polarity reversal, fan overrun,
cutoffs, door-open idle, offline hold) and the backend/dashboard can be exercised. Its outputs
are labelled ``simulated`` everywhere and must never be presented as chamber measurements.

State (per chamber, fully independent between chambers):
  internal_heatsink_c   inside (chamber-side) heatsink of the Peltier module
  chamber_c             chamber air
  starter_c             starter/dough (lags the air)
  external_heatsink_c   outside (room-side) heatsink
Drivers: Peltier output fraction u in [0, 1] and direction (heat/cool), ambient room
temperature (slow sinusoidal drift + small random walk), door state (open door couples the air
strongly to the room).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .limits import LIMITS, LocalLimits


@dataclass
class PlantParams:
    """All values are assumptions for a ~2-5 L insulated box (no literature source)."""

    ambient_c: float = 21.0
    ambient_drift_amplitude_c: float = 0.6
    ambient_drift_period_s: float = 3 * 3600.0
    ambient_random_walk_c_per_sqrt_s: float = 0.002
    tau_loss_s: float = 1800.0              # chamber air -> room, door closed
    tau_loss_door_open_s: float = 90.0      # door open: strong exchange with the room
    tau_internal_sink_s: float = 30.0       # internal heatsink thermal lag
    tau_sink_to_air_s: float = 600.0        # internal heatsink -> chamber air
    heat_sink_delta_c: float = 10.0         # internal sink above air at full heating
    cool_sink_delta_c: float = 10.0         # internal sink below air at full cooling
    tau_starter_s: float = 900.0            # starter lags the air
    tau_external_sink_s: float = 60.0
    external_rise_cooling_c: float = 15.0   # hot side outside when cooling at full output
    external_drop_heating_c: float = 4.0    # cold side outside when heating at full output


@dataclass
class ControllerParams:
    hysteresis_c: float = 0.3
    kp_per_c: float = 0.35
    ki_per_c_s: float = 0.0015
    max_output: float = 1.0
    slew_per_s: float = 0.05                 # ramp the PWM duty, never step it
    fault_clear_s: float = 60.0              # back in range this long -> clear latched fault


@dataclass
class ThermalPlant:
    params: PlantParams = field(default_factory=PlantParams)
    seed: int = 0
    t_s: float = 0.0

    def __post_init__(self) -> None:
        p = self.params
        self.rng = random.Random(self.seed)
        self._ambient_walk = 0.0
        self.chamber_c = p.ambient_c
        self.starter_c = p.ambient_c - 0.3
        self.internal_heatsink_c = p.ambient_c
        self.external_heatsink_c = p.ambient_c

    @property
    def ambient_c(self) -> float:
        p = self.params
        drift = p.ambient_drift_amplitude_c * math.sin(2 * math.pi * self.t_s / p.ambient_drift_period_s)
        return p.ambient_c + drift + self._ambient_walk

    def step(self, dt: float, u: float, direction: str | None, door_open: bool) -> None:
        """Advance by dt seconds (sub-stepped for stability) with output u in direction."""
        p = self.params
        u = min(max(u, 0.0), 1.0)
        heat = u if direction == "heat" else 0.0
        cool = u if direction == "cool" else 0.0
        n = max(1, math.ceil(dt / 2.0))
        h = dt / n
        for _ in range(n):
            self.t_s += h
            self._ambient_walk += self.rng.gauss(0.0, p.ambient_random_walk_c_per_sqrt_s * math.sqrt(h))
            self._ambient_walk = max(-1.0, min(1.0, self._ambient_walk))
            amb = self.ambient_c
            sink_target = self.chamber_c + heat * p.heat_sink_delta_c - cool * p.cool_sink_delta_c
            self.internal_heatsink_c += h * (sink_target - self.internal_heatsink_c) / p.tau_internal_sink_s
            tau_loss = p.tau_loss_door_open_s if door_open else p.tau_loss_s
            self.chamber_c += h * ((self.internal_heatsink_c - self.chamber_c) / p.tau_sink_to_air_s
                                   + (amb - self.chamber_c) / tau_loss)
            self.starter_c += h * (self.chamber_c - self.starter_c) / p.tau_starter_s
            ext_target = amb + cool * p.external_rise_cooling_c - heat * p.external_drop_heating_c
            self.external_heatsink_c += h * (ext_target - self.external_heatsink_c) / p.tau_external_sink_s


class PeltierController:
    """Local firmware controller: the ESP32 decides the mode, never the server.

    Modes (contract v1.0 enum): idle | heat | cool | switching | fault | off.
    """

    def __init__(self, params: ControllerParams | None = None, limits: LocalLimits = LIMITS):
        self.p = params or ControllerParams()
        self.limits = limits
        self.mode = "off"                 # boot: Peltier off until sensors valid + fresh command
        self.output = 0.0                 # actual output fraction (0..1), slew limited
        self.direction: str | None = None  # direction currently energised
        self._integral = 0.0
        self._last_direction: str | None = None
        self._last_deenergised_at = -1e9
        self._switch_target: str | None = None
        self._switch_started = 0.0
        self._fan_until = -1e9
        self.fault_codes: list[str] = []
        self._fault_ok_since: float | None = None
        self._last_valid_temp_at: float | None = None

    @property
    def fan_running(self) -> bool:
        return self._now < self._fan_until or self.output > 0.0

    _now = 0.0

    def _faults(self, now: float, chamber_c: float | None, heatsink_c: float | None) -> list[str]:
        L = self.limits
        out = []
        if chamber_c is not None:
            self._last_valid_temp_at = now
            if chamber_c > L.chamber_cutoff_high_c:
                out.append("overtemp")
            elif chamber_c < L.chamber_cutoff_low_c:
                out.append("undertemp")
        elif self._last_valid_temp_at is None or now - self._last_valid_temp_at > L.sensor_stale_s:
            if self._last_valid_temp_at is not None:
                out.append("sensor_stale")
        if heatsink_c is not None and heatsink_c > L.heatsink_max_c:
            out.append("heatsink_overtemp")
        return out

    def update(self, now: float, dt: float, *, setpoint_c: float | None, chamber_c: float | None,
               heatsink_c: float | None, door_open: bool, offline: bool) -> None:
        self._now = now
        L, p = self.limits, self.p
        active = self._faults(now, chamber_c, heatsink_c)
        if active:
            self.fault_codes = sorted(set(self.fault_codes) | set(active))
            self._fault_ok_since = None
        elif self.fault_codes:
            self._fault_ok_since = self._fault_ok_since if self._fault_ok_since is not None else now
            if now - self._fault_ok_since >= p.fault_clear_s:
                self.fault_codes, self._fault_ok_since = [], None

        want: str  # heat | cool | idle | fault | off
        if self.fault_codes:
            want = "fault"
        elif setpoint_c is None or chamber_c is None:
            want = "off"
        elif offline or door_open:
            want = "idle"
        else:
            err = setpoint_c - chamber_c
            h = p.hysteresis_c
            if self.direction == "heat" or self._switch_target == "heat":
                want = "cool" if err < -h else "heat"
            elif self.direction == "cool" or self._switch_target == "cool":
                want = "heat" if err > h else "cool"
            else:
                want = "heat" if err > h else "cool" if err < -h else "idle"

        if want in ("heat", "cool"):
            reversing = (self._last_direction not in (None, want)
                         and now - self._last_deenergised_at < L.heat_cool_dead_time_s)
            if self.direction not in (None, want):
                # never reverse polarity directly: de-energise and wait the dead time
                self._deenergise(now)
                reversing = True
            if reversing:
                if self._switch_target != want:
                    self._switch_target, self._switch_started = want, now
                self.mode, self.output, self._integral = "switching", 0.0, 0.0
                return
            self._switch_target = None
            if self.direction != want:
                self._integral = 0.0
            self.direction = self.mode = want
            e = (setpoint_c - chamber_c) * (1 if want == "heat" else -1)  # type: ignore[operator]
            self._integral = min(max(self._integral + p.ki_per_c_s * e * dt, 0.0), p.max_output)
            target = min(max(p.kp_per_c * e + self._integral, 0.0), p.max_output)
            step = p.slew_per_s * dt
            self.output = min(max(target, self.output - step), self.output + step)
            self._last_direction = want
            self._fan_until = now + L.fan_overrun_s
        else:
            if self.direction is not None:
                self._deenergise(now)
            self._switch_target = None
            self.mode, self.output, self._integral = want, 0.0, 0.0

    def _deenergise(self, now: float) -> None:
        self._last_direction = self.direction
        self.direction = None
        self.output = 0.0
        self._last_deenergised_at = now
        self._fan_until = now + self.limits.fan_overrun_s


class ThermalChamber:
    """Plant + local controller for one chamber."""

    def __init__(self, plant: PlantParams | None = None, controller: ControllerParams | None = None,
                 seed: int = 0, limits: LocalLimits = LIMITS):
        self.plant = ThermalPlant(plant or PlantParams(), seed=seed)
        self.controller = PeltierController(controller, limits)

    def step(self, now: float, dt: float, *, setpoint_c: float | None, door_open: bool,
             offline: bool, measured_chamber_c: float | None = None,
             measured_heatsink_c: float | None = None) -> None:
        c = self.controller
        meas = self.plant.chamber_c if measured_chamber_c is None else measured_chamber_c
        sink = self.plant.external_heatsink_c if measured_heatsink_c is None else measured_heatsink_c
        c.update(now, dt, setpoint_c=setpoint_c, chamber_c=meas, heatsink_c=sink,
                 door_open=door_open, offline=offline)
        self.plant.step(dt, c.output, c.direction, door_open)
