"""FermentAI serial protocol v1 (contracts/serial_v1.md): newline-delimited JSON over USB CDC.

One UTF-8 JSON object per line, <= MAX_LINE_BYTES including the trailing "\\n":
    {"type": "<message type>", "v": "1.0", ...}
Lines that are not JSON objects (boot ROM output, Serial.println debugging) are treated as
device log text and never break the link.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PROTOCOL_NAME = "serial_v1"
PROTOCOL_VERSION = "1.0"
DEFAULT_BAUD = 115200
MAX_LINE_BYTES = 8192
DEVICE_TYPES = frozenset({"hello", "telemetry", "ack", "image", "log", "ping", "pong"})
HOST_TYPES = frozenset({"hello_ack", "command", "time", "ping", "pong"})
LOG_LEVELS = ("debug", "info", "warn", "error")


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(obj: Any) -> tuple[Any, bool]:
    """Replace NaN/Infinity (which Python's json accepts but the API rejects) with None."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None, True
    if isinstance(obj, dict):
        changed = False
        out = {}
        for k, v in obj.items():
            out[k], c = _finite(v)
            changed |= c
        return out, changed
    if isinstance(obj, list):
        changed = False
        out = []
        for v in obj:
            nv, c = _finite(v)
            out.append(nv)
            changed |= c
        return out, changed
    return obj, False


def encode(msg: dict) -> bytes:
    """One protocol line. Raises ValueError if it would exceed MAX_LINE_BYTES."""
    msg = {"type": msg["type"], "v": PROTOCOL_VERSION, **{k: v for k, v in msg.items() if k != "type"}}
    data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(data) > MAX_LINE_BYTES:
        raise ValueError(f"{msg['type']} line is {len(data)} bytes > {MAX_LINE_BYTES}")
    return data


class LineFramer:
    """Accumulates raw bytes and yields complete lines (without line terminators).

    * accepts "\\n" and "\\r\\n"; empty lines are skipped;
    * a line longer than max_bytes is dropped (counted in `overlong`) up to its next newline,
      so one corrupt burst can never make the buffer grow without bound.
    """

    def __init__(self, max_bytes: int = MAX_LINE_BYTES):
        self.max_bytes = max_bytes
        self.buf = bytearray()
        self.discarding = False
        self.overlong = 0

    def feed(self, data: bytes) -> list[bytes]:
        lines: list[bytes] = []
        self.buf.extend(data)
        while True:
            i = self.buf.find(b"\n")
            if i < 0:
                if len(self.buf) > self.max_bytes:
                    if not self.discarding:
                        self.overlong += 1
                    self.discarding = True
                    self.buf.clear()
                return lines
            line = bytes(self.buf[:i]).rstrip(b"\r")
            del self.buf[: i + 1]
            if self.discarding:          # tail of an overlong line
                self.discarding = False
                continue
            if len(line) + 1 > self.max_bytes:
                self.overlong += 1
                continue
            if line.strip():
                lines.append(line)


@dataclass
class Parsed:
    kind: str                     # "msg" | "log" | "invalid"
    msg: dict | None = None
    text: str = ""
    error: str | None = None
    nan_fixed: bool = False
    extra: dict = field(default_factory=dict)


def parse_line(raw: bytes, allowed: frozenset[str] = DEVICE_TYPES) -> Parsed:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        return Parsed("log", text=text)
    try:
        obj = json.loads(text)
    except ValueError:
        return Parsed("log", text=text, error="not valid JSON")   # e.g. a truncated line
    if not isinstance(obj, dict):
        return Parsed("invalid", text=text, error="JSON line is not an object")
    t = obj.get("type")
    if t not in allowed:
        return Parsed("invalid", text=text[:200], error=f"unknown message type {t!r}")
    v = str(obj.get("v", PROTOCOL_VERSION))
    if v.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
        return Parsed("invalid", text=text[:200], error=f"unsupported protocol version {v!r}")
    obj, fixed = _finite(obj)
    return Parsed("msg", msg=obj, text=text, nan_fixed=fixed)


# ---------------------------------------------------------------------------- host -> device
def hello_ack(chamber_id: str, poll_interval_s: float, device_id: str | None = None,
              server_time_utc: str | None = None) -> dict:
    return {"type": "hello_ack", "server_time_utc": server_time_utc or utc_now_iso(),
            "chamber_id": chamber_id, "device_id": device_id, "poll_interval_s": poll_interval_s}


def command(payload: dict, server_time_utc: str) -> dict:
    """payload = one command_v1 object; server_time_utc = the desired-control response's
    server_time_utc, so the device can compute remaining validity without a clock."""
    return {"type": "command", "server_time_utc": server_time_utc, "payload": payload}


def time_msg(server_time_utc: str | None = None) -> dict:
    return {"type": "time", "server_time_utc": server_time_utc or utc_now_iso()}


def ping(seq: int) -> dict:
    return {"type": "ping", "seq": seq}


def pong(seq: Any) -> dict:
    return {"type": "pong", "seq": seq}
