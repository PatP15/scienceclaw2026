"""Helpers for tests: an isolated app on a temp SQLite DB, plus a controllable clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .app import create_app
from .config import HardwareConfig, Settings, load_hardware_config

TEST_TOKEN = "test-token"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _deep_update(d: dict, u: dict) -> dict:
    for k, v in u.items():
        d[k] = _deep_update(dict(d.get(k) or {}), v) if isinstance(v, dict) else v
    return d


def make_app(tmp_path: Path, *, hw_overrides: dict[str, Any] | None = None,
             token: str = TEST_TOKEN, clock: FakeClock | None = None,
             static_dir: Path | None = None, device_tokens: dict[str, str] | None = None,
             data_dir: Path | None = None) -> FastAPI:
    hw = load_hardware_config()
    if hw_overrides:
        hw = HardwareConfig.model_validate(_deep_update(hw.model_dump(), hw_overrides))
    settings = Settings(
        api_token=token,
        db_path=tmp_path / "fermentai-test.sqlite3",
        data_dir=data_dir or tmp_path / "data",
        hardware=hw,
        dashboard_static_dir=static_dir or tmp_path / "no-dashboard",
        device_tokens=dict(device_tokens or {}),
        findings_path=tmp_path / "findings_log.jsonl",  # never touch the repo's runtime log
        chat_sessions_path=tmp_path / "chat_sessions.jsonl",
    )
    app = create_app(settings)
    if clock is not None:
        app.state.service.clock = clock
    return app


def auth(token: str = TEST_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def telemetry(chamber_id: str = "A", **kw) -> dict:
    body = {"schema_version": "1.0", "chamber_id": chamber_id, "timestamp_utc": "2026-10-31T12:00:00Z",
            "chamber_temperature_c": 22.0, "source": "simulated", "quality_flags": []}
    body.update(kw)
    return body
