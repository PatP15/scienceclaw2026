"""FermentAI sourdough fermentation model (CPU-only SciPy ODE). All outputs are "simulated".

Quick start::

    from fermentai.fermentation.simulation import TemperatureSchedule, simulate, load_scenario
    df = simulate(load_scenario("B").schedule, t_end_h=16)          # pandas DataFrame
    df = simulate(TemperatureSchedule([(0, 27), (8, 22)]), t_end_h=16)  # agent-revised schedule

Modules: ``kinetics`` (parameters, rate laws, buffered pH), ``simulation`` (schedules,
``simulate``, ``simulate_with_uncertainty``, ``summarize_prediction``), ``calibration``
(``calibrate``), ``scenarios`` (configs/experiments.yaml), ``sensitivity``, ``plotting``.
See docs/scientific_assumptions.md.
"""

from .calibration import CalibrationResult, calibrate
from .kinetics import apply_molecular_descriptors
from .simulation import (
    TemperatureSchedule,
    load_scenario,
    load_scenarios,
    simulate,
    simulate_with_uncertainty,
    summarize_for_morphology,
    summarize_prediction,
)

__all__ = [
    "CalibrationResult",
    "TemperatureSchedule",
    "calibrate",
    "load_scenario",
    "load_scenarios",
    "simulate",
    "simulate_with_uncertainty",
    "summarize_prediction",
    "summarize_for_morphology",
    "apply_molecular_descriptors",
]
