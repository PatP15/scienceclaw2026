"""Outbound-network audit for the FermentAI server process (PLAN §8 / PLAN_MASTER §12).

Runs uvicorn with a CPython audit hook (PEP 578) that records every outbound socket operation
the server process attempts — DNS lookups (``socket.getaddrinfo`` / ``gethostbyname``),
``socket.connect`` and ``socket.sendto`` — as one JSON line each. Inbound connections accepted
by the server are not "connects" and are not recorded.

The asyncio event loop is forced (``--loop asyncio``): uvloop/libuv opens sockets in C and would
bypass the audit hook, so auditing under uvloop would be incomplete.

    uv run python -m tests.integration.net_audit --log /tmp/net.jsonl --host 0.0.0.0 --port 8765
    uv run python -m tests.integration.net_audit --check /tmp/net.jsonl   # exit 1 on external traffic

``classify()`` treats loopback, RFC 1918 / link-local / ULA addresses, ``.local`` mDNS names and
the machine's own hostname as LAN. Anything else (a public IP or an internet hostname) is a
violation.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

AUDITED = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
           "socket.gethostbyaddr", "socket.connect", "socket.sendto", "socket.sendmsg"}


def _host_of(event: str, args: tuple) -> tuple[str | None, object]:
    if event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
        addr = args[1] if len(args) > 1 else None
        if isinstance(addr, tuple) and addr:
            return str(addr[0]), addr[1] if len(addr) > 1 else None
        return (None if addr is None else str(addr)), None   # AF_UNIX path etc.
    host = args[0] if args else None
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    port = args[1] if event == "socket.getaddrinfo" and len(args) > 1 else None
    return (None if host is None else str(host)), port


def install(log_path: str | os.PathLike) -> None:
    """Install the audit hook; every audited event is appended to ``log_path`` (JSONL)."""
    lock = threading.Lock()
    fh = open(log_path, "a", buffering=1)  # noqa: SIM115 - lives for the process lifetime
    busy = threading.local()

    def hook(event: str, args: tuple) -> None:
        if event not in AUDITED or getattr(busy, "on", False):
            return
        busy.on = True
        try:
            host, port = _host_of(event, args)
            rec = {"t": round(time.time(), 3), "pid": os.getpid(), "event": event, "host": host,
                   "port": port if isinstance(port, (int, str)) or port is None else str(port)}
            with lock:
                fh.write(json.dumps(rec) + "\n")
        except Exception:  # noqa: BLE001, S110 - never break the server because of the audit
            pass
        finally:
            busy.on = False

    sys.addaudithook(hook)


def _own_names() -> set[str]:
    names = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", ""}
    try:
        names.add(socket.gethostname().lower())
    except OSError:
        pass
    return names


def classify(host: str | None) -> str:
    """'lan' or 'external'. Unix-socket paths / unspecified hosts count as local."""
    if host is None or host.startswith("/"):
        return "lan"
    h = host.strip("[]").split("%")[0].lower()
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if h in _own_names() or h.endswith(".local") or "." not in h:
            return "lan"
        return "external"
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return "lan"
    return "external"


def read_log(log_path: str | os.PathLike) -> list[dict]:
    p = Path(log_path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def violations(log_path: str | os.PathLike) -> list[dict]:
    return [r for r in read_log(log_path) if classify(r.get("host")) == "external"]


def summarize(log_path: str | os.PathLike) -> dict:
    recs = read_log(log_path)
    by_host: dict[str, int] = {}
    for r in recs:
        key = f"{r['event']} {r.get('host')}"
        by_host[key] = by_host.get(key, 0) + 1
    return {"events": len(recs), "external": violations(log_path), "by_event_host": by_host}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, help="JSONL audit log to append to (runs the server)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--check", type=Path, help="only check an existing log; exit 1 on external traffic")
    args = ap.parse_args(argv)
    if args.check:
        s = summarize(args.check)
        print(json.dumps(s, indent=2))
        return 1 if s["external"] else 0
    if not args.log:
        ap.error("--log or --check is required")
    install(args.log)
    import uvicorn

    uvicorn.run("fermentai.api.app:app", host=args.host, port=args.port, loop="asyncio",
                log_level=os.environ.get("FERMENTAI_LOG_LEVEL", "warning"),
                timeout_graceful_shutdown=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
