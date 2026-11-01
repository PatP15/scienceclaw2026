"""VirtualChamber: one virtual ESP32 chamber controller (SIMULATED).

Mirrors the firmware loop in firmware_contract/README_FOR_ESP32_AGENT.md §4:
  every poll_interval_s : GET desired-control -> flat control/motor fields AND v1.1 `commands`
  every telemetry_interval_s : POST telemetry
  every image_interval_s : POST image (synthetic JPEG stamped SIMULATED)
  continuously : local thermal control + interlocks, motor timing (independent of the network)

Clock: an injectable monotonic clock (seconds). Tests drive ``await device.step()`` with a stubbed
clock; ``run()`` is the real-time loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import DEVICE_IDS, FIRMWARE_VERSION
from .limits import LIMITS, LocalLimits
from .network_client import DeviceHttpClient, Result
from .virtual_camera import render_jar_jpeg
from .virtual_motor import VirtualMotor
from .virtual_sensors import Bme680Like, StarterModel, read_ds18b20
from .virtual_thermal_plant import ControllerParams, PlantParams, ThermalChamber

log = logging.getLogger("simulated_devices.device")

MOTOR_ACTIONS = ("animate_motor", "stop_motor")
# Fields that are device metadata rather than readings/state (not given field provenance).
_META_FIELDS = {"schema_version", "chamber_id", "experiment_id", "source", "quality_flags",
                "device_id", "firmware_version", "boot_id", "sequence_number", "diagnostics",
                "field_provenance", "timestamp_quality", "last_command_id", "last_sequence_number",
                "fault_codes", "uptime_s", "device_uptime_ms", "timestamp_utc"}


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class ChamberConfig:
    chamber_id: str
    device_id: str = ""
    seed: int = 0
    plant: PlantParams = field(default_factory=PlantParams)
    controller: ControllerParams = field(default_factory=ControllerParams)
    initial_starter_height_mm: float = 25.0
    has_ph_probe: bool = False                 # only chamber A may have one (--ph)
    ph_method: str = "probe_sample"             # 'probe' for simulated always-on pH (B-F)
    manual_ph: float | None = None              # injected manual reading (sent once)
    telemetry_interval_s: float = 5.0
    poll_interval_s: float = 2.0                # 1-3 s per PLAN_MASTER §6
    images_enabled: bool = True
    image_interval_s: float = 300.0
    synced_clock: bool = False                  # False -> timestamp_utc null, "unsynced"
    experiment_id: str | None = None
    door_events: list[tuple[float, float]] = field(default_factory=list)  # (start_s, duration_s)
    ferment_time_scale: float = 30.0
    use_fermentation_model: bool = True
    save_frames_dir: Path | None = None
    limits: LocalLimits = LIMITS
    room_rh_percent: float = 45.0               # assumed room humidity (demo profiles vary it)

    def __post_init__(self) -> None:
        self.device_id = self.device_id or DEVICE_IDS.get(self.chamber_id,
                                                          f"fermentai-esp32-{self.chamber_id.lower()}")


class VirtualChamber:
    def __init__(self, config: ChamberConfig, net: DeviceHttpClient | None, *, clock=time.monotonic,
                 utcnow=lambda: datetime.now(UTC)):
        self.cfg = config
        self.chamber_id = config.chamber_id
        self.net = net
        self.clock = clock
        self.utcnow = utcnow
        self.limits = config.limits
        self.rng = random.Random(config.seed)
        self.boot_id = uuid.uuid4().hex[:16]
        self.boot_at = clock()
        self._last_tick = self.boot_at
        # hardware
        self.thermal = ThermalChamber(config.plant, config.controller, seed=config.seed, limits=self.limits)
        self.bme = Bme680Like(self.rng, room_rh_percent=config.room_rh_percent)
        self.starter = StarterModel(initial_height_mm=config.initial_starter_height_mm,
                                    time_scale=config.ferment_time_scale,
                                    use_fermentation_model=config.use_fermentation_model, rng=self.rng)
        self.motor = VirtualMotor(self.limits.motor_max_duration_s)
        self.door_override: bool | None = None
        self.pending_manual_ph = config.manual_ph
        # control state
        self.setpoint_c: float | None = None
        self.control_mode = "auto"
        self.experiment_id = config.experiment_id
        self.last_valid_command_at: float | None = None
        self.server_epoch: str | None = None
        self.last_seq: dict[str, int] = {}
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.last_command_id: str | None = None
        self.telemetry_seq = 0
        # I/O state
        self.ack_outbox: deque[dict] = deque()
        self.telemetry_outbox: deque[dict] = deque(maxlen=50)
        self._next_poll = self.boot_at
        self._next_telemetry = self.boot_at
        self._next_image = self.boot_at + 5.0
        self._image_requests: list[str] = []     # capture_image command ids
        self._backoff_s = 0.0
        self._net_retry_at = self.boot_at
        self.stats = {"polls": 0, "telemetry": 0, "acks": 0, "images": 0, "errors": 0,
                      "motor_runs": 0, "commands_seen": 0}
        self.ack_log: list[dict] = []
        self.terminal: set[str] = set()          # ids that already got a terminal ack
        self._wifi_rssi = -55.0
        log.info("[%s] boot device_id=%s boot_id=%s ph_probe=%s starter_model=%s", self.chamber_id,
                 config.device_id, self.boot_id, config.has_ph_probe, self.starter.model_name)

    # ================================================================== time helpers
    def uptime_s(self, now: float | None = None) -> float:
        return (self.clock() if now is None else now) - self.boot_at

    def uptime_ms(self, now: float | None = None) -> int:
        return int(round(self.uptime_s(now) * 1000))

    @property
    def door_open(self) -> bool:
        if self.door_override is not None:
            return self.door_override
        up = self.uptime_s()
        return any(s <= up < s + d for s, d in self.cfg.door_events)

    def offline(self, now: float) -> bool:
        return (self.last_valid_command_at is None
                or now - self.last_valid_command_at > self.limits.offline_hold_s)

    # ================================================================== local physics
    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        door = self.door_open
        if door and self.motor.running:   # README §5.6: stop a running motor when the door opens
            run = self.motor.stop()
            self._queue_ack(run.command_id, "failed", run.sequence_number,
                            detail="door opened: motor stopped", result={"ran_ms": int((now - run.started_at) * 1000)})
        if dt > 0:
            sp = None if self.control_mode == "idle" else self.setpoint_c
            self.thermal.step(now, dt, setpoint_c=sp, door_open=door, offline=self.offline(now))
            p = self.thermal.plant
            self.starter.step(dt, p.starter_c)
            self.bme.step(dt, chamber_c=p.chamber_c, ambient_c=p.ambient_c, door_open=door,
                          activity=self.starter.activity)
            self._wifi_rssi = max(-85.0, min(-40.0, self._wifi_rssi + self.rng.gauss(0, 0.3)))
        done = self.motor.update(now)
        if done:
            self._queue_ack(done.command_id, "completed", done.sequence_number,
                            detail=f"ran {done.duration_s:.1f} s ({done.pattern})",
                            result={"ran_ms": int(done.duration_s * 1000)})
            log.info("[%s] motor done %s", self.chamber_id, done.command_id)

    # ================================================================== commands
    def _mark_seen(self, key: str) -> bool:
        """True if new. Bounded memory of executed command ids (never execute twice)."""
        if key in self.seen:
            return False
        self.seen[key] = None
        while len(self.seen) > 2048:
            self.seen.popitem(last=False)
        return True

    def _stale_seq(self, kind: str, seq: int | None) -> bool:
        return seq is not None and seq < self.last_seq.get(kind, -1)

    def _note_seq(self, kind: str, seq: int | None) -> None:
        if seq is not None:
            self.last_seq[kind] = max(seq, self.last_seq.get(kind, seq))

    def _queue_ack(self, command_id: str, status: str, seq: int | None, *, detail: str | None = None,
                   applied: float | None = None, result: dict | None = None) -> None:
        ack = {
            "schema_version": "1.0", "command_id": command_id, "chamber_id": self.chamber_id,
            "status": status, "sequence_number": seq,
            "timestamp_utc": utc_iso(self.utcnow()) if self.cfg.synced_clock else None,
            "applied_setpoint_c": applied, "message": detail, "detail": detail,
            "device_id": self.cfg.device_id, "device_uptime_ms": self.uptime_ms(), "result": result,
        }
        if status in ("completed", "failed", "cancelled", "rejected", "expired"):
            self.terminal.add(command_id)
        self.ack_outbox.append(ack)
        self.ack_log.append(ack)

    def _apply_setpoint(self, requested: float) -> tuple[float, str | None]:
        applied = self.limits.clamp_setpoint(requested)
        self.setpoint_c = applied
        note = None if applied == requested else f"clamped locally to {applied:g} C"
        return applied, note

    def handle_desired_control(self, resp: dict, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if resp.get("schema_version") not in ("1.0", None):
            log.error("[%s] unsupported schema_version %r", self.chamber_id, resp.get("schema_version"))
            return
        epoch = resp.get("server_epoch")
        if epoch != self.server_epoch:
            if self.server_epoch is not None:
                log.warning("[%s] server_epoch changed -> reset sequence tracking", self.chamber_id)
            self.server_epoch = epoch
            self.last_seq.clear()
        if resp.get("experiment_id"):
            self.experiment_id = resp["experiment_id"]
        if resp.get("control_mode") in ("auto", "hold", "idle"):
            self.control_mode = resp["control_mode"]
        # v1.1 command list first (ascending sequence), then the v1.0 flat fields. A command id
        # that appears in both is executed exactly once (shared seen-set).
        cmds = resp.get("commands") or []
        for entry in sorted((c for c in cmds if isinstance(c, dict)),
                            key=lambda c: (c.get("sequence_number") is None, c.get("sequence_number") or 0)):
            self._handle_command(entry, resp, now)
        self._handle_flat_control(resp, now)
        self._handle_flat_motor(resp.get("motor") or {}, now)

    # ---------------------------------------------------------------- v1.0 flat
    def _handle_flat_control(self, resp: dict, now: float) -> None:
        cid, seq = resp.get("command_id"), resp.get("sequence_number")
        ttl = resp.get("ttl_s")
        if not cid or resp.get("temperature_setpoint_c") is None:
            return
        if ttl is not None and ttl > 0:
            self.last_valid_command_at = now   # re-served valid command keeps offline hold fresh
        if cid in self.seen or self._stale_seq("control", seq):
            return
        self._mark_seen(cid)
        self.stats["commands_seen"] += 1
        self._note_seq("control", seq)
        if ttl is None or ttl <= 0:
            self._queue_ack(cid, "expired", seq, detail="received after expiry; not applied")
            return
        requested = float(resp["temperature_setpoint_c"])
        applied, note = self._apply_setpoint(requested)
        self.last_command_id = cid
        log.info("[%s] setpoint %.2f C (cmd %s seq %s)", self.chamber_id, applied, cid[:8], seq)
        self._queue_ack(cid, "accepted", seq, applied=applied, detail=note,
                        result={"applied_setpoint_c": applied})

    def _handle_flat_motor(self, m: dict, now: float) -> None:
        action, mid, seq = m.get("action", "none"), m.get("command_id"), m.get("sequence_number")
        if action == "stop":
            if not mid:
                return
            cur = self.motor.current
            if cur is not None and cur.command_id == mid:
                self.motor.stop()
                log.info("[%s] motor STOP (%s)", self.chamber_id, mid[:8])
            # (v1.1 servers also send a stop_motor list command for the same cancel: ack once)
            if self._mark_seen(mid + ":stop") and mid not in self.terminal:
                self._mark_seen(mid)
                self._queue_ack(mid, "cancelled", seq, detail="stopped on server stop directive")
            return
        if action != "run" or not mid:
            return
        if mid in self.seen or self._stale_seq("motor", seq):
            return
        self._mark_seen(mid)
        self.stats["commands_seen"] += 1
        self._note_seq("motor", seq)
        if (m.get("ttl_s") or 0) <= 0:
            self._queue_ack(mid, "expired", seq, detail="motor command expired; not run")
            return
        self._start_motor(mid, float(m.get("duration_s") or 0.0), m.get("pattern") or "wiggle", seq,
                          now, "flat")

    def _start_motor(self, mid: str, duration_s: float, pattern: str, seq: int | None, now: float,
                     source: str) -> None:
        if self.door_open:
            self._queue_ack(mid, "rejected", seq, detail="door open")
            return
        if duration_s <= 0:
            self._queue_ack(mid, "rejected", seq, detail="non-positive duration")
            return
        prev = self.motor.start(mid, duration_s, pattern, now, sequence_number=seq, source=source)
        if prev is not None:
            self._queue_ack(prev.command_id, "cancelled", prev.sequence_number,
                            detail=f"pre-empted by {mid}")
        run = self.motor.current
        self.stats["motor_runs"] += 1
        log.info("[%s] motor RUN %.1f s %s (%s)", self.chamber_id, run.duration_s, pattern, mid[:8])
        clamp = "" if run.duration_s == duration_s else f" (clamped from {duration_s:g} s)"
        self._queue_ack(mid, "accepted", seq, detail=f"running {run.duration_s:.1f} s{clamp}")

    # ---------------------------------------------------------------- v1.1 list
    def _remaining_ttl(self, entry: dict, resp: dict) -> float | None:
        if entry.get("ttl_s") is not None:
            return float(entry["ttl_s"])
        exp = parse_utc(entry.get("expires_at_utc"))
        if exp is None:
            return None
        ref = parse_utc(resp.get("server_time_utc"))  # relative to server time: no device RTC needed
        if ref is None:
            ref = self.utcnow() if self.cfg.synced_clock else None
        return None if ref is None else (exp - ref).total_seconds()

    def _handle_command(self, entry: dict, resp: dict, now: float) -> None:
        cid, action = entry.get("command_id"), entry.get("action")
        seq = entry.get("sequence_number")
        if not cid or cid in self.seen:
            return
        if entry.get("chamber_id") not in (None, self.chamber_id):
            self._mark_seen(cid)
            self._queue_ack(cid, "rejected", seq, detail=f"command for chamber {entry.get('chamber_id')}")
            return
        if self._stale_seq("commands", seq):
            self._mark_seen(cid)
            log.warning("[%s] ignoring stale command %s seq %s", self.chamber_id, cid, seq)
            return
        self._mark_seen(cid)
        self.stats["commands_seen"] += 1
        self._note_seq("commands", seq)
        ttl = self._remaining_ttl(entry, resp)
        if ttl is not None and ttl <= 0:
            self._queue_ack(cid, "expired", seq, detail="received after expiry; not executed")
            return
        params = entry.get("parameters") or {}
        if action == "set_setpoint":
            requested = params.get("setpoint_c", params.get("temperature_setpoint_c"))
            if requested is None:
                self._queue_ack(cid, "rejected", seq, detail="missing parameters.setpoint_c")
                return
            mode = params.get("control_mode")
            if mode in ("auto", "hold", "idle"):
                self.control_mode = mode
            applied, note = self._apply_setpoint(float(requested))
            self.last_command_id = cid
            self.last_valid_command_at = now
            if entry.get("experiment_id"):
                self.experiment_id = entry["experiment_id"]
            log.info("[%s] setpoint %.2f C (set_setpoint %s seq %s)", self.chamber_id, applied,
                     cid[:8], seq)
            # A setpoint is a STANDING command: ack "accepted" only (as in v1.0). The backend
            # re-serves a control command only while it is issued/accepted; acking "completed"
            # would make it mint a new control command_id on every poll.
            self._queue_ack(cid, "accepted", seq, applied=applied, detail=note,
                            result={"applied_setpoint_c": applied})
        elif action == "animate_motor":
            if params.get("duration_ms") is not None:
                dur = float(params["duration_ms"]) / 1000.0
            else:
                dur = float(params.get("duration_s") or 0.0)
            self._start_motor(cid, dur, params.get("pattern") or "wiggle", seq, now, "list")
        elif action == "stop_motor":
            self._queue_ack(cid, "accepted", seq)
            cur = self.motor.current
            target = params.get("target_command_id")
            if cur is not None and (target is None or target == cur.command_id):
                self.motor.stop()
                self._queue_ack(cur.command_id, "cancelled", cur.sequence_number,
                                detail=f"stopped by {cid}",
                                result={"ran_ms": int((now - cur.started_at) * 1000)})
                self._queue_ack(cid, "completed", seq, detail=f"stopped {cur.command_id}")
            else:
                if target:
                    self._mark_seen(target)   # never start it later
                self._queue_ack(cid, "completed", seq, detail="motor was not running")
        elif action == "capture_image":
            self._queue_ack(cid, "accepted", seq)
            self._image_requests.append(cid)
        elif action == "noop":
            self._queue_ack(cid, "accepted", seq)
            self._queue_ack(cid, "completed", seq, detail="noop")
        else:
            self._queue_ack(cid, "rejected", seq, detail=f"unknown action {action!r}")

    # ================================================================== telemetry
    def build_telemetry(self, now: float | None = None) -> dict:
        """Full v1.1 superset payload (the network client trims it for v1.0 servers)."""
        now = self.clock() if now is None else now
        p, c = self.thermal.plant, self.thermal.controller
        g = self.rng.gauss
        door = self.door_open
        chamber_c = round(p.chamber_c + g(0, 0.05), 2)
        bme = self.bme.read(p.chamber_c)
        self.telemetry_seq += 1
        flags = ["simulated_device"]
        if door:
            flags.append("door_open_during_reading")
        if self.uptime_s(now) < 120:
            flags.append("sensor_warming_up")
        ph, ph_method = None, None
        provenance_override = {}
        if self.pending_manual_ph is not None:
            ph, ph_method = round(float(self.pending_manual_ph), 2), "manual"
            provenance_override = {"ph": "manual", "ph_method": "manual"}
            self.pending_manual_ph = None
        elif self.cfg.has_ph_probe:
            ph, ph_method = self.starter.ph_reading(), self.cfg.ph_method
        synced = self.cfg.synced_clock
        sensor_health = {"bme680": "ok", "ds18b20_starter": "ok", "internal_heatsink": "ok",
                         "external_heatsink": "ok", "door_switch": "ok", "camera": "ok"}
        if self.cfg.has_ph_probe:
            sensor_health["ph"] = "ok"
        out = {
            "schema_version": "1.0",
            "device_id": self.cfg.device_id,
            "chamber_id": self.chamber_id,
            "experiment_id": self.experiment_id,
            "boot_id": self.boot_id,
            "sequence_number": self.telemetry_seq,
            "timestamp_utc": utc_iso(self.utcnow()) if synced else None,
            "timestamp_quality": "synced" if synced else "unsynced",
            "device_uptime_ms": self.uptime_ms(now),
            "chamber_temperature_c": chamber_c,
            "starter_temperature_c": read_ds18b20(p.starter_c, self.rng),
            "internal_heatsink_temperature_c": round(p.internal_heatsink_c + g(0, 0.1), 2),
            "external_heatsink_temperature_c": round(p.external_heatsink_c + g(0, 0.1), 2),
            "relative_humidity_percent": bme["relative_humidity_percent"],
            "pressure_pa": bme["pressure_pa"],
            "gas_resistance_ohms": bme["gas_resistance_ohms"],
            "ph": ph,
            "ph_method": ph_method,
            "starter_height_mm": self.starter.height_mm(),
            "door_open": door,
            "peltier_mode": c.mode,
            "peltier_output_fraction": round(c.output, 3),
            "temperature_setpoint_c": self.setpoint_c,
            "motor_running": self.motor.running,
            "source": "simulated",
            "quality_flags": flags,
            # v1.0 diagnostics
            "firmware_version": FIRMWARE_VERSION,
            "uptime_s": round(self.uptime_s(now), 1),
            "wifi_rssi_dbm": round(self._wifi_rssi, 1),
            "heatsink_temperature_c": round(p.external_heatsink_c, 2),
            "fan_running": c.fan_running,
            "peltier_duty_percent": round(c.output * 100.0, 1),
            "last_command_id": self.last_command_id,
            "last_sequence_number": self.last_seq.get("control"),
            "fault_codes": list(c.fault_codes),
            # v1.1 diagnostics
            "diagnostics": {
                "wifi_rssi_dbm": round(self._wifi_rssi, 1),
                "free_heap_bytes": int(180_000 + self.rng.randint(-4000, 4000)),
                "firmware_version": FIRMWARE_VERSION,
                "last_command_id": self.last_command_id,
                "sensor_health": sensor_health,
            },
        }
        out["field_provenance"] = {k: provenance_override.get(k, "simulated") for k, v in out.items()
                                   if k not in _META_FIELDS and v is not None}
        return out

    # ================================================================== I/O
    async def _flush_acks(self) -> None:
        while self.ack_outbox:
            ack = self.ack_outbox[0]
            res = await self.net.post_ack(ack)
            if res.ok:
                self.stats["acks"] += 1
            else:
                self.stats["errors"] += 1
                if res.retryable:
                    self._network_failed()
                    return                     # keep order; retry later
                log.warning("[%s] dropping ack %s/%s after %s", self.chamber_id,
                            ack["command_id"][:8], ack["status"], res.status)
            self.ack_outbox.popleft()

    def _network_failed(self) -> None:
        self._backoff_s = min(30.0, max(1.0, self._backoff_s * 2))
        self._net_retry_at = self.clock() + self._backoff_s

    def _network_ok(self) -> None:
        self._backoff_s = 0.0

    async def _poll(self, now: float) -> None:
        res: Result = await self.net.get_desired_control()
        self.stats["polls"] += 1
        if res.ok and isinstance(res.body, dict):
            self._network_ok()
            self.handle_desired_control(res.body, now)
        else:
            self.stats["errors"] += 1
            if res.retryable:
                self._network_failed()

    async def _send_telemetry(self, now: float) -> None:
        self.telemetry_outbox.append(self.build_telemetry(now))
        while self.telemetry_outbox:
            res = await self.net.post_telemetry(self.telemetry_outbox[0])
            if res.ok:
                self.stats["telemetry"] += 1
                self._network_ok()
            else:
                self.stats["errors"] += 1
                if res.retryable:
                    self._network_failed()
                    return
            self.telemetry_outbox.popleft()

    def render_frame(self) -> bytes:
        return render_jar_jpeg(self.chamber_id, self.starter.expansion * self.starter.h0,
                               caption=f"uptime {self.uptime_s():.0f} s  boot {self.boot_id[:6]}",
                               door_open=self.door_open, motor_running=self.motor.running)

    async def _upload_image(self, request_id: str | None = None) -> bool:
        jpeg = self.render_frame()
        if self.cfg.save_frames_dir:
            d = Path(self.cfg.save_frames_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{self.chamber_id}_{self.boot_id[:6]}_{self.uptime_ms():09d}.jpg").write_bytes(jpeg)
        fields = {"chamber_id": self.chamber_id, "source": "simulated", "device_id": self.cfg.device_id,
                  "boot_id": self.boot_id}
        if self.cfg.synced_clock:
            fields["timestamp_utc"] = utc_iso(self.utcnow())
        if self.experiment_id:
            fields["experiment_id"] = self.experiment_id
        res = await self.net.post_image(jpeg, fields)
        if res.ok:
            self.stats["images"] += 1
        else:
            self.stats["errors"] += 1
        if request_id:
            self._queue_ack(request_id, "completed" if res.ok else "failed", None,
                            detail="image uploaded" if res.ok else f"upload failed ({res.status})",
                            result={"image_id": res.body.get("image_id")}
                            if res.ok and isinstance(res.body, dict) else None)
        return res.ok

    async def step(self) -> None:
        """One iteration: local physics, then whatever network work is due."""
        now = self.clock()
        self.tick(now)
        if self.net is None or now < self._net_retry_at:
            return
        if now >= self._next_poll:
            self._next_poll = now + min(max(self.cfg.poll_interval_s, 1.0), 3.0)
            await self._poll(now)
        await self._flush_acks()
        if now >= self._next_telemetry:
            self._next_telemetry = now + self.cfg.telemetry_interval_s
            await self._send_telemetry(now)
        while self._image_requests:
            await self._upload_image(self._image_requests.pop(0))
        if self.cfg.images_enabled and now >= self._next_image:
            self._next_image = now + self.cfg.image_interval_s
            await self._upload_image()
        await self._flush_acks()

    async def run(self, *, duration_s: float = 0.0, tick_s: float = 0.2,
                  stop: asyncio.Event | None = None) -> dict:
        start = self.clock()
        while not (duration_s and self.clock() - start >= duration_s) and not (stop and stop.is_set()):
            try:
                await self.step()
            except Exception:  # keep the virtual device alive like a watchdog would
                log.exception("[%s] step failed", self.chamber_id)
                self.stats["errors"] += 1
            await asyncio.sleep(tick_s)
        log.info("[%s] stats %s", self.chamber_id, self.stats)
        return self.stats
