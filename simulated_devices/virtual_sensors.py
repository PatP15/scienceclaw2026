"""Virtual sensors for one chamber (all values SIMULATED).

* ``Bme680Like``   - relative humidity, pressure and raw gas resistance with slow drift.
                     The gas resistance is a VOC-sensitive raw resistance; it is NOT CO2 and is
                     never converted to ppm CO2.
* ``StarterModel`` - starter height (and optional pH) from a rise curve. When importable it is
                     driven by ``fermentai.fermentation.simulation.simulate`` on the starter
                     temperature history (labelled simulated); otherwise a logistic fallback.
* ``read_ds18b20`` - starter probe quantisation/noise helper.

Fermentation runs on an accelerated clock (``time_scale``) so a few demo minutes show visible
rise; the thermal plant is real-time. Both are model outputs, not measurements.
"""

from __future__ import annotations

import logging
import math
import random

log = logging.getLogger("simulated_devices.sensors")


def saturation_vapour_pressure_hpa(t_c: float) -> float:
    """Magnus formula (Alduchov & Eskridge 1996), hPa."""
    return 6.1094 * math.exp(17.625 * t_c / (t_c + 243.04))


class Bme680Like:
    def __init__(self, rng: random.Random, *, room_rh_percent: float = 45.0,
                 chamber_rh_equilibrium_percent: float = 78.0, base_pressure_pa: float = 101325.0,
                 gas_baseline_ohms: float = 150_000.0):
        self.rng = rng
        self.room_rh = room_rh_percent
        self.eq_rh = chamber_rh_equilibrium_percent
        self.base_pressure = base_pressure_pa
        self.gas_baseline = gas_baseline_ohms
        self.vapour_hpa = saturation_vapour_pressure_hpa(21.0) * room_rh_percent / 100.0
        self.voc = 0.0            # arbitrary VOC load, unitless
        self._pressure_walk = 0.0
        self.t_s = 0.0

    def step(self, dt: float, *, chamber_c: float, ambient_c: float, door_open: bool,
             activity: float) -> None:
        """activity: fermentation activity 0..~1 (relative gas/VOC production)."""
        self.t_s += dt
        if door_open:
            target, tau = saturation_vapour_pressure_hpa(ambient_c) * self.room_rh / 100.0, 60.0
        else:
            target, tau = saturation_vapour_pressure_hpa(chamber_c) * self.eq_rh / 100.0, 1200.0
        self.vapour_hpa += (target - self.vapour_hpa) * (1 - math.exp(-dt / tau))
        leak_tau = 60.0 if door_open else 3600.0
        self.voc += dt * (0.0008 * max(activity, 0.0)) - self.voc * (1 - math.exp(-dt / leak_tau))
        self.voc = max(self.voc, 0.0)
        self._pressure_walk += self.rng.gauss(0.0, 0.5 * math.sqrt(max(dt, 0.0)))
        self._pressure_walk = max(-300.0, min(300.0, self._pressure_walk))

    def read(self, chamber_c: float) -> dict:
        g = self.rng.gauss
        rh = 100.0 * self.vapour_hpa / saturation_vapour_pressure_hpa(chamber_c)
        rh = min(max(rh + g(0, 0.4), 0.0), 100.0)
        pressure = (self.base_pressure + 80.0 * math.sin(2 * math.pi * self.t_s / (6 * 3600.0))
                    + self._pressure_walk + g(0, 3.0))
        # MOX sensor: resistance rises during burn-in, falls with VOC load
        burn_in = 0.75 + 0.25 * (1 - math.exp(-self.t_s / 900.0))
        gas = self.gas_baseline * burn_in / (1.0 + 4.0 * self.voc) * (1 + g(0, 0.01))
        return {"relative_humidity_percent": round(rh, 1), "pressure_pa": round(pressure, 1),
                "gas_resistance_ohms": round(gas, 0)}


def read_ds18b20(true_c: float, rng: random.Random) -> float:
    """12-bit DS18B20: 0.0625 C resolution, small noise."""
    return round(round((true_c + rng.gauss(0, 0.04)) / 0.0625) * 0.0625, 4)


class StarterModel:
    """Starter rise (and pH) on an accelerated fermentation clock."""

    def __init__(self, *, initial_height_mm: float = 25.0, time_scale: float = 30.0,
                 use_fermentation_model: bool = True, rng: random.Random | None = None):
        self.h0 = initial_height_mm
        self.time_scale = time_scale
        self.rng = rng or random.Random(0)
        self.ferment_h = 0.0
        self.history: list[list[float]] = []       # [[t_h, starter_temp_c], ...]
        self.expansion = 1.0
        self.ph_value = 5.2
        self.activity = 0.0
        self._sim = None
        if use_fermentation_model:
            try:
                from fermentai.fermentation.simulation import simulate

                self._sim = simulate
            except Exception as e:  # pragma: no cover - depends on environment
                log.info("fermentation simulator unavailable (%s); using logistic fallback", e)
        self.model_name = ("fermentai.fermentation.simulation.simulate" if self._sim
                           else "logistic_fallback")
        self._last_eval_h = -1.0

    def step(self, dt: float, starter_c: float) -> None:
        dh = dt * self.time_scale / 3600.0
        self.ferment_h += dh
        if not self.history or abs(self.history[-1][1] - starter_c) > 0.2 or \
                self.ferment_h - self.history[-1][0] > 0.25:
            self.history.append([self.ferment_h, round(starter_c, 2)])
            self.history = self.history[-400:]
            if self.history[0][0] != 0.0:
                self.history[0][0] = 0.0
        if self._sim is not None:
            if self.ferment_h - self._last_eval_h >= 0.05:
                self._evaluate_model()
        else:
            self._logistic(dh, starter_c)

    def _evaluate_model(self) -> None:
        t_end = max(self.ferment_h, 0.1)
        prev = self.expansion
        try:
            df = self._sim({"breakpoints": self.history, "mode": "piecewise_constant"},
                           t_end_h=min(t_end, 48.0), dt_h=0.05)
            self.expansion = float(df["expansion_ratio"].iloc[-1])
            self.ph_value = float(df["ph"].iloc[-1])
        except Exception as e:  # never crash the device loop over the model
            log.warning("fermentation model failed (%s); switching to logistic fallback", e)
            self._sim, self.model_name = None, "logistic_fallback"
            return
        dt_h = max(self.ferment_h - self._last_eval_h, 1e-6) if self._last_eval_h >= 0 else 1.0
        self.activity = max(0.0, (self.expansion - prev) / dt_h)
        self._last_eval_h = self.ferment_h

    def _logistic(self, dh: float, starter_c: float) -> None:
        r = 0.35 * 2 ** ((starter_c - 24.0) / 10.0)          # 1/h, assumption
        e_max = 2.1
        x = self.expansion - 1.0 + 0.02
        growth = r * x * (1 - (self.expansion - 1.0) / (e_max - 1.0))
        self.expansion = min(e_max, self.expansion + dh * growth)
        self.activity = max(0.0, growth)
        self.ph_value = max(3.7, self.ph_value - dh * 0.12 * 2 ** ((starter_c - 24.0) / 10.0)
                            * (self.ph_value - 3.6) / 1.6)

    def height_mm(self) -> float:
        return round(self.h0 * self.expansion + self.rng.gauss(0, 0.2), 1)

    def ph_reading(self) -> float:
        return round(self.ph_value + self.rng.gauss(0, 0.02), 2)
