"""USB-serial transport for the virtual ESP32s (contracts/serial_v1.md).

Drop-in replacement for :class:`simulated_devices.network_client.DeviceHttpClient`: the
VirtualChamber calls the same four coroutines (post_telemetry / post_ack / get_desired_control /
post_image) but instead of HTTP they become newline-delimited JSON lines on a serial port (a pty
in tests, `--port <pty-slave>` on the CLI). The Mac-side bridge (`python -m fermentai.bridge`)
turns them into API calls.

Device -> host: hello (boot + every 30 s until hello_ack), telemetry, ack, image chunks, log, pong.
Host -> device: hello_ack, command (one command_v1 object + server_time_utc), time, ping.
Commands are queued until the VirtualChamber's next "poll"; their remaining validity is computed
from the line's server_time_utc on the device's monotonic clock (no wall clock needed). Until a
`time` line arrives telemetry carries timestamp_utc=null / "unsynced"; afterwards the host time
(+ monotonic offset) is used and reported as "synced".

Everything is still labelled source="simulated" and the hello says simulated=true.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from fermentai.bridge import protocol as P
from fermentai.bridge.serial_link import AsyncLineChannel, LinkClosed, SerialLink

from . import FIRMWARE_VERSION
from .network_client import ContractState, Result

log = logging.getLogger("simulated_devices.serial")

CAPABILITIES = ["telemetry", "ack", "commands", "set_setpoint", "animate_motor", "stop_motor",
                "capture_image", "image_chunks", "time_sync", "simulated"]
IMAGE_CHUNK_RAW_BYTES = 4096          # base64 -> 5464 chars; line stays well under 8 KB


def _parse_utc(s: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


class SerialDeviceTransport:
    def __init__(self, chamber_id: str, device_id: str, contract: ContractState, *, port: str | None = None,
                 fd: int | None = None, baud: int = P.DEFAULT_BAUD, hello_interval_s: float = 30.0,
                 sync_from_host: bool = True, clock=time.monotonic):
        if (port is None) == (fd is None):
            raise ValueError("give exactly one of port= or fd=")
        self.chamber_id = chamber_id
        self.device_id = device_id
        self.contract = contract
        self.port, self.fd, self.baud = port, fd, baud
        self.hello_interval_s = hello_interval_s
        self.sync_from_host = sync_from_host
        self.clock = clock
        self.device = None                      # VirtualChamber, attached after construction
        self.channel: AsyncLineChannel | None = None
        self.hello_acked = False
        self.host_poll_interval_s: float | None = None
        self.pending: list[tuple[dict, float, float | None]] = []   # (command, received_mono, remaining_s)
        self.stats = {"tx_lines": 0, "rx_lines": 0, "rx_commands": 0, "rx_time": 0, "rx_garbage": 0,
                      "hellos": 0, "reconnects": 0}
        self._task: asyncio.Task | None = None
        self._last_hello = -1e9

    # ---------------------------------------------------------------- lifecycle
    def attach(self, device) -> None:
        self.device = device

    async def start(self) -> SerialDeviceTransport:
        self._open()
        self._task = asyncio.create_task(self._pump(), name=f"serial-dev-{self.chamber_id}")
        return self

    def _open(self) -> None:
        link = (SerialLink.open(self.port, self.baud, exclusive=False) if self.port
                else SerialLink.from_fd(self.fd, owns_fd=False))
        self.channel = AsyncLineChannel(link).start()
        self.hello_acked = False
        self._last_hello = -1e9

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.channel:
            await self.channel.close()

    async def _write(self, msg: dict) -> bool:
        if self.channel is None or self.channel.is_closed:
            return False
        try:
            await self.channel.write_line(P.encode(msg))
        except (LinkClosed, TimeoutError, OSError) as e:
            log.warning("[%s] serial write failed: %s", self.chamber_id, e)
            return False
        self.stats["tx_lines"] += 1
        return True

    async def log_line(self, text: str, level: str = "info") -> None:
        await self._write({"type": "log", "level": level, "msg": text[:500]})

    async def send_hello(self) -> None:
        dev = self.device
        self._last_hello = self.clock()
        self.stats["hellos"] += 1
        await self._write({
            "type": "hello", "device_id": self.device_id, "chamber_id": self.chamber_id,
            "firmware_version": FIRMWARE_VERSION, "boot_id": dev.boot_id if dev else None,
            "capabilities": CAPABILITIES, "simulated": True,
            "uptime_ms": dev.uptime_ms() if dev else None,
        })

    async def _pump(self) -> None:
        """Consume host lines; re-send hello every hello_interval_s until acked; reopen on loss."""
        while True:
            if self.channel is None or self.channel.is_closed:
                await asyncio.sleep(0.5)
                with contextlib.suppress(OSError, LinkClosed):
                    if self.channel:
                        await self.channel.close()
                    self._open()
                    self.stats["reconnects"] += 1
                continue
            if not self.hello_acked and self.clock() - self._last_hello >= self.hello_interval_s:
                await self.send_hello()
            try:
                line = await self.channel.readline(timeout=0.1)
            except TimeoutError:
                continue
            if line is None:
                continue                      # link closed; loop reopens
            self.stats["rx_lines"] += 1
            p = P.parse_line(line, allowed=P.HOST_TYPES)
            if p.kind != "msg":
                self.stats["rx_garbage"] += 1
                continue
            await self._on_host(p.msg)

    async def _on_host(self, msg: dict) -> None:
        t = msg["type"]
        if t == "hello_ack":
            if msg.get("chamber_id") not in (None, self.chamber_id):
                log.error("[%s] host acked hello for chamber %s", self.chamber_id, msg.get("chamber_id"))
                return
            self.hello_acked = True
            self.host_poll_interval_s = msg.get("poll_interval_s")
            self._sync_time(msg.get("server_time_utc"))
            log.info("[%s] hello acked by host", self.chamber_id)
        elif t == "time":
            self.stats["rx_time"] += 1
            self._sync_time(msg.get("server_time_utc"))
        elif t == "ping":
            await self._write(P.pong(msg.get("seq")))
        elif t == "command":
            cmd = msg.get("payload")
            if not isinstance(cmd, dict):
                return
            ref, exp = _parse_utc(msg.get("server_time_utc")), _parse_utc(cmd.get("expires_at_utc"))
            remaining = (exp - ref).total_seconds() if ref and exp else None
            self.pending.append((cmd, self.clock(), remaining))
            self.stats["rx_commands"] += 1

    def _sync_time(self, server_time: Any) -> None:
        ref = _parse_utc(server_time)
        dev = self.device
        if not (self.sync_from_host and ref and dev):
            return
        mono = self.clock()
        dev.utcnow = lambda: ref + timedelta(seconds=self.clock() - mono)
        if not dev.cfg.synced_clock:
            log.info("[%s] clock set from host time %s -> timestamps now 'synced'", self.chamber_id,
                     server_time)
        dev.cfg.synced_clock = True

    # ---------------------------------------------------------------- DeviceHttpClient interface
    async def post_telemetry(self, full: dict) -> Result:
        ok = await self._write({"type": "telemetry", "payload": self.contract.shape_telemetry(full)})
        return Result(ok, 200 if ok else None, {"ok": ok}, None if ok else "serial link down")

    async def post_ack(self, full: dict) -> Result:
        ok = await self._write({"type": "ack", "payload": self.contract.shape_ack(full)})
        return Result(ok, 200 if ok else None, {"ok": ok}, None if ok else "serial link down")

    async def get_desired_control(self) -> Result:
        """Hand queued commands to the VirtualChamber as a desired-control-like envelope.
        Each command carries ttl_s = remaining validity measured on this device's own clock."""
        now = self.clock()
        cmds = []
        for cmd, received, remaining in self.pending:
            c = dict(cmd)
            if remaining is not None:
                c["ttl_s"] = remaining - (now - received)
            cmds.append(c)
        self.pending.clear()
        if self.channel is None or self.channel.is_closed:
            return Result(False, None, error="serial link down")
        return Result(True, 200, {"schema_version": "1.0", "chamber_id": self.chamber_id, "commands": cmds})

    async def post_image(self, jpeg: bytes, fields: dict[str, str]) -> Result:
        sha = hashlib.sha256(jpeg).hexdigest()
        chunks = [jpeg[i:i + IMAGE_CHUNK_RAW_BYTES] for i in range(0, len(jpeg), IMAGE_CHUNK_RAW_BYTES)] or [b""]
        n = len(chunks)
        for i, part in enumerate(chunks, 1):
            msg = {"type": "image", "chamber_id": self.chamber_id, "device_id": self.device_id,
                   "uptime_ms": self.device.uptime_ms() if self.device else None, "bytes": len(jpeg),
                   "sha256": sha, "encoding": "base64-chunks", "chunk": f"{i}/{n}",
                   "data": base64.b64encode(part).decode(), "source": fields.get("source", "simulated")}
            for k in ("timestamp_utc", "experiment_id"):
                if fields.get(k):
                    msg[k] = fields[k]
            if not await self._write(msg):
                return Result(False, None, error="serial link down")
        return Result(True, 200, {"ok": True, "image_id": None, "transport": "usb-serial"})
