"""Wire-level recording and replay of PreForm API traffic.

Two small HTTP servers, both bound to 127.0.0.1 on an ephemeral port:

``RecordingProxy``
    Sits between the official Formlabs SDK and a *real* PreFormServer. Every request is forwarded
    unchanged and logged (method, path, query, JSON body, status, JSON response, duration). The
    resulting transcript is the evidence of what the live API actually did, and it is the
    recording that :class:`MockPreFormServer` replays.

``MockPreFormServer``
    Replays a recorded transcript so the same SDK code path can run in tests without the
    PreFormServer binary. It answers requests in recorded order per ``(method, path)`` (and per
    ``operation_id`` for ``/v1/operation/get/``). Endpoints that write files on the server's
    machine (``/v1/scene/save/``, ``/v1/scene/render/``) write clearly-marked placeholders, never a
    fake ``.form``. Anything produced through the mock is provenance ``replay``.

Neither server ever answers ``/v1/scene/print/``: the proxy refuses to forward it unless the
caller explicitly allows it, and the mock always refuses it.
"""

from __future__ import annotations

import json
import struct
import threading
import time
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

PRINT_PATH = "/v1/scene/print/"
FILE_WRITING_PATHS = {"/v1/scene/save/", "/v1/scene/render/"}
MOCK_FORM_BYTES = (
    b"FERMENTAI MOCK PLACEHOLDER - NOT A PREFORM .form FILE.\n"
    b"Written by fermentai.formlabs.replay.MockPreFormServer replaying a recorded session.\n"
    b"Run scripts/prepare_print.py with a real PreFormServer to produce a real .form file.\n"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _decode(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"_non_json_bytes": len(body)}


def placeholder_png(width: int = 64, height: int = 64, grey: int = 200) -> bytes:
    """A tiny valid greyscale PNG (no Pillow needed) used for mock renders."""
    raw = b"".join(b"\x00" + bytes([grey]) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@dataclass
class _Background:
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _serve(handler_cls: type[BaseHTTPRequestHandler]) -> _Background:
    server = _QuietServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, name=handler_cls.__name__, daemon=True)
    thread.start()
    return _Background(server, thread)


# ---------------------------------------------------------------------------------------------
# Recording proxy (live)
# ---------------------------------------------------------------------------------------------


@dataclass
class RecordingProxy:
    """Forward to a real PreFormServer and record every exchange."""

    upstream: str = "http://localhost:44388"
    allow_print: bool = False
    entries: list[dict] = field(default_factory=list)
    _bg: _Background | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._bg is None:
            raise RuntimeError("proxy not started")
        return self._bg.base_url

    def start(self) -> None:
        import httpx  # dependency of the SDK and of fermentai itself

        proxy = self
        client = httpx.Client(base_url=self.upstream, timeout=httpx.Timeout(900.0, connect=10.0))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence stderr access log
                pass

            def _forward(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                url = urlsplit(self.path)
                t0 = time.perf_counter()
                started = _now()
                if url.path == PRINT_PATH and not proxy.allow_print:
                    payload = json.dumps({"error": {
                        "code": "FERMENTAI_PRINT_BLOCKED",
                        "message": "fermentai blocks /v1/scene/print/ unless a human confirmed "
                                   "(--i-confirm-print and FERMENTAI_FORMLABS_CONFIRM_PRINT)."}})
                    status, resp_body, headers = 403, payload.encode(), {
                        "Content-Type": "application/json"}
                else:
                    fwd_headers = {k: v for k, v in self.headers.items()
                                   if k.lower() in ("content-type", "accept")}
                    r = client.request(self.command, self.path, content=body or None,
                                       headers=fwd_headers)
                    status, resp_body = r.status_code, r.content
                    headers = {k: v for k, v in r.headers.items()
                               if k.lower() in ("content-type", "location")}
                entry = {
                    "seq": None,
                    "started_utc": started,
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "method": self.command,
                    "path": url.path,
                    "query": {k: v[0] for k, v in parse_qs(url.query).items()},
                    "request": _decode(body),
                    "status": status,
                    "location": headers.get("location") or headers.get("Location"),
                    "response": _decode(resp_body),
                }
                with proxy._lock:
                    entry["seq"] = len(proxy.entries)
                    proxy.entries.append(entry)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)

            do_GET = do_POST = do_DELETE = do_PUT = _forward

        self._client = client
        self._bg = _serve(Handler)

    def stop(self) -> None:
        if self._bg is not None:
            self._bg.stop()
            self._bg = None
            self._client.close()

    def transcript(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self.entries]


def endpoints_used(entries: list[dict]) -> list[dict]:
    """Distinct ``METHOD path`` pairs in first-use order, with call counts."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    order: list[tuple[str, str]] = []
    for e in entries:
        key = (e["method"], e["path"])
        if key not in counts:
            order.append(key)
        counts[key] += 1
    return [{"method": m, "path": p, "calls": counts[(m, p)]} for m, p in order]


def scrub_paths(obj: Any, replacements: dict[str, str]) -> Any:
    """Recursively replace absolute path prefixes (e.g. the repo root) in strings."""
    if isinstance(obj, str):
        for old, new in replacements.items():
            if old:
                obj = obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [scrub_paths(v, replacements) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub_paths(v, replacements) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------------------------
# Mock server (replay)
# ---------------------------------------------------------------------------------------------


@dataclass
class MockPreFormServer:
    """Replay a recorded PreForm API session over HTTP (no PreFormServer binary needed).

    ``transcript`` is a list of entries as written by :class:`RecordingProxy`. Requests are
    answered from per-key queues in recorded order; once a queue is exhausted its last response
    repeats (polling counts differ run to run). Unknown endpoints get a 404 error envelope and are
    listed in :attr:`unmatched`. ``/v1/scene/print/`` is always refused.
    """

    transcript: list[dict]
    path_replacements: dict[str, str] = field(default_factory=dict)
    requests: list[dict] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    _bg: _Background | None = None

    @classmethod
    def from_file(cls, path: str | Path, **kw: Any) -> MockPreFormServer:
        data = json.loads(Path(path).read_text())
        entries = data["entries"] if isinstance(data, dict) else data
        return cls(transcript=entries, **kw)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._bg is None:
            raise RuntimeError("mock not started")
        return self._bg.base_url

    def _queues(self) -> dict[tuple, deque]:
        queues: dict[tuple, deque] = defaultdict(deque)
        for e in self.transcript:
            entry = scrub_paths(e, self.path_replacements) if self.path_replacements else e
            if entry["path"] == "/v1/operation/get/":
                key = ("GET", entry["path"], entry.get("query", {}).get("operation_id"))
            else:
                key = (entry["method"], entry["path"])
            queues[key].append(entry)
        return queues

    def start(self) -> None:
        mock = self
        queues = self._queues()
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: Any, location: str | None = None) -> None:
                body = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                req = _decode(self.rfile.read(length)) if length else None
                url = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                mock.requests.append({"method": self.command, "path": url.path,
                                      "query": query, "request": req})
                if url.path == PRINT_PATH:
                    self._send(403, {"error": {"code": "FERMENTAI_PRINT_BLOCKED",
                                               "message": "MockPreFormServer never prints."}})
                    return
                if url.path == "/v1/operation/get/":
                    key: tuple = ("GET", url.path, query.get("operation_id"))
                else:
                    key = (self.command, url.path)
                with lock:
                    q = queues.get(key)
                    if not q:
                        mock.unmatched.append(f"{self.command} {url.path}")
                        self._send(404, {"error": {"code": "NOT_FOUND", "message":
                                                   f"mock has no recording for {key}"}})
                        return
                    entry = q.popleft() if len(q) > 1 else q[0]
                if url.path in FILE_WRITING_PATHS and isinstance(req, dict) and req.get("file"):
                    target = Path(req["file"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if url.path == "/v1/scene/render/":
                        target.write_bytes(placeholder_png())
                    else:
                        target.write_bytes(MOCK_FORM_BYTES)
                    mock.files_written.append(str(target))
                self._send(entry["status"], entry.get("response"), entry.get("location"))

            do_GET = do_POST = do_DELETE = do_PUT = _handle

        self._bg = _serve(Handler)

    def stop(self) -> None:
        if self._bg is not None:
            self._bg.stop()
            self._bg = None
