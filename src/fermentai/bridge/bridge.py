"""USB-serial <-> HTTP bridge (contracts/serial_v1.md).

One `PortSession` per serial port:
  device -> host lines   hello / telemetry / ack / image / log / ping / pong
  host   -> device lines hello_ack / command / time / ping / pong
Telemetry and acks are forwarded unchanged (except provenance/NaN fixes listed below) to the
local FastAPI backend with the chamber's device token + X-Device-Id; desired-control is polled
per chamber and every NEW command_v1 object is written to the device once (dedupe by command_id,
bounded re-send while it stays pending). The bridge never invents measurements or timestamps.

Provenance policy for telemetry `source`:
  * replay mode                     -> "replay" (always)
  * hello says simulated=true       -> "simulated" (never upgraded to measured)
  * otherwise "real"/missing        -> "measured"; "simulated"/"replay" sent by the device are kept
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import protocol as P
from .serial_link import (
    DEFAULT_PORT_PATTERNS,
    AsyncLineChannel,
    LinkClosed,
    SerialLink,
    discover_ports,
)

log = logging.getLogger("fermentai.bridge")

MAX_IMAGE_BYTES = 2_000_000


@dataclass
class PortSpec:
    path: str | None = None          # device node (re-opened after unplug)
    chamber_id: str | None = None    # --map / --port PATH=CHAMBER (authoritative if given)
    fd: int | None = None            # pre-opened fd (pty master in tests); re-attached after EOF
    label: str | None = None

    @property
    def name(self) -> str:
        return self.path or self.label or f"fd:{self.fd}"


@dataclass
class BridgeConfig:
    base_url: str = "http://127.0.0.1:8000"
    ports: list[PortSpec] = field(default_factory=list)
    discover: bool = False
    patterns: tuple[str, ...] = DEFAULT_PORT_PATTERNS
    port_map: dict[str, str] = field(default_factory=dict)     # path -> chamber for discovered ports
    exclude: set[str] = field(default_factory=set)
    baud: int = P.DEFAULT_BAUD
    exclusive: bool = True
    poll_interval_s: float = 2.0
    time_interval_s: float = 60.0
    ping_interval_s: float = 15.0
    silent_after_s: float = 30.0
    reconnect_s: float = 1.0
    discover_interval_s: float = 2.0
    resend_after_s: float = 10.0
    max_sends_per_command: int = 3
    status_path: Path | None = None
    status_interval_s: float = 2.0
    summary_interval_s: float = 30.0
    operator_token: str = "change-me-local-device-token"
    device_tokens: dict[str, str] = field(default_factory=dict)
    chambers: dict[str, str] = field(default_factory=lambda: {"A": "fermentai-esp32-a",
                                                              "B": "fermentai-esp32-b"})  # chamber -> default device id
    record_path: Path | None = None
    http_timeout_s: float = 5.0
    max_outbox: int = 500


class ReplayChannel:
    """Feeds a recorded device line log as if it came from a port (writes are discarded)."""

    def __init__(self, path: Path, speed: float = 0.0):
        self.items: list[tuple[float, bytes]] = []
        for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            t, line = None, raw
            try:
                rec = json.loads(raw)
                if isinstance(rec, dict) and "line" in rec and "type" not in rec:   # --record format
                    if rec.get("dir", "rx") != "rx":
                        continue
                    t, line = rec.get("t"), str(rec["line"])
            except ValueError:
                pass
            self.items.append((float(t) if isinstance(t, (int, float)) else None, line.encode()))
        self.speed = speed
        self.tx_lines: list[bytes] = []
        self.dropped_lines = 0
        self.rx_bytes = self.tx_bytes = 0
        self.closed_reason: str | None = None
        self._i = 0
        self._t0: float | None = None
        self._start = time.monotonic()

    def start(self) -> ReplayChannel:
        return self

    async def readline(self, timeout: float | None = None) -> bytes | None:
        if self._i >= len(self.items):
            self.closed_reason = "replay finished"
            return None
        t, line = self.items[self._i]
        if self.speed > 0 and t is not None:
            if self._t0 is None:
                self._t0 = t
            due = self._start + (t - self._t0) / self.speed
            wait = due - time.monotonic()
            if wait > 0:
                if timeout is not None and wait > timeout:
                    await asyncio.sleep(timeout)
                    raise TimeoutError
                await asyncio.sleep(wait)
        self._i += 1
        self.rx_bytes += len(line)
        return line

    async def write_line(self, data: bytes) -> None:
        self.tx_lines.append(data)
        self.tx_bytes += len(data)

    @property
    def is_closed(self) -> bool:
        return self.closed_reason is not None

    async def close(self) -> None:
        return None


class PortSession:
    def __init__(self, bridge: Bridge, spec: PortSpec, *, replay: ReplayChannel | None = None):
        self.bridge = bridge
        self.cfg = bridge.cfg
        self.spec = spec
        self.name = spec.name
        self.replay = replay
        self.channel: AsyncLineChannel | ReplayChannel | None = None
        self.mapped = spec.chamber_id
        self.chamber_id: str | None = spec.chamber_id
        self.last_chamber_id: str | None = spec.chamber_id   # survives disconnects (status)
        self.state = "waiting"
        self.blocked_reason: str | None = None
        self.hello: dict | None = None
        self.device_id: str | None = None
        self.boot_id: str | None = None
        self.firmware_version: str | None = None
        self.simulated: bool | None = None
        self.capabilities: list[str] = []
        self.counters: Counter = Counter()
        self.sent: OrderedDict[str, dict] = OrderedDict()
        self.outbox: deque[tuple[str, str, dict]] = deque(maxlen=self.cfg.max_outbox)
        self.images: dict[str, dict] = {}
        self.logs: deque[str] = deque(maxlen=20)
        self.last_error: str | None = None
        self.last_rx_mono: float | None = None
        self.last_proto_mono: float | None = None
        self.times: dict[str, str | None] = {"connected_at_utc": None, "hello_at_utc": None,
                                             "last_rx_utc": None, "last_telemetry_utc": None,
                                             "last_ack_utc": None, "disconnected_at_utc": None}
        self._ping_seq = 0
        self._retry_at = 0.0
        self._finished = False

    # ---------------------------------------------------------------- lifecycle
    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set() and not self._finished:
            try:
                connected = await self._connect()
            except (LinkClosed, TimeoutError, OSError) as e:
                self.last_error = f"{type(e).__name__}: {e}"
                await self._disconnect()
                connected = False
            if connected:
                try:
                    await self._serve(stop)
                except (LinkClosed, TimeoutError, OSError) as e:
                    self.last_error = f"{type(e).__name__}: {e}"
                finally:
                    await self._disconnect()
                if self.replay is not None:
                    self._finished = True
                    break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), self.cfg.reconnect_s)

    async def _connect(self) -> bool:
        try:
            if self.replay is not None:
                self.channel = self.replay.start()
                self.state = "replay"
            else:
                if self.spec.fd is not None:
                    link = SerialLink.from_fd(self.spec.fd, raw=False, owns_fd=False, path=self.spec.label)
                else:
                    if not self.spec.path or not os.path.exists(self.spec.path):
                        self.state = "disconnected"
                        return False
                    link = SerialLink.open(self.spec.path, self.cfg.baud, exclusive=self.cfg.exclusive)
                self.channel = AsyncLineChannel(link).start()
                self.state = "waiting"
        except OSError as e:
            self.state = "busy" if e.errno == 16 else "error"
            msg = f"open failed: {os.strerror(e.errno) if e.errno else e}"
            if msg != self.last_error:
                log.warning("[%s] %s", self.name, msg)
            self.last_error = msg
            return False
        self.counters["connect_attempts"] += 1
        self.times["connected_at_utc"] = P.utc_now_iso()
        self.sent.clear()                # re-plugged/rebooted device must get pending commands again
        self.hello = None
        self.chamber_id = self.mapped
        self.blocked_reason = None
        self.last_rx_mono = self.last_proto_mono = None
        if self.spec.fd is None:
            log.info("[%s] port open (%d baud)", self.name, self.cfg.baud)
        if self.replay is None:
            await self._send(P.time_msg())  # device may use host time once it has it
        return True

    async def _disconnect(self) -> None:
        if self.channel is not None:
            reason = self.channel.closed_reason
            with contextlib.suppress(Exception):
                await self.channel.close()
            self.channel = None
            if self.replay is None and self.last_rx_mono is not None:   # only for links that were live
                self.counters["disconnects"] += 1
                self.times["disconnected_at_utc"] = P.utc_now_iso()
                log.warning("[%s] link lost (%s); will reconnect", self.name, reason or self.last_error)
        if self.replay is None:
            self.state = "disconnected"
        else:
            self.state = "replay_finished"
        self.bridge.release(self)

    # ---------------------------------------------------------------- main loop
    async def _serve(self, stop: asyncio.Event) -> None:
        cfg = self.cfg
        now = time.monotonic()
        next_poll, next_time, next_ping = now + 0.2, now + cfg.time_interval_s, now + cfg.ping_interval_s
        while not stop.is_set():
            try:
                line = await self.channel.readline(timeout=0.1)
            except TimeoutError:
                line = b""
            if line is None:
                raise LinkClosed(self.channel.closed_reason or "closed")
            if line:
                await self._handle_line(line)
            now = time.monotonic()
            if self.replay is None:
                if self.chamber_id and not self.blocked_reason and now >= next_poll:
                    next_poll = now + cfg.poll_interval_s
                    await self._poll_commands()
                if now >= next_time:
                    next_time = now + cfg.time_interval_s
                    await self._send(P.time_msg())
                if now >= next_ping:
                    next_ping = now + cfg.ping_interval_s
                    self._ping_seq += 1
                    await self._send(P.ping(self._ping_seq))
            if self.outbox and now >= self._retry_at:
                await self._flush_outbox()
            self._expire_images(now)
            self._update_state(now)

    def _update_state(self, now: float) -> None:
        if self.replay is not None:
            return
        if self.blocked_reason:
            self.state = "conflict"
        elif self.last_rx_mono is None:
            self.state = "waiting"
        elif self.last_proto_mono is None:
            self.state = "logs_only"
        elif now - self.last_proto_mono > self.cfg.silent_after_s:
            self.state = "silent"
        else:
            self.state = "connected"

    async def _send(self, msg: dict) -> bool:
        if self.channel is None:
            return False
        data = P.encode(msg)
        await self.channel.write_line(data)
        self.counters[f"tx_{msg['type']}"] += 1
        self.bridge.record(self.name, "tx", data.decode().rstrip("\n"))
        return True

    # ---------------------------------------------------------------- device lines
    async def _handle_line(self, raw: bytes) -> None:
        now = time.monotonic()
        self.last_rx_mono = now
        self.times["last_rx_utc"] = P.utc_now_iso()
        self.counters["rx_lines"] += 1
        self.bridge.record(self.name, "rx", raw.decode("utf-8", errors="replace"))
        p = P.parse_line(raw)
        if p.kind == "log":
            self.counters["rx_log_text"] += 1
            self._log(p.text)
            return
        if p.kind == "invalid":
            self.counters["rx_invalid"] += 1
            self.last_error = p.error
            log.debug("[%s] ignored line: %s", self.name, p.error)
            return
        msg = p.msg
        self.last_proto_mono = now
        self.counters[f"rx_{msg['type']}"] += 1
        if p.nan_fixed:
            self.counters["nan_replaced"] += 1
        handler = getattr(self, f"_on_{msg['type']}")
        try:
            await handler(msg, p)
        except (LinkClosed, TimeoutError):
            raise
        except Exception as e:  # one bad message must never kill the port
            self.counters["handler_errors"] += 1
            self.last_error = f"{msg['type']}: {type(e).__name__}: {e}"
            log.exception("[%s] handling %s failed", self.name, msg["type"])

    def _log(self, text: str, level: str = "info") -> None:
        text = text[:500]
        self.logs.append(text)
        log.log(logging.WARNING if level in ("warn", "error") else logging.INFO,
                "[%s%s] device: %s", self.name, f"/{self.chamber_id}" if self.chamber_id else "", text)

    async def _on_log(self, msg: dict, _p) -> None:
        level = str(msg.get("level") or "info").lower()
        self._log(str(msg.get("msg", "")), level)

    async def _on_ping(self, msg: dict, _p) -> None:
        await self._send(P.pong(msg.get("seq")))

    async def _on_pong(self, msg: dict, _p) -> None:
        return None

    async def _on_hello(self, msg: dict, _p) -> None:
        ch = msg.get("chamber_id")
        target = self.mapped or ch
        if self.mapped and ch and ch != self.mapped:
            self._block(f"hello says chamber {ch!r} but port is mapped to {self.mapped!r}")
            return
        if target not in self.cfg.chambers:
            self._block(f"unknown chamber {target!r} (configured: {', '.join(self.cfg.chambers)})")
            return
        owner = self.bridge.claim(self, target)
        if owner is not None:
            self._block(f"chamber {target} is already attached on {owner.name}")
            return
        boot = msg.get("boot_id")
        if boot and boot != self.boot_id:
            self.sent.clear()
        self.blocked_reason = None
        self.hello = msg
        self.chamber_id = self.last_chamber_id = target
        self.device_id = msg.get("device_id") or self.device_id or self.cfg.chambers[target]
        self.boot_id = boot or self.boot_id
        self.firmware_version = msg.get("firmware_version")
        self.capabilities = [str(c) for c in (msg.get("capabilities") or [])][:32]
        self.simulated = bool(msg.get("simulated")) or "simulated" in self.capabilities
        self.times["hello_at_utc"] = P.utc_now_iso()
        log.info("[%s] hello: chamber %s device %s fw %s boot %s%s", self.name, target, self.device_id,
                 self.firmware_version, self.boot_id, " (SIMULATED)" if self.simulated else "")
        if self.replay is None:
            await self._send(P.hello_ack(target, self.cfg.poll_interval_s, self.device_id))
            await self._send(P.time_msg())

    def _block(self, why: str) -> None:
        if why != self.blocked_reason:
            log.error("[%s] %s; not forwarding this port", self.name, why)
        self.blocked_reason = why
        self.last_error = why
        self.counters["blocked_messages"] += 1

    def _resolve_chamber(self, payload: dict) -> str | None:
        ch = payload.get("chamber_id")
        if self.blocked_reason:
            return None
        if self.chamber_id is None and ch in self.cfg.chambers:
            if self.bridge.claim(self, ch) is None:      # fallback before hello: trust the payload
                self.chamber_id = self.last_chamber_id = ch
        if self.chamber_id is None or ch != self.chamber_id:
            self.counters["chamber_mismatch"] += 1
            self.last_error = f"payload chamber_id {ch!r} != port chamber {self.chamber_id!r}; dropped"
            return None
        return ch

    async def _on_telemetry(self, msg: dict, p) -> None:
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            self.counters["rx_invalid"] += 1
            return
        ch = self._resolve_chamber(payload)
        if ch is None:
            return
        payload = dict(payload)
        if self.replay is not None:
            payload["source"] = "replay"
            if payload.get("boot_id"):
                payload["boot_id"] = f"replay-{payload['boot_id']}"[:64]
        elif self.simulated:
            payload["source"] = "simulated"
        elif payload.get("source") in (None, "", "real"):
            payload["source"] = "measured"
        payload.setdefault("device_id", self.device_id or self.cfg.chambers.get(ch))
        if p.nan_fixed:
            payload["quality_flags"] = [*(payload.get("quality_flags") or []), "bridge_nan_replaced_with_null"]
        self.times["last_telemetry_utc"] = P.utc_now_iso()
        await self._post("telemetry", "/api/v1/telemetry", payload)

    async def _on_ack(self, msg: dict, _p) -> None:
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            self.counters["rx_invalid"] += 1
            return
        if self._resolve_chamber(payload) is None:
            return
        payload = dict(payload)
        if self.replay is not None:
            self.counters["acks_not_forwarded_in_replay"] += 1
            return
        if payload.get("device_id") is None and self.device_id:
            payload["device_id"] = self.device_id
        self.times["last_ack_utc"] = P.utc_now_iso()
        await self._post("ack", "/api/v1/control-ack", payload)

    async def _on_image(self, msg: dict, _p) -> None:
        ch = self._resolve_chamber({"chamber_id": msg.get("chamber_id")})
        if ch is None:
            return
        sha = str(msg.get("sha256") or "")
        try:
            i, n = (int(x) for x in str(msg.get("chunk", "1/1")).split("/"))
            total = int(msg.get("bytes") or 0)
            if msg.get("encoding", "base64-chunks") != "base64-chunks" or not (1 <= i <= n <= 1000):
                raise ValueError("bad chunk header")
            if not (0 < total <= MAX_IMAGE_BYTES) or len(sha) != 64:
                raise ValueError("bad size/sha256")
            part = base64.b64decode(str(msg.get("data") or ""), validate=True)
        except (ValueError, TypeError) as e:
            self.counters["image_errors"] += 1
            self.last_error = f"image chunk rejected: {e}"
            return
        img = self.images.setdefault(sha, {"n": n, "chunks": {}, "bytes": total, "started": time.monotonic(),
                                           "meta": msg})
        img["chunks"][i] = part
        if len(img["chunks"]) < img["n"]:
            return
        del self.images[sha]
        data = b"".join(img["chunks"][k] for k in range(1, img["n"] + 1))
        if len(data) != img["bytes"] or hashlib.sha256(data).hexdigest() != sha:
            self.counters["image_errors"] += 1
            self.last_error = "image reassembly failed (size/sha256 mismatch)"
            return
        source = "replay" if self.replay is not None else ("simulated" if self.simulated else
                                                           msg.get("source") or "measured")
        params = {"chamber_id": ch, "source": "measured" if source == "real" else source}
        if self.device_id:
            params["device_id"] = self.device_id
        for k in ("timestamp_utc", "experiment_id"):
            if msg.get(k):
                params[k] = msg[k]
        status, body = await self._http("POST", "/api/v1/images", ch, params=params, content=data,
                                        headers={"content-type": "image/jpeg"})
        self.counters["images_ok" if status == 200 else "images_failed"] += 1
        if status != 200:
            self.last_error = f"image upload -> {status} {str(body)[:200]}"

    def _expire_images(self, now: float) -> None:
        for sha in [s for s, im in self.images.items() if now - im["started"] > 60]:
            del self.images[sha]
            self.counters["image_timeouts"] += 1

    # ---------------------------------------------------------------- HTTP
    def _headers(self, chamber: str) -> dict[str, str]:
        token = self.cfg.device_tokens.get(chamber) or self.cfg.operator_token
        h = {"Authorization": f"Bearer {token}"}
        dev = self.device_id or self.cfg.chambers.get(chamber)
        if dev:
            h["X-Device-Id"] = dev
        return h

    async def _http(self, method: str, path: str, chamber: str, headers: dict | None = None,
                    **kw) -> tuple[int | None, Any]:
        try:
            r = await self.bridge.http.request(method, path, headers={**self._headers(chamber), **(headers or {})},
                                               **kw)
        except httpx.HTTPError as e:
            return None, f"{type(e).__name__}: {e}"
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    async def _post(self, kind: str, path: str, payload: dict, *, from_outbox: bool = False) -> bool:
        ch = payload.get("chamber_id")
        status, body = await self._http("POST", path, ch, json=payload)
        if status is not None and status < 300:
            dup = isinstance(body, dict) and body.get("duplicate")
            self.counters[f"{kind}_duplicate" if dup else f"{kind}_ok"] += 1
            return True
        if status is None or status >= 500:          # backend unreachable: keep and retry in order
            self.counters[f"{kind}_retry"] += 1
            self.last_error = f"{kind} -> {status or 'network'}: {str(body)[:200]}"
            if not from_outbox:
                if len(self.outbox) == self.outbox.maxlen:
                    self.counters["outbox_dropped"] += 1
                self.outbox.append((kind, path, payload))
            self._retry_at = time.monotonic() + 2.0
            return False
        self.counters[f"{kind}_rejected"] += 1         # 4xx: the payload itself is wrong; don't retry
        self.last_error = f"{kind} rejected {status}: {str(body)[:300]}"
        log.warning("[%s] %s", self.name, self.last_error)
        return True

    async def _flush_outbox(self) -> None:
        while self.outbox:
            kind, path, payload = self.outbox[0]
            if not await self._post(kind, path, payload, from_outbox=True):
                return
            self.outbox.popleft()

    async def _poll_commands(self) -> None:
        ch = self.chamber_id
        status, body = await self._http("GET", f"/api/v1/chambers/{ch}/desired-control", ch)
        if status != 200 or not isinstance(body, dict):
            self.counters["poll_errors"] += 1
            self.last_error = f"desired-control -> {status}: {str(body)[:200]}"
            return
        self.counters["polls"] += 1
        server_time = body.get("server_time_utc") or P.utc_now_iso()
        now = time.monotonic()
        pending = sorted((c for c in body.get("commands") or [] if isinstance(c, dict)),
                         key=lambda c: c.get("sequence_number") or 0)
        for cmd in pending:
            cid = cmd.get("command_id")
            if not cid:
                continue
            st = self.sent.get(cid)
            if st is not None and (st["count"] >= self.cfg.max_sends_per_command
                                   or now - st["last"] < self.cfg.resend_after_s):
                continue
            await self._send(P.command(cmd, server_time))
            if st is None:
                self.sent[cid] = {"first": now, "last": now, "count": 1, "action": cmd.get("action")}
                self.counters["commands_sent"] += 1
            else:
                st["last"], st["count"] = now, st["count"] + 1
                self.counters["commands_resent"] += 1
        while len(self.sent) > 500:
            self.sent.popitem(last=False)

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "port": self.name, "chamber_id": self.chamber_id, "mapped_chamber_id": self.mapped,
            "link": self.state, "device_id": self.device_id, "boot_id": self.boot_id,
            "firmware_version": self.firmware_version, "simulated": self.simulated,
            "capabilities": self.capabilities, "blocked_reason": self.blocked_reason,
            **self.times, "counters": dict(self.counters), "outbox": len(self.outbox),
            "last_error": self.last_error, "last_log": self.logs[-1] if self.logs else None,
            "recent_logs": list(self.logs)[-5:],
        }


class Bridge:
    def __init__(self, cfg: BridgeConfig, *, http_client: httpx.AsyncClient | None = None,
                 replay: tuple[Path, float] | None = None):
        self.cfg = cfg
        self._own_http = http_client is None
        self.http = http_client or httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.http_timeout_s)
        self.sessions: list[PortSession] = []
        self.owners: dict[str, PortSession] = {}
        self.started_at = P.utc_now_iso()
        self._tasks: list[asyncio.Task] = []
        self._replay = replay
        self._record_fh = open(cfg.record_path, "a", encoding="utf-8") if cfg.record_path else None
        self._t0 = time.monotonic()

    # chamber ownership: one live port per chamber
    def claim(self, session: PortSession, chamber: str) -> PortSession | None:
        owner = self.owners.get(chamber)
        if owner is not None and owner is not session and owner.channel is not None:
            return owner
        self.owners[chamber] = session
        return None

    def release(self, session: PortSession) -> None:
        for ch in [c for c, s in self.owners.items() if s is session]:
            del self.owners[ch]

    def record(self, port: str, direction: str, line: str) -> None:
        if self._record_fh:
            self._record_fh.write(json.dumps({"t": round(time.monotonic() - self._t0, 3), "port": port,
                                              "dir": direction, "line": line}) + "\n")
            self._record_fh.flush()

    def add_port(self, spec: PortSpec, stop: asyncio.Event, replay: ReplayChannel | None = None) -> PortSession:
        s = PortSession(self, spec, replay=replay)
        self.sessions.append(s)
        self._tasks.append(asyncio.create_task(s.run(stop), name=f"port:{s.name}"))
        return s

    def status(self) -> dict:
        chambers = {}
        for cid, dev in self.cfg.chambers.items():
            s = self.owners.get(cid) or next((x for x in self.sessions if x.last_chamber_id == cid), None)
            link = s.state if s else "not_attached"
            chambers[cid] = {
                "link": link, "port": s.name if s else None,
                "device_id": (s.device_id if s else None) or dev,
                "boot_id": s.boot_id if s else None, "firmware_version": s.firmware_version if s else None,
                "simulated": s.simulated if s else None,
                "last_seen_utc": s.times["last_rx_utc"] if s else None,
                "last_telemetry_utc": s.times["last_telemetry_utc"] if s else None,
                "label": "USB · " + {"connected": "connected", "waiting": "waiting for device",
                                     "logs_only": "device printing logs only", "silent": "silent",
                                     "disconnected": "disconnected", "not_attached": "no device",
                                     "conflict": "chamber conflict", "busy": "port busy",
                                     "error": "port error", "replay": "replay",
                                     "replay_finished": "replay finished"}.get(link, link),
            }
        return {"transport": "usb-serial", "protocol": P.PROTOCOL_NAME, "protocol_version": P.PROTOCOL_VERSION,
                "mode": "replay" if self._replay else "live", "base_url": self.cfg.base_url,
                "pid": os.getpid(), "started_at_utc": self.started_at, "updated_at_utc": P.utc_now_iso(),
                "ports": [s.status() for s in self.sessions], "chambers": chambers}

    def write_status(self) -> None:
        if not self.cfg.status_path:
            return
        path = Path(self.cfg.status_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.status(), indent=1, default=str))
        os.replace(tmp, path)

    def _summary(self) -> str:
        parts = []
        for cid, c in self.status()["chambers"].items():
            s = self.owners.get(cid) or next((x for x in self.sessions if x.last_chamber_id == cid), None)
            cnt = s.counters if s else Counter()
            parts.append(f"{cid}={c['link']}" + (f"({c['port']}) tel={cnt['telemetry_ok']} cmd={cnt['commands_sent']}"
                                                 f" ack={cnt['ack_ok']} logs={cnt['rx_log_text']}" if s else ""))
        return "bridge: " + " | ".join(parts)

    async def run(self, stop: asyncio.Event | None = None, duration_s: float = 0.0) -> dict:
        stop = stop or asyncio.Event()
        if self._replay:
            path, speed = self._replay
            spec = self.cfg.ports[0] if self.cfg.ports else PortSpec(label=f"replay:{Path(path).name}")
            self.add_port(spec, stop, replay=ReplayChannel(path, speed))
        else:
            for spec in self.cfg.ports:
                self.add_port(spec, stop)
        known = {s.spec.path for s in self.sessions if s.spec.path}
        start = last_status = last_summary = time.monotonic()
        last_discover = start - self.cfg.discover_interval_s
        try:
            while not stop.is_set():
                now = time.monotonic()
                if duration_s and now - start >= duration_s:
                    break
                if self._replay and all(s._finished for s in self.sessions):
                    break
                if self.cfg.discover and now - last_discover >= self.cfg.discover_interval_s:
                    last_discover = now
                    for p in discover_ports(self.cfg.patterns, self.cfg.exclude | known):
                        known.add(p)
                        log.info("discovered serial port %s", p)
                        self.add_port(PortSpec(path=p, chamber_id=self.cfg.port_map.get(p)), stop)
                if now - last_status >= self.cfg.status_interval_s:
                    last_status = now
                    self.write_status()
                if now - last_summary >= self.cfg.summary_interval_s:
                    last_summary = now
                    log.info(self._summary())
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), 0.2)
        finally:
            stop.set()
            for t in self._tasks:
                with contextlib.suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(t, 3.0)
            self.write_status()
            log.info(self._summary())
            if self._own_http:
                await self.http.aclose()
            if self._record_fh:
                self._record_fh.close()
        return self.status()
