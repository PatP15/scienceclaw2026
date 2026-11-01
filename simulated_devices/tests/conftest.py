"""Shared helpers for simulated_devices tests (no network; stubbed monotonic clocks)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from simulated_devices.device import ChamberConfig, VirtualChamber


class Mono:
    """Stubbed monotonic clock (seconds)."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_device(chamber_id: str = "A", net=None, clock: Mono | None = None, **kw) -> VirtualChamber:
    kw.setdefault("images_enabled", False)
    kw.setdefault("use_fermentation_model", False)
    cfg = ChamberConfig(chamber_id=chamber_id, seed=7 if chamber_id == "A" else 8, **kw)
    return VirtualChamber(cfg, net, clock=clock or Mono())


def flat_response(cid="ctl-1", seq=1, setpoint=24.0, ttl=90, motor=None, commands=None,
                  server_time="2026-10-31T12:00:00Z", epoch="e1"):
    return {
        "schema_version": "1.0", "command_type": "control", "command_id": cid, "sequence_number": seq,
        "server_epoch": epoch, "issued_at_utc": server_time, "expires_at_utc": server_time,
        "ttl_s": ttl, "server_time_utc": server_time, "chamber_id": "A", "experiment_id": None,
        "temperature_setpoint_c": setpoint, "peltier_mode_hint": "heat",
        "motor": motor or {"command_id": None, "action": "none", "run": False, "duration_s": 0.0,
                           "pattern": None, "sequence_number": None, "ttl_s": None,
                           "expires_at_utc": None},
        "limits": {}, "poll_interval_s": 5, **({"commands": commands} if commands is not None else {}),
    }


def list_cmd(cid, action, seq, params=None, *, expires_in_s=60, server_time="2026-10-31T12:00:00Z",
             chamber_id="A"):
    t0 = datetime.fromisoformat(server_time.replace("Z", "+00:00")).astimezone(UTC)
    return {"schema_version": "1.0", "command_id": cid, "chamber_id": chamber_id, "action": action,
            "parameters": params or {}, "sequence_number": seq, "issued_at_utc": server_time,
            "expires_at_utc": (t0 + timedelta(seconds=expires_in_s)).strftime("%Y-%m-%dT%H:%M:%SZ")}


@pytest.fixture
def mono():
    return Mono()
