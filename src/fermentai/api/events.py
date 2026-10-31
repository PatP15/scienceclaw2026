"""Server-sent events for the dashboard.

Events are rows in the SQLite `events` table (so agent processes writing to the same DB also
show up) and are streamed by polling the table. Each SSE frame:

    id: <int>
    event: <type>            # omitted when ?format=plain
    data: {"id":..,"type":..,"ts_utc":..,"chamber_id":..,"data":{..}}
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import Request

from .db import Database

EVENT_TYPES = (
    "telemetry", "image", "setpoint", "command", "ack", "experiment",
    "feedback", "feedback_result", "message", "simulation",
)


def format_sse(evt: dict, named: bool = True) -> str:
    lines = [f"id: {evt['id']}"]
    if named:
        lines.append(f"event: {evt['type']}")
    lines.append("data: " + json.dumps(evt, default=str, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


async def event_stream(
    request: Request, db: Database, since: int, *, types: list[str] | None = None,
    named: bool = True, max_events: int | None = None, poll_s: float = 0.5,
    keepalive_s: float = 15.0, timeout_s: float | None = None,
) -> AsyncIterator[str]:
    last_id = since
    sent = 0
    started = last_ping = time.monotonic()
    yield "retry: 2000\n: fermentai event stream\n\n"
    while True:
        if await request.is_disconnected():
            return
        rows = await asyncio.to_thread(db.events_since, last_id, 200, types)
        for evt in rows:
            last_id = evt["id"]
            yield format_sse(evt, named)
            sent += 1
            if max_events is not None and sent >= max_events:
                return
        now = time.monotonic()
        if timeout_s is not None and now - started >= timeout_s:
            return
        if now - last_ping >= keepalive_s:
            last_ping = now
            yield ": keepalive\n\n"
        await asyncio.sleep(poll_s)
