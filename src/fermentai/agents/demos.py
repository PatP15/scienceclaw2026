"""Scripted "Replay" examples for the dashboard's offline demo (PLAN §11).

Every file in ``data/demos/`` is labeled ``"label": "Replay"`` / ``"provenance": "replay"``:
the feedback texts are scripted examples (not real tastings) and any telemetry is synthetic.
The ``result`` block is real output of the offline deterministic policy for that input.

Regenerate:  ``uv run python -m fermentai.agents.demos``
Load:        ``load_demos()`` -> list of dicts (sorted by id)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .communication import REPO_ROOT
from .pipeline import run_taste_feedback_cycle

DEMO_DIR = REPO_ROOT / "data" / "demos"
DEMO_NOW = "2026-10-31T18:00:00Z"
DISCLAIMER = ("REPLAY: scripted example for the offline demo. The feedback text is written by the team, not a real "
              "judge's tasting; any telemetry is synthetic. The decision output is genuine output of the offline "
              "deterministic policy for this input.")


def _replay_telemetry(heights: list[float], setpoint: float, chamber: str) -> list[dict[str, Any]]:
    rows = []
    for i, h in enumerate(heights):
        hh, mm = divmod(i * 30, 60)
        rows.append({
            "schema_version": "1.0", "chamber_id": chamber, "experiment_id": "trial_001",
            "timestamp_utc": f"2026-10-31T{6 + hh:02d}:{mm:02d}:00Z",
            "chamber_temperature_c": setpoint, "starter_temperature_c": round(setpoint - 0.4, 1),
            "relative_humidity_percent": 72.0, "gas_resistance_ohms": None, "ph": None, "ph_method": None,
            "starter_height_mm": h, "door_open": False, "peltier_mode": "idle",
            "temperature_setpoint_c": setpoint, "motor_running": False, "source": "replay", "quality_flags": [],
        })
    return rows


def _replay_telemetry_v11(chamber: str, setpoint: float, heights: list[float], ph_last: float | None) -> list[dict[str, Any]]:
    """Synthetic v1.1 records (PLAN_MASTER §4) ending 2 min before DEMO_NOW; labeled replay."""
    from datetime import datetime, timedelta

    end = datetime(2026, 10, 31, 17, 58)
    rows = []
    for i, h in enumerate(heights):
        t = end - timedelta(minutes=20 * (len(heights) - 1 - i))
        last = i == len(heights) - 1
        rows.append({
            "schema_version": "1.1", "device_id": f"fermentai-esp32-{chamber.lower()}", "chamber_id": chamber,
            "experiment_id": "trial_001", "boot_id": f"boot_{chamber.lower()}001", "sequence_number": 200 + i,
            "timestamp_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "timestamp_quality": "synced",
            "device_uptime_ms": 3_600_000 + i * 1_200_000,
            "chamber_temperature_c": setpoint + 0.5, "starter_temperature_c": setpoint - 0.2,
            "internal_heatsink_temperature_c": setpoint - 6.0, "external_heatsink_temperature_c": 38.0,
            "relative_humidity_percent": 74.0, "pressure_pa": 101300.0, "gas_resistance_ohms": 98000.0 - 900 * i,
            "ph": ph_last if last else None, "ph_method": "probe_sample" if (last and ph_last) else None,
            "starter_height_mm": h, "door_open": False, "peltier_mode": "heating" if setpoint > 24 else "idle",
            "peltier_output_fraction": 0.3, "temperature_setpoint_c": setpoint, "motor_running": False,
            "source": "replay", "quality_flags": [],
            "diagnostics": {"ph_calibration_valid": True, "ph_calibration_date": "2026-10-31"} if (last and ph_last) else {},
            "field_provenance": {"starter_height_mm": {"source": "replay", "confidence": 0.85}},
        })
    return rows


DEMOS: list[dict[str, Any]] = [
    {
        "id": "demo_01_sour_but_airy",
        "title": "Replay: 'more sour, keep it airy' (PLAN §11 example)",
        "feedback_text": ("Loaf A was mild and fluffy. Loaf B was more sour but a little dense. "
                          "I'd like a stronger sour flavor without losing the airy texture."),
        "context": {"setpoints": {"A": 22.0, "B": 27.0}},
    },
    {
        "id": "demo_02_too_vinegary",
        "title": "Replay: 'B too vinegary and collapsed, A bland'",
        "feedback_text": ("Loaf B was way too vinegary and it collapsed a bit. Loaf A was nicer but bland. "
                          "Next time make it milder than B but with more flavor than A."),
        "context": {"setpoints": {"A": 22.0, "B": 27.0}},
    },
    {
        "id": "demo_03_overproof_in_progress",
        "title": "Replay: 'both fluffy, not sour enough' while chamber B's current run passes peak",
        "feedback_text": "Both were fluffy but not sour enough.",
        "context": {
            "setpoints": {"A": 22.0, "B": 27.0},
            "telemetry": {
                "A": _replay_telemetry([20.0, 21.0, 22.4, 24.0, 25.8, 27.5], 22.0, "A"),
                "B": _replay_telemetry([20.0, 24.0, 29.0, 34.0, 38.0, 40.0, 40.4, 39.2, 37.8], 27.0, "B"),
            },
        },
    },
    {
        "id": "demo_04_master_plan_judge_script",
        "title": "Replay: PLAN_MASTER §14 judge script ('more sour, but still fluffy')",
        "feedback_text": ("Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, but the crumb "
                          "is too dense. I'd like a more sour loaf that's still fluffy."),
        "context": {
            "setpoints": {"A": 22.0, "B": 27.0},
            "experiment_id": "trial_001",
            "lead_agent": "A",  # §14: Scientist A publishes the hypothesis, Scientist B reviews it
            "telemetry": {
                "A": _replay_telemetry_v11("A", 22.0, [20.0, 21.5, 23.4, 25.6, 27.9, 30.1], None),
                "B": _replay_telemetry_v11("B", 27.0, [20.0, 25.0, 31.0, 36.5, 39.8, 40.6], 4.05),
            },
        },
        "expected_structured_preference": ["More sourness", "Lower density", "Preserve good rise (airy/fluffy crumb)"],
    },
]


def _expected_interpretation(interp: dict[str, Any]) -> dict[str, Any]:
    return {
        "loaves": {l: {d: {"label": v["label"], "score": v["score"], "confidence": v["confidence"]}
                       for d, v in interp["loaves"][l]["attributes"].items()} for l in ("A", "B")},
        "desired": {d: {"direction": v["direction"], "strength": v["strength"], "confidence": v["confidence"]}
                    for d, v in interp["desired"].items()},
        "desired_summary": interp["desired_summary"],
        "structured_preference": interp.get("structured_preference"),
        "preferred_loaf": interp["preferred_loaf"],
        "flags": interp["flags"],
    }


def generate_demo(spec: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(spec["context"], replay=True, persist_findings=False, now_utc=DEMO_NOW)
    result = run_taste_feedback_cycle(spec["feedback_text"], ctx, mode="offline")
    ne = result["next_experiment"]
    return {
        "label": "Replay",
        "provenance": "replay",
        "disclaimer": DISCLAIMER,
        "id": spec["id"],
        "title": spec["title"],
        "feedback_text": spec["feedback_text"],
        "context": spec["context"],
        "expected_interpretation": _expected_interpretation(result["sensory_interpretation"]),
        **({"expected_structured_preference": spec["expected_structured_preference"]}
           if "expected_structured_preference" in spec else {}),
        "resulting_experiment": {
            "id": ne["id"], "title": ne["title"], "objective": ne["objective"],
            "schedules": {c: {"schedule_id": s["schedule_id"], "name": s["name"], "points": s["points"]}
                          for c, s in ne["schedules"].items()},
            "proposed_by": result["hypothesis"]["proposed_by"],
            "reviewed_by": result["hypothesis"]["challenged_by"],
            "hypothesis": result["hypothesis"]["text"],
        },
        "result": result,
        "reasoning_mode_label": result["reasoning_mode_label"],
        "generated_with": {"function": "fermentai.agents.pipeline.run_taste_feedback_cycle", "mode": "offline",
                           "now_utc": DEMO_NOW, "prediction_source": result["provenance"]["prediction_source"],
                           "regenerate": "uv run python -m fermentai.agents.demos"},
    }


def write_demos(directory: Path = DEMO_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in DEMOS:
        p = directory / f"{spec['id']}.json"
        p.write_text(json.dumps(generate_demo(spec), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


def load_demos(directory: Path = DEMO_DIR) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("demo_*.json"))]


if __name__ == "__main__":
    for path in write_demos():
        print(path)
