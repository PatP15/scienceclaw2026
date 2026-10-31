"""Test helpers: pty pairs, an in-process app + bridge, and a scripted 'raw firmware' device.

Never opens a real USB port: every link is one end of a pty pair.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import termios
import time
import tty
from pathlib import Path

import httpx

from . import protocol as P
from .bridge import Bridge, BridgeConfig, PortSpec
from .serial_link import AsyncLineChannel, SerialLink


def pty_pair() -> tuple[int, int, str]:
    """(master_fd, slave_fd, slave_path), both ends raw."""
    m, s = os.openpty()
    tty.setraw(m, termios.TCSANOW)
    tty.setraw(s, termios.TCSANOW)
    return m, s, os.ttyname(s)


def close_fds(*fds: int) -> None:
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)


async def wait_for(pred, timeout: float = 10.0, interval: float = 0.05, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while True:
        res = pred()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return res
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(interval)


def asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge-test")


class BridgeHarness:
    """Runs a Bridge against an in-process FastAPI app (no sockets)."""

    def __init__(self, app, ports: list[PortSpec], *, operator_token: str, status_path: Path | None = None,
                 http: httpx.AsyncClient | None = None, replay: tuple[Path, float] | None = None, **cfg):
        self.http = http or asgi_client(app)
        self.cfg = BridgeConfig(ports=ports, operator_token=operator_token, status_path=status_path,
                                poll_interval_s=cfg.pop("poll_interval_s", 0.3),
                                status_interval_s=cfg.pop("status_interval_s", 0.3),
                                reconnect_s=cfg.pop("reconnect_s", 0.2), **cfg)
        self.bridge = Bridge(self.cfg, http_client=self.http, replay=replay)
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> BridgeHarness:
        self.task = asyncio.create_task(self.bridge.run(self.stop))
        await asyncio.sleep(0.02)          # let run() register its port sessions
        return self

    async def __aexit__(self, *exc) -> None:
        self.stop.set()
        if self.task:
            await asyncio.wait_for(self.task, 10)
        await self.http.aclose()

    def session(self, i: int = 0):
        return self.bridge.sessions[i]


class RawDevice:
    """Scripted stand-in for real firmware on the slave end of a pty: writes exact lines, records
    what the host sends (no VirtualChamber logic)."""

    def __init__(self, fd: int):
        self.fd = fd
        self.channel = AsyncLineChannel(SerialLink.from_fd(fd, owns_fd=False)).start()
        self.received: list[dict] = []
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        while True:
            line = await self.channel.readline()
            if line is None:
                return
            with contextlib.suppress(ValueError):
                self.received.append(json.loads(line))

    async def send(self, msg: dict | str | bytes) -> None:
        if isinstance(msg, dict):
            data = P.encode(msg)
        elif isinstance(msg, str):
            data = (msg + "\n").encode()
        else:
            data = msg
        await self.channel.write_line(data)

    def of_type(self, t: str) -> list[dict]:
        return [m for m in self.received if m.get("type") == t]

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await self.channel.close()


def hello(chamber: str = "A", device_id: str | None = None, boot_id: str = "boot0001", **kw) -> dict:
    return {"type": "hello", "device_id": device_id or f"fermentai-esp32-{chamber.lower()}",
            "chamber_id": chamber, "firmware_version": "test-fw", "boot_id": boot_id,
            "capabilities": ["telemetry", "ack", "commands"], **kw}


def telemetry_line(chamber: str = "A", seq: int = 1, boot_id: str = "boot0001", **fields) -> dict:
    payload = {"schema_version": "1.0", "chamber_id": chamber, "device_id": f"fermentai-esp32-{chamber.lower()}",
               "boot_id": boot_id, "sequence_number": seq, "timestamp_utc": None,
               "timestamp_quality": "unsynced", "device_uptime_ms": 1000 * seq,
               "chamber_temperature_c": 24.0, "starter_temperature_c": 23.5, "source": "measured",
               "quality_flags": []}
    payload.update(fields)
    return {"type": "telemetry", "payload": payload}
