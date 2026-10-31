"""Command line for the virtual chamber devices. See `uv run python -m simulated_devices -h`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

import httpx

from .network_client import ContractState, DeviceHttpClient, token_candidates

log = logging.getLogger("simulated_devices")

EPILOG = """\
Everything sent is labelled source="simulated" (quality flag "simulated_device"), or
source="replay" in --replay mode. The thermal plant is a controller-test model with assumed
time constants, NOT a validated physical simulation of the chambers.

Auth: Authorization: Bearer <FERMENTAI_DEVICE_TOKEN_A|B, else FERMENTAI_API_TOKEN> + X-Device-Id.

Examples:
  uv run python -m simulated_devices --chambers A B --base-url http://127.0.0.1:8000 --interval 5
  uv run python -m simulated_devices --chambers A --ph --door-events A:60:30
  uv run python -m simulated_devices --contract v1.0 --duration 120
  uv run python -m simulated_devices --replay recorded.jsonl --replay-speed 10
  uv run python -m simulated_devices --chambers A --transport serial --port /dev/ttys004
      (USB-serial protocol contracts/serial_v1.md; pair with `python -m fermentai.bridge`)
"""


# --profile demo-restaurants: SIMULATED example labs at fictional restaurants (hardware.yaml C-F).
# setpoint_c is only a documentation of the server-side initial setpoint; the device obeys the
# server's desired-control. ambient_c / rh = assumed room conditions; doors = (start_s, dur_s).
DEMO_RESTAURANTS: dict[str, dict] = {
    "C": dict(setpoint_c=21.0, ambient_c=19.0, rh=40.0, seed=303, doors=[(240, 25), (900, 20)]),
    "D": dict(setpoint_c=24.0, ambient_c=23.0, rh=60.0, seed=404, doors=[(420, 30), (1200, 15)]),
    "E": dict(setpoint_c=26.0, ambient_c=25.0, rh=72.0, seed=505, doors=[(120, 20), (780, 25)]),
    "F": dict(setpoint_c=28.0, ambient_c=27.0, rh=80.0, seed=606, doors=[(600, 20), (1500, 30)]),
}


def apply_profile(args: argparse.Namespace) -> None:
    if args.profile == "demo-restaurants":
        args.chambers = list(DEMO_RESTAURANTS)


def parse_door_events(spec: list[str] | None, chambers: list[str]) -> dict[str, list[tuple[float, float]]]:
    """'A:60:30' = chamber A door opens 60 s after boot for 30 s. 'B:120' -> 20 s default.
    Without a chamber prefix ('60:30') the event applies to every chamber."""
    out: dict[str, list[tuple[float, float]]] = {c: [] for c in chambers}
    for item in spec or []:
        for tok in item.replace(";", ",").split(","):
            tok = tok.strip()
            if not tok:
                continue
            parts = tok.split(":")
            targets = chambers
            if parts[0] and not parts[0].replace(".", "").isdigit():
                targets, parts = [parts[0].upper()], parts[1:]
            start = float(parts[0])
            dur = float(parts[1]) if len(parts) > 1 else 20.0
            for c in targets:
                out.setdefault(c, []).append((start, dur))
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m simulated_devices",
                                 description="Virtual FermentAI ESP32 chamber devices (SIMULATED).",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chambers", nargs="+", default=["A", "B"])
    ap.add_argument("--profile", choices=["demo-restaurants"], default=None,
                    help="demo-restaurants: start simulated demo labs C-F (fictional restaurants) "
                         "with distinct presets (overrides --chambers)")
    ap.add_argument("--base-url", default=os.environ.get(
        "FERMENTAI_BASE_URL", f"http://127.0.0.1:{os.environ.get('FERMENTAI_PORT', '8000')}"))
    ap.add_argument("--token", default=None,
                    help="override token (default: FERMENTAI_DEVICE_TOKEN_<X>, then FERMENTAI_API_TOKEN)")
    ap.add_argument("--interval", "--telemetry-interval", dest="interval", type=float, default=5.0,
                    help="telemetry interval in s (default 5)")
    ap.add_argument("--poll-interval", type=float, default=2.0, help="desired-control poll, 1-3 s")
    ap.add_argument("--contract", choices=["v1.0", "v1.1"], default="v1.1",
                    help="telemetry/ack field set (default v1.1, auto-fallback to v1.0 on 422)")
    ap.add_argument("--no-contract-fallback", action="store_true")
    ap.add_argument("--synced-clock", action="store_true",
                    help="send real UTC timestamps (timestamp_quality=synced); default null/unsynced")
    ap.add_argument("--ph", action="store_true", help="simulated pH probe on chamber A (B-F report simulated pH by default)")
    ap.add_argument("--manual-ph", action="append", default=[], metavar="CHAMBER:PH",
                    help="inject one manual pH reading, e.g. A:4.15 (field provenance 'manual')")
    ap.add_argument("--door-events", nargs="+", metavar="C:START:DUR",
                    help="scripted door openings, e.g. A:60:30 B:120:15")
    ap.add_argument("--no-images", action="store_true", help="disable periodic image uploads")
    ap.add_argument("--images", action="store_true", help=argparse.SUPPRESS)  # legacy flag; on by default
    ap.add_argument("--image-interval", type=float, default=300.0)
    ap.add_argument("--save-frames", type=Path, default=None, help="also write uploaded frames here")
    ap.add_argument("--ambient", type=float, default=21.0, help="room temperature, C")
    ap.add_argument("--ferment-time-scale", type=float, default=30.0,
                    help="fermentation clock speed-up for starter rise (default 30x)")
    ap.add_argument("--no-fermentation-model", action="store_true",
                    help="use the logistic rise fallback instead of fermentai simulate()")
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = forever)")
    ap.add_argument("--replay", type=Path, default=None, help="re-send recorded telemetry (csv/jsonl/json)")
    ap.add_argument("--replay-speed", type=float, default=1.0, help="0 = as fast as possible")
    ap.add_argument("--write-fixtures", action="store_true", help="regenerate camera fixtures and exit")
    ap.add_argument("--transport", choices=["http", "serial"], default="http",
                    help="http (default) or serial: speak contracts/serial_v1.md on --port")
    ap.add_argument("--port", action="append", default=[], metavar="[CHAMBER=]PATH",
                    help="serial port / pty slave per chamber (in --chambers order, or CHAMBER=PATH)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def make_configs(args: argparse.Namespace):
    apply_profile(args)
    from . import chamber_A, chamber_B
    from .device import ChamberConfig
    from .virtual_thermal_plant import PlantParams

    manual = {}
    for item in args.manual_ph:
        c, _, v = item.partition(":")
        manual[c.upper()] = float(v)
    doors = parse_door_events(args.door_events, args.chambers)
    configs = []
    for cid in args.chambers:
        common = dict(telemetry_interval_s=args.interval, poll_interval_s=args.poll_interval,
                      images_enabled=not args.no_images, image_interval_s=args.image_interval,
                      synced_clock=args.synced_clock, experiment_id=args.experiment_id,
                      door_events=doors.get(cid, []), ferment_time_scale=args.ferment_time_scale,
                      use_fermentation_model=not args.no_fermentation_model,
                      save_frames_dir=args.save_frames, manual_ph=manual.get(cid))
        if cid == "A":
            cfg = chamber_A.config(has_ph_probe=args.ph, **common)
        elif cid == "B":
            cfg = chamber_B.config(**common)
        else:
            preset = DEMO_RESTAURANTS.get(cid) if args.profile == "demo-restaurants" else None
            if preset:   # SIMULATED demo lab (fictional restaurant)
                common["door_events"] = common["door_events"] or list(preset["doors"])
                cfg = ChamberConfig(chamber_id=cid, seed=preset["seed"],
                                    plant=PlantParams(ambient_c=preset["ambient_c"]),
                                    room_rh_percent=preset["rh"],
                                    has_ph_probe=True, ph_method="probe", **common)
            else:
                cfg = ChamberConfig(chamber_id=cid, has_ph_probe=True, ph_method="probe", **common)
        if args.profile == "demo-restaurants" and cid in DEMO_RESTAURANTS:
            pass  # the profile's ambient wins over the global --ambient default
        elif args.ambient is not None:
            cfg.plant = PlantParams(**{**cfg.plant.__dict__, "ambient_c": args.ambient})
        if args.seed is not None:
            cfg.seed = args.seed + ord(cid[0])
        configs.append(cfg)
    return configs


async def run_serial(args: argparse.Namespace) -> int:
    """--transport serial: each chamber talks contracts/serial_v1.md on its own port."""
    from .device import VirtualChamber
    from .transport_serial import SerialDeviceTransport

    if args.replay:
        log.error("--replay is HTTP-only; replay serial line logs with `python -m fermentai.bridge --replay`")
        return 2
    ports: dict[str, str] = {}
    unnamed = []
    for item in args.port:
        ch, sep, path = item.partition("=")
        if sep and ch.upper() in args.chambers:
            ports[ch.upper()] = path
        else:
            unnamed.append(item)
    for cid in args.chambers:
        if cid not in ports and unnamed:
            ports[cid] = unnamed.pop(0)
    missing = [c for c in args.chambers if c not in ports]
    if missing:
        log.error("--transport serial needs a --port for chamber(s) %s", ", ".join(missing))
        return 2
    contract = ContractState(args.contract, fallback=False)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    devices, transports = [], []
    for cfg in make_configs(args):
        tr = SerialDeviceTransport(cfg.chamber_id, cfg.device_id, contract, port=ports[cfg.chamber_id])
        dev = VirtualChamber(cfg, tr)
        tr.attach(dev)
        await tr.start()
        devices.append(dev)
        transports.append(tr)
    log.info("SIMULATED devices %s over USB-serial %s (source=simulated)", args.chambers,
             ", ".join(f"{c}={p}" for c, p in ports.items()))
    try:
        await asyncio.gather(*(d.run(duration_s=args.duration, stop=stop) for d in devices))
    finally:
        for tr in transports:
            await tr.close()
    return 0


async def run_async(args: argparse.Namespace) -> int:
    if getattr(args, "transport", "http") == "serial":
        return await run_serial(args)
    from .device import VirtualChamber
    from .replay import replay

    contract = ContractState(args.contract, fallback=not args.no_contract_fallback)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    async with httpx.AsyncClient(base_url=args.base_url, timeout=5.0) as client:
        configs = make_configs(args)
        nets = {c.chamber_id: DeviceHttpClient(client, c.chamber_id, c.device_id, contract,
                                               token_candidates(c.chamber_id, args.token))
                for c in configs}
        if args.replay:
            log.info("REPLAY %s -> %s (source=replay, timestamps preserved)", args.replay, args.base_url)
            stats = await replay(args.replay, nets, speed=args.replay_speed,
                                 default_chamber=args.chambers[0])
            return 0 if stats["failed"] == 0 else 1
        devices = [VirtualChamber(c, nets[c.chamber_id]) for c in configs]
        log.info("SIMULATED devices %s -> %s (contract %s; source=simulated; thermal plant is a "
                 "controller-test model, not a validated simulation)", args.chambers, args.base_url,
                 contract.mode)
        await asyncio.gather(*(d.run(duration_s=args.duration, stop=stop) for d in devices))
        log.info("contract mode at exit: %s (switches: %s)", contract.mode, contract.switches or "none")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    apply_profile(args)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    if args.write_fixtures:
        from .virtual_camera import write_fixtures

        for p in write_fixtures():
            print(p)
        return 0
    try:
        from fermentai.api.config import load_dotenv  # same .env the backend reads

        load_dotenv()
    except ImportError:
        log.debug("fermentai not importable; .env not loaded")
    try:
        return asyncio.run(run_async(args))
    except KeyboardInterrupt:
        return 0
