"""CLI for the USB-serial bridge. See `uv run python -m fermentai.bridge -h`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from .bridge import Bridge, BridgeConfig, PortSpec
from .protocol import DEFAULT_BAUD
from .serial_link import DEFAULT_PORT_PATTERNS

log = logging.getLogger("fermentai.bridge")

EPILOG = """\
Ports: with no --port, ports matching --pattern (default /dev/cu.usbmodem*, /dev/ttyACM*) are
discovered every 2 s and attached when they appear (USB re-plug included). Each port is mapped
to a chamber by the device's `hello` (or --map/--port PATH=CHAMBER, which is authoritative).

Auth: per chamber FERMENTAI_DEVICE_TOKEN_<A|B>, else FERMENTAI_API_TOKEN (from env / .env).
Status: data/telemetry/bridge_status.json (also GET /api/v1/bridge/status).

Examples:
  uv run python -m fermentai.bridge
  uv run python -m fermentai.bridge --port /dev/cu.usbmodem2101=A --port /dev/cu.usbmodem2201=B
  uv run python -m fermentai.bridge --map /dev/cu.usbmodem2101=A          # discover, pin one
  uv run python -m fermentai.bridge --port /dev/cu.usbmodem2101 --record data/telemetry/serial_A.jsonl
  uv run python -m fermentai.bridge --replay data/telemetry/serial_A.jsonl --replay-speed 10
"""


def _split(item: str) -> tuple[str, str | None]:
    path, sep, ch = item.rpartition("=")
    return (path, ch.upper()) if sep and path else (item, None)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m fermentai.bridge",
                                 description="FermentAI USB-serial <-> HTTP bridge (serial_v1).",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get(
        "FERMENTAI_BASE_URL", f"http://127.0.0.1:{os.environ.get('FERMENTAI_PORT', '8000')}"))
    ap.add_argument("--port", action="append", default=[], metavar="PATH[=CHAMBER]",
                    help="serial port to use (repeatable); disables discovery unless --discover")
    ap.add_argument("--map", action="append", default=[], metavar="PATH=CHAMBER",
                    help="pin a (discovered) port to a chamber")
    ap.add_argument("--discover", action="store_true", help="discover ports even when --port is given")
    ap.add_argument("--pattern", action="append", default=None, help="discovery glob (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="never open this port")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("FERMENTAI_SERIAL_BAUD", DEFAULT_BAUD)))
    ap.add_argument("--no-exclusive", action="store_true", help="don't request exclusive port access")
    ap.add_argument("--poll-interval", type=float, default=2.0, help="desired-control poll per chamber, s")
    ap.add_argument("--time-interval", type=float, default=60.0)
    ap.add_argument("--status-file", type=Path, default=None,
                    help="default: <FERMENTAI_DATA_DIR or data>/telemetry/bridge_status.json")
    ap.add_argument("--token", default=None, help="override the operator token")
    ap.add_argument("--record", type=Path, default=None, help="append every rx/tx line (JSONL) here")
    ap.add_argument("--replay", type=Path, default=None,
                    help="replay a recorded device line log (raw lines or --record JSONL); source=replay")
    ap.add_argument("--replay-speed", type=float, default=0.0, help="0 = as fast as possible")
    ap.add_argument("--parse-status-log", action=argparse.BooleanOptionalAction, default=True,
                    help="convert legacy fw 0.3.x plain-text status blocks into measured telemetry "
                         "(default on; disabled per port once the device speaks serial_v1)")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = forever)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def config_from_args(args: argparse.Namespace) -> BridgeConfig:
    from fermentai.api.config import REPO_ROOT, load_dotenv, load_hardware_config

    load_dotenv()
    hw = load_hardware_config()
    data_dir = Path(os.environ.get("FERMENTAI_DATA_DIR", "data"))
    data_dir = data_dir if data_dir.is_absolute() else REPO_ROOT / data_dir
    ports = [PortSpec(path=p, chamber_id=c) for p, c in map(_split, args.port)]
    port_map = {p: c for p, c in map(_split, args.map) if c}
    for spec in ports:
        spec.chamber_id = spec.chamber_id or port_map.get(spec.path or "")
    for spec in ports + [PortSpec(chamber_id=c) for c in port_map.values()]:
        if spec.chamber_id and spec.chamber_id not in hw.chambers:
            raise SystemExit(f"unknown chamber {spec.chamber_id!r}; configured: {', '.join(hw.chambers)}")
    tokens = {c: t for c in hw.chambers if (t := os.environ.get(f"FERMENTAI_DEVICE_TOKEN_{c.upper()}", "").strip())}
    return BridgeConfig(
        base_url=args.base_url.rstrip("/"),
        ports=ports,
        discover=(not ports or args.discover) and args.replay is None,
        patterns=tuple(args.pattern) if args.pattern else DEFAULT_PORT_PATTERNS,
        port_map=port_map,
        exclude=set(args.exclude),
        baud=args.baud,
        exclusive=not args.no_exclusive,
        poll_interval_s=args.poll_interval,
        time_interval_s=args.time_interval,
        status_path=args.status_file or data_dir / "telemetry" / "bridge_status.json",
        operator_token=(args.token or os.environ.get("FERMENTAI_API_TOKEN") or "change-me-local-device-token").strip(),
        device_tokens=tokens,
        chambers={c: (cfg.device_id or f"fermentai-esp32-{c.lower()}") for c, cfg in hw.chambers.items()},
        record_path=args.record,
        parse_status_log=args.parse_status_log,
    )


async def run(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    if args.replay:
        log.info("REPLAY %s -> %s (telemetry labelled source=replay; no commands sent)", args.replay, cfg.base_url)
    else:
        log.info("USB-serial bridge -> %s; ports: %s%s", cfg.base_url,
                 ", ".join(f"{s.path}={s.chamber_id or '?'}" for s in cfg.ports) or "(none given)",
                 f"; discovering {', '.join(cfg.patterns)}" if cfg.discover else "")
    bridge = Bridge(cfg, replay=(args.replay, args.replay_speed) if args.replay else None)
    await bridge.run(stop, duration_s=args.duration)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
