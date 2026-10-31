"""Experiment scenarios (Chamber A / B) loaded from ``configs/experiments.yaml``.

API: ``load_experiments(path=None) -> dict`` (raw YAML), ``load_scenarios(path=None) ->
dict[str, Scenario]``, ``load_scenario(chamber_id, path=None) -> Scenario``.
A ``Scenario`` has ``chamber_id, name, hypothesis, objective, schedule (TemperatureSchedule),
window_h, horizon_h, caveat``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .kinetics import config_dir

if TYPE_CHECKING:  # pragma: no cover
    from .simulation import TemperatureSchedule


@dataclass(frozen=True)
class Scenario:
    chamber_id: str
    name: str
    hypothesis: str
    objective: str
    schedule: TemperatureSchedule
    window_h: tuple[float, float]
    horizon_h: float
    caveat: str = ""


def load_experiments(path: str | Path | None = None) -> dict:
    """Raw contents of configs/experiments.yaml."""
    p = Path(path) if path is not None else config_dir() / "experiments.yaml"
    with open(p) as fh:
        return yaml.safe_load(fh)


def load_scenarios(path: str | Path | None = None) -> dict[str, Scenario]:
    """All chambers as ``{chamber_id: Scenario}``."""
    from .simulation import TemperatureSchedule

    cfg = load_experiments(path)
    window = tuple(float(x) for x in cfg["window"]["observation_window_h"])
    horizon = float(cfg["window"]["prediction_horizon_h"])
    out = {}
    for cid, c in cfg["chambers"].items():
        out[str(cid)] = Scenario(
            chamber_id=str(cid),
            name=c["name"],
            hypothesis=c["hypothesis"],
            objective=c.get("objective", ""),
            schedule=TemperatureSchedule.from_dict(c["temperature_schedule"]),
            window_h=window,  # type: ignore[arg-type]
            horizon_h=horizon,
            caveat=c.get("caveat", ""),
        )
    return out


def load_scenario(chamber_id: str, path: str | Path | None = None) -> Scenario:
    """One chamber's scenario (``"A"`` or ``"B"``)."""
    sc = load_scenarios(path)
    key = str(chamber_id).upper()
    if key not in sc:
        raise KeyError(f"unknown chamber {chamber_id!r}; available: {sorted(sc)}")
    return sc[key]
