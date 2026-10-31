#!/usr/bin/env python3
"""SIMULATED ESP32 chamber controller(s) - a software stand-in for firmware contract v1.0.

Everything this posts is labelled source="simulated" with quality flag "simulated_device".
It is NOT a physical measurement.

It behaves like the real firmware is required to:
  * polls GET /api/v1/chambers/{id}/desired-control,
  * applies a control command only if ttl_s > 0 at receipt and its sequence number is new
    (per command type; reset when server_epoch changes), clamps to its OWN local limits,
    and acks it via POST /api/v1/control-ack,
  * runs the chef-hat motor for at most min(duration_s, 10 s), acks accepted -> completed,
    stops immediately on action "stop" (acks "cancelled"), rejects expired motor commands,
  * posts telemetry every --telemetry-interval seconds,
  * falls back to Peltier idle when no valid command has been received for offline_hold_s.

Usage:
  uv run python scripts/simulate_device.py                       # A and B, forever
  uv run python scripts/simulate_device.py --chambers A --duration 60
  FERMENTAI_API_TOKEN=... uv run python scripts/simulate_device.py --base-url http://10.0.0.5:8000
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import os
import random
import time
from datetime import UTC, datetime

import httpx

log = logging.getLogger("simulate_device")

LOCAL_MIN_C, LOCAL_MAX_C = 4.0, 35.0       # firmware_local_limits (hardware.yaml)
MOTOR_MAX_S = 10.0
OFFLINE_HOLD_S = 1800.0
DEAD_TIME_S = 60.0


def utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SimulatedDevice:
    def __init__(self, chamber_id: str, client: httpx.Client, token: str, *,
                 telemetry_interval_s: float = 10.0, poll_interval_s: float = 5.0,
                 ambient_c: float = 21.0, experiment_id: str | None = None,
                 seed: int | None = None, post_images: bool = False,
                 image_interval_s: float = 300.0, clock=time.monotonic):
        self.chamber_id = chamber_id
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        self.telemetry_interval_s = telemetry_interval_s
        self.poll_interval_s = poll_interval_s
        self.experiment_id = experiment_id
        self.post_images = post_images
        self.image_interval_s = image_interval_s
        self.clock = clock
        self.rng = random.Random(seed if seed is not None else hash(chamber_id) & 0xFFFF)
        # physical state (simulated)
        self.ambient_c = ambient_c
        self.chamber_c = ambient_c
        self.starter_c = ambient_c - 0.3
        self.height_mm = 20.0
        self.gas_ohms = 120_000.0
        self.door_open = False
        self.peltier_mode = "idle"
        self._mode_change_at = -1e9
        # control state
        self.setpoint_c: float | None = None
        self.last_valid_command_at: float | None = None
        self.server_epoch: str | None = None
        self.last_seq: dict[str, int] = {}
        self.seen_ids: set[str] = set()
        self.last_command_id: str | None = None
        # motor state
        self.motor_command_id: str | None = None
        self.motor_until: float | None = None
        # schedule
        t0 = self.clock()
        self._last_tick = t0
        self._next_poll = t0
        self._next_telemetry = t0
        self._next_image = t0
        self.stats = {"polls": 0, "telemetry": 0, "acks": 0, "errors": 0, "motor_runs": 0}

    # ------------------------------------------------------------------ HTTP
    def _post(self, path: str, **kw) -> httpx.Response | None:
        try:
            r = self.client.post(path, headers=self.headers, **kw)
            if r.status_code >= 400:
                log.warning("[%s] POST %s -> %s %s", self.chamber_id, path, r.status_code, r.text[:200])
                self.stats["errors"] += 1
            return r
        except httpx.HTTPError as e:
            log.warning("[%s] POST %s failed: %s", self.chamber_id, path, e)
            self.stats["errors"] += 1
            return None

    def ack(self, command_id: str, status: str, seq: int | None, *, applied: float | None = None,
            message: str | None = None) -> None:
        body = {"schema_version": "1.0", "command_id": command_id, "chamber_id": self.chamber_id,
                "status": status, "sequence_number": seq, "timestamp_utc": utc_iso(),
                "applied_setpoint_c": applied, "message": message}
        if self._post("/api/v1/control-ack", json=body) is not None:
            self.stats["acks"] += 1

    # ------------------------------------------------------------------ command handling
    def _is_new(self, kind: str, command_id: str, seq: int | None) -> bool:
        if command_id in self.seen_ids:
            return False
        if seq is not None and seq <= self.last_seq.get(kind, 0):
            log.warning("[%s] ignoring %s command %s with stale seq %s", self.chamber_id, kind,
                        command_id, seq)
            return False
        return True

    def handle_desired_control(self, cmd: dict, now: float) -> None:
        if cmd.get("schema_version") != "1.0":
            log.error("[%s] unsupported schema_version %r", self.chamber_id, cmd.get("schema_version"))
            return
        if cmd.get("server_epoch") != self.server_epoch:
            self.server_epoch = cmd.get("server_epoch")
            self.last_seq.clear()  # server DB was reset: restart sequence tracking
        cid, seq = cmd["command_id"], cmd.get("sequence_number")
        if self._is_new("control", cid, seq):
            self.seen_ids.add(cid)
            if seq is not None:
                self.last_seq["control"] = seq
            if cmd.get("ttl_s", 0) <= 0:
                self.ack(cid, "expired", seq, message="received after expiry; not applied")
            else:
                sp = float(cmd["temperature_setpoint_c"])
                applied = min(max(sp, LOCAL_MIN_C), LOCAL_MAX_C)  # LOCAL limits, independent of server
                self.setpoint_c = applied
                self.last_command_id = cid
                self.ack(cid, "accepted", seq, applied=applied,
                         message=None if applied == sp else f"clamped locally to {applied}")
        if cmd.get("ttl_s", 0) > 0:
            self.last_valid_command_at = now
        self.handle_motor(cmd.get("motor") or {}, now)

    def handle_motor(self, m: dict, now: float) -> None:
        action, mid, seq = m.get("action", "none"), m.get("command_id"), m.get("sequence_number")
        if action == "stop":
            if self.motor_until is not None:
                stopped = self.motor_command_id
                self.motor_until = None
                self.motor_command_id = None
                log.info("[%s] motor STOP (cancelled %s)", self.chamber_id, stopped)
            if mid and mid + ":stop-acked" not in self.seen_ids:
                self.seen_ids.update({mid, mid + ":stop-acked"})
                self.ack(mid, "cancelled", seq, message="stopped on server stop directive")
            return
        if action != "run" or not mid or not self._is_new("motor", mid, seq):
            return
        self.seen_ids.add(mid)
        if seq is not None:
            self.last_seq["motor"] = seq
        if (m.get("ttl_s") or 0) <= 0:
            self.ack(mid, "rejected", seq, message="motor command expired")
            return
        if self.door_open:
            self.ack(mid, "rejected", seq, message="door open")
            return
        duration = min(float(m.get("duration_s") or 0.0), MOTOR_MAX_S)
        self.motor_command_id = mid
        self.motor_until = now + duration
        self.stats["motor_runs"] += 1
        log.info("[%s] motor RUN %.1f s (%s)", self.chamber_id, duration, m.get("pattern"))
        self.ack(mid, "accepted", seq, message=f"running {duration:.1f} s")

    def _finish_motor(self, now: float) -> None:
        if self.motor_until is not None and now >= self.motor_until:
            mid = self.motor_command_id
            self.motor_until = None
            self.motor_command_id = None
            if mid:
                self.ack(mid, "completed", self.last_seq.get("motor"), message="motor run finished")

    # ------------------------------------------------------------------ simulated physics
    def _physics(self, dt: float, now: float) -> None:
        sp = self.setpoint_c
        offline = (self.last_valid_command_at is None
                   or now - self.last_valid_command_at > OFFLINE_HOLD_S)
        want = "idle"
        if sp is not None and not offline and not self.door_open:
            if self.chamber_c < sp - 0.3:
                want = "heat"
            elif self.chamber_c > sp + 0.3:
                want = "cool"
            elif self.peltier_mode in ("heat", "cool") and abs(self.chamber_c - sp) > 0.1:
                want = self.peltier_mode
        # safe heat<->cool switching: pass through "switching" (off) for DEAD_TIME_S
        if want != self.peltier_mode:
            if {want, self.peltier_mode} == {"heat", "cool"}:
                want = "switching"
            if self.peltier_mode == "switching" and now - self._mode_change_at < DEAD_TIME_S:
                want = "switching"
            if want != self.peltier_mode:
                self.peltier_mode, self._mode_change_at = want, now
        drive = {"heat": 0.012, "cool": -0.010}.get(self.peltier_mode, 0.0)  # degC/s
        self.chamber_c += dt * (drive + (self.ambient_c - self.chamber_c) / 3600.0)
        self.starter_c += dt * (self.chamber_c - self.starter_c) / 900.0
        rate = 0.0006 * math.exp(0.07 * (self.starter_c - 24.0))  # relative growth per second-ish
        self.height_mm += dt * rate * self.height_mm * max(0.0, 1.0 - self.height_mm / 48.0) / 10.0
        self.gas_ohms = max(20_000.0, self.gas_ohms - dt * 0.4)

    def telemetry(self, now: float) -> dict:
        n = self.rng.gauss
        return {
            "schema_version": "1.0",
            "chamber_id": self.chamber_id,
            "experiment_id": self.experiment_id,
            "timestamp_utc": utc_iso(),
            "chamber_temperature_c": round(self.chamber_c + n(0, 0.05), 2),
            "starter_temperature_c": round(self.starter_c + n(0, 0.05), 2),
            "relative_humidity_percent": round(70.0 + n(0, 1.0), 1),
            "gas_resistance_ohms": round(self.gas_ohms * (1 + n(0, 0.01)), 0),
            "ph": None,
            "ph_method": None,
            "starter_height_mm": round(self.height_mm + n(0, 0.2), 1),
            "door_open": self.door_open,
            "peltier_mode": self.peltier_mode,
            "temperature_setpoint_c": self.setpoint_c,
            "motor_running": self.motor_until is not None,
            "source": "simulated",
            "quality_flags": ["simulated_device"],
            "device_id": f"simulated-esp32-{self.chamber_id.lower()}",
            "firmware_version": "sim-1.0",
            "last_command_id": self.last_command_id,
            "last_sequence_number": self.last_seq.get("control"),
        }

    def _jpeg(self) -> bytes | None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        img = Image.new("RGB", (320, 240), (235, 230, 220))
        d = ImageDraw.Draw(img)
        h = int(self.height_mm * 4)
        d.rectangle([110, 230 - h, 210, 230], fill=(225, 200, 150), outline=(120, 90, 60))
        d.text((8, 8), f"SIMULATED chamber {self.chamber_id}  {self.height_mm:.1f} mm", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    # ------------------------------------------------------------------ main step
    def step(self) -> None:
        now = self.clock()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        self._physics(dt, now)
        self._finish_motor(now)
        if now >= self._next_poll:
            self._next_poll = now + self.poll_interval_s
            try:
                r = self.client.get(f"/api/v1/chambers/{self.chamber_id}/desired-control",
                                    headers=self.headers)
                self.stats["polls"] += 1
                if r.status_code == 200:
                    cmd = r.json()
                    self.poll_interval_s = float(cmd.get("poll_interval_s") or self.poll_interval_s)
                    self.handle_desired_control(cmd, now)
                else:
                    log.warning("[%s] desired-control -> %s %s", self.chamber_id, r.status_code,
                                r.text[:200])
                    self.stats["errors"] += 1
            except httpx.HTTPError as e:
                log.warning("[%s] poll failed: %s", self.chamber_id, e)
                self.stats["errors"] += 1
        if now >= self._next_telemetry:
            self._next_telemetry = now + self.telemetry_interval_s
            if self._post("/api/v1/telemetry", json=self.telemetry(now)) is not None:
                self.stats["telemetry"] += 1
        if self.post_images and now >= self._next_image:
            self._next_image = now + self.image_interval_s
            jpg = self._jpeg()
            if jpg:
                self._post("/api/v1/images", files={"file": ("sim.jpg", jpg, "image/jpeg")},
                           data={"chamber_id": self.chamber_id, "timestamp_utc": utc_iso(),
                                 "source": "simulated"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get(
        "FERMENTAI_BASE_URL", f"http://127.0.0.1:{os.environ.get('FERMENTAI_PORT', '8000')}"))
    ap.add_argument("--token", default=os.environ.get("FERMENTAI_API_TOKEN", "change-me-local-device-token"))
    ap.add_argument("--chambers", nargs="+", default=["A", "B"])
    ap.add_argument("--telemetry-interval", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = forever)")
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--images", action="store_true", help="also upload synthetic JPEG snapshots")
    ap.add_argument("--image-interval", type=float, default=300.0)
    ap.add_argument("--ambient", type=float, default=21.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    log.info("SIMULATED devices %s -> %s (labelled source=simulated)", args.chambers, args.base_url)
    with httpx.Client(base_url=args.base_url, timeout=5.0) as client:
        devices = [SimulatedDevice(c, client, args.token, telemetry_interval_s=args.telemetry_interval,
                                   experiment_id=args.experiment_id, post_images=args.images,
                                   image_interval_s=args.image_interval, ambient_c=args.ambient)
                   for c in args.chambers]
        start = time.monotonic()
        try:
            while not args.duration or time.monotonic() - start < args.duration:
                for d in devices:
                    d.step()
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        for d in devices:
            log.info("[%s] stats %s", d.chamber_id, d.stats)


if __name__ == "__main__":
    main()
