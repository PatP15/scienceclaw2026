"""Chamber control service: setpoints (the ONLY temperature path), commands, acks, telemetry.

SAFETY MODEL
------------
* `ControlService.set_desired_setpoint()` is the single function through which anything
  (operator, scientist agents, LLM output, judge feedback -> recommended schedule) can change
  a chamber's temperature setpoint. It
    - rejects non-finite / non-numeric input,
    - clamps to [temperature.min_setpoint_c, temperature.max_setpoint_c] (configs/hardware.yaml),
    - limits each change to temperature.max_step_c_per_update,
    - rate-limits applied changes to one per temperature.min_update_interval_s,
  and logs every request (applied or not) in `setpoint_changes`.
* `desired_control()` clamps the stored setpoint again before emitting a command (defence in
  depth in case hardware.yaml limits were tightened after a value was stored).
* Motor commands are a separate command type with their own ids, carrying only
  duration/pattern; they cannot express a setpoint or Peltier mode. A motor is reported as
  having run ONLY after the device acks `completed`.
* The ESP32 enforces its own compiled-in limits independently (firmware_contract/).

COMMAND MODEL (contract v1.1)
-----------------------------
commands.command_type -> wire `action`:
    control    -> set_setpoint     (also the v1.0 flat fields of desired-control)
    motor      -> animate_motor    (also the v1.0 `motor` directive with action "run")
    motor_stop -> stop_motor       (v1.0 `motor` directive with action "stop")
    capture    -> capture_image
One sequence counter per chamber across all types, so numbers are monotonic both per type
(v1.0 rule) and globally (v1.1 list, processed in ascending order).

DEVICE ISOLATION
----------------
A device id listed in hardware.yaml may only write its own chamber; an unlisted id is bound to
the first chamber it writes (devices table) and may never write another one. Device tokens are
additionally bound to their chamber (auth.py).
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .auth import Principal
from .config import HardwareConfig
from .db import Database, dumps, iso, loads_cols, parse_iso, utcnow
from .schemas import SCHEMA_VERSION, ControlAckIn, TelemetryIn

TELEMETRY_COLUMNS = (
    "chamber_temperature_c",
    "starter_temperature_c",
    "relative_humidity_percent",
    "gas_resistance_ohms",
    "ph",
    "ph_method",
    "starter_height_mm",
    "door_open",
    "peltier_mode",
    "temperature_setpoint_c",
    "motor_running",
)

# Fields broken out into sensor_readings: field -> (unit, candidate sensor names for sensor_health)
READING_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "chamber_temperature_c": ("degC", ("bme680", "sht31", "chamber_temperature")),
    "starter_temperature_c": ("degC", ("ds18b20_starter", "ds18b20", "starter_probe")),
    "internal_heatsink_temperature_c": ("degC", ("ds18b20_heatsink_internal", "internal_heatsink")),
    "external_heatsink_temperature_c": ("degC", ("ds18b20_heatsink_external", "external_heatsink")),
    "heatsink_temperature_c": ("degC", ("heatsink",)),
    "relative_humidity_percent": ("%RH", ("bme680", "sht31")),
    "pressure_pa": ("Pa", ("bme680",)),
    "gas_resistance_ohms": ("ohm", ("bme680",)),
    "ph": ("pH", ("ph", "ph_probe")),
    "starter_height_mm": ("mm", ("camera", "height")),
    "door_open": ("bool", ("door", "reed_switch")),
    "peltier_output_fraction": ("fraction", ("peltier",)),
    "temperature_setpoint_c": ("degC", ()),
    "motor_running": ("bool", ("motor",)),
}
SOURCE_TO_PROVENANCE = {"measured": "measured", "real": "measured", "simulated": "simulated",
                        "replay": "replay"}
PELTIER_CANONICAL = {"heating": "heat", "cooling": "cool"}

ACTION_OF = {"control": "set_setpoint", "motor": "animate_motor", "motor_stop": "stop_motor",
             "capture": "capture_image"}
TERMINAL_MOTOR = {"completed", "failed", "rejected", "cancelled", "expired", "expired_unacked"}


class UnknownChamber(KeyError):
    pass


class CommandError(ValueError):
    def __init__(self, msg: str, status_code: int = 400):
        super().__init__(msg)
        self.status_code = status_code


def agent_key(name: Any) -> str | None:
    """Normalise agent identifiers: 'scientist_A', 'Scientist A', 'agent-a', 'A' -> 'a'."""
    if name is None:
        return None
    s = re.sub(r"[\s_\-]+", "", str(name).lower())
    for prefix in ("scientist", "agent"):
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
    return s or None


class ControlService:
    def __init__(self, db: Database, hw: HardwareConfig, clock: Callable[[], datetime] = utcnow):
        self.db = db
        self.hw = hw
        self.clock = clock
        from .door import DoorRule
        self.door_rule = DoorRule(self)
        self._ensure_chambers()

    # ------------------------------------------------------------------ chambers / devices
    @property
    def chamber_ids(self) -> list[str]:
        return list(self.hw.chambers)

    @property
    def lab_ids(self) -> list[str]:
        """Chambers that are our own labs (kind != "demo"); the sous-chef agents only see these."""
        return [c for c, cfg in self.hw.chambers.items() if cfg.kind != "demo"]

    def require_chamber(self, chamber_id: str) -> str:
        if chamber_id not in self.hw.chambers:
            raise UnknownChamber(
                f"unknown chamber_id {chamber_id!r}; configured: {', '.join(self.hw.chambers)}"
            )
        return chamber_id

    def _ensure_chambers(self) -> None:
        now = iso(self.clock())
        lo, hi = self.hw.temperature.min_setpoint_c, self.hw.temperature.max_setpoint_c
        with self.db.tx() as conn:
            for cid, c in self.hw.chambers.items():
                sp = min(max(float(c.initial_setpoint_c), lo), hi)
                conn.execute(
                    "INSERT OR IGNORE INTO chamber_control(chamber_id, setpoint_c, source, rationale,"
                    " updated_at_utc) VALUES (?,?,?,?,?)",
                    (cid, sp, "config_initial", "configs/hardware.yaml initial_setpoint_c", now),
                )
            for dev, cid in self.hw.device_bindings().items():
                conn.execute(
                    "INSERT INTO devices(device_id, chamber_id, registered) VALUES (?,?,1)"
                    " ON CONFLICT(device_id) DO UPDATE SET chamber_id=excluded.chamber_id, registered=1",
                    (dev, cid),
                )

    def authorize(self, principal: Principal | None, chamber_id: str,
                  body_device_id: str | None = None) -> str | None:
        """Enforce token<->chamber and device_id<->chamber binding. Returns the effective device id."""
        self.require_chamber(chamber_id)
        if principal is not None and principal.kind == "device" and principal.chamber_id != chamber_id:
            raise CommandError(
                f"device token for chamber {principal.chamber_id} cannot act on chamber {chamber_id}", 403)
        header_id = principal.device_id if principal else None
        if body_device_id and header_id and body_device_id != header_id:
            raise CommandError(
                f"X-Device-Id {header_id!r} does not match body device_id {body_device_id!r}", 403)
        device_id = body_device_id or header_id
        if device_id:
            self._bind_device(device_id, chamber_id)
        return device_id

    def _bind_device(self, device_id: str, chamber_id: str) -> None:
        with self.db.tx() as conn:
            row = conn.execute("SELECT chamber_id FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO devices(device_id, chamber_id, registered, first_seen_at_utc)"
                    " VALUES (?,?,0,?)", (device_id, chamber_id, iso(self.clock())))
                self.db.emit("device", {"device_id": device_id, "chamber_id": chamber_id,
                                        "event": "auto_bound", "registered": False},
                             chamber_id, conn=conn)
            elif row["chamber_id"] != chamber_id:
                raise CommandError(
                    f"device {device_id!r} is bound to chamber {row['chamber_id']} and cannot write "
                    f"chamber {chamber_id}", 403)

    def list_devices(self, chamber_id: str | None = None) -> list[dict]:
        """Registry rows + `active`/`inactive_reason`. If a chamber's latest telemetry is
        measured, only that measured device is active; virtual/other ids are marked inactive
        (kept for history, never deleted). Otherwise devices that have reported are active."""
        if chamber_id:
            rows = self.db.query("SELECT * FROM devices WHERE chamber_id=? ORDER BY device_id", (chamber_id,))
        else:
            rows = self.db.query("SELECT * FROM devices ORDER BY chamber_id, device_id")
        measured: dict[str, str | None] = {}
        for cid in {r["chamber_id"] for r in rows}:
            t = self.db.query_one("SELECT source, device_id FROM telemetry WHERE chamber_id=? ORDER BY id DESC"
                                  " LIMIT 1", (cid,))
            measured[cid] = t["device_id"] if t and t["source"] == "measured" else None
        for r in rows:
            live = measured.get(r["chamber_id"])
            if live:
                r["active"] = r["device_id"] == live
                r["inactive_reason"] = None if r["active"] else (
                    f"chamber {r['chamber_id']} has a measured device ({live}); this id is not the live one")
            else:
                r["active"] = r["last_seen_at_utc"] is not None
                r["inactive_reason"] = None if r["active"] else "never seen"
        return rows

    def _clamp(self, v: float) -> float:
        t = self.hw.temperature
        return min(max(v, t.min_setpoint_c), t.max_setpoint_c)

    def current_control(self, chamber_id: str) -> dict:
        self.require_chamber(chamber_id)
        row = self.db.query_one("SELECT * FROM chamber_control WHERE chamber_id=?", (chamber_id,))
        row["setpoint_c"] = self._clamp(row["setpoint_c"])
        row["control_mode"] = row.get("control_mode") or "auto"
        return row

    # ------------------------------------------------------------------ THE setpoint path
    def set_desired_setpoint(
        self, chamber_id: str, setpoint_c: Any, source: str, rationale: str = "",
        experiment_id: str | None = None,
    ) -> dict:
        """Request a new setpoint. Returns a SetpointResult-shaped dict; never raises for
        out-of-range values (they are clamped / step-limited / rejected and logged)."""
        self.require_chamber(chamber_id)
        t = self.hw.temperature
        now = self.clock()
        source = str(source or "unknown")[:64]
        rationale = str(rationale or "")[:1000]

        requested: float | None
        try:
            if isinstance(setpoint_c, bool):
                raise TypeError("bool is not a temperature")
            requested = float(setpoint_c)
            if not math.isfinite(requested):
                raise ValueError("non-finite")
        except (TypeError, ValueError):
            requested = None

        with self.db.tx() as conn:
            row = dict(conn.execute(
                "SELECT * FROM chamber_control WHERE chamber_id=?", (chamber_id,)
            ).fetchone())
            previous = float(row["setpoint_c"])
            prev_safe = self._clamp(previous)
            clamped = step_limited = rejected = False
            reasons: list[str] = []

            if requested is None:
                applied, rejected = prev_safe, True
                reasons.append(f"rejected: setpoint {setpoint_c!r} is not a finite number")
            else:
                target = self._clamp(requested)
                if target != requested:
                    clamped = True
                    reasons.append(
                        f"clamped {requested:g} -> {target:g} C to limits "
                        f"[{t.min_setpoint_c:g}, {t.max_setpoint_c:g}]"
                    )
                delta = target - prev_safe
                if abs(delta) > t.max_step_c_per_update:
                    target = prev_safe + math.copysign(t.max_step_c_per_update, delta)
                    step_limited = True
                    reasons.append(f"step-limited to {t.max_step_c_per_update:g} C per update")
                applied = round(self._clamp(target), 3)  # final defensive clamp

                last = row.get("last_changed_at_utc")
                if applied != previous and last and t.min_update_interval_s > 0:
                    elapsed = (now - parse_iso(last)).total_seconds()
                    if elapsed < t.min_update_interval_s:
                        rejected = True
                        reasons.append(
                            f"rejected: rate limit ({elapsed:.1f} s since last change < "
                            f"{t.min_update_interval_s:g} s)"
                        )
                        applied = prev_safe

            changed = (not rejected) and applied != previous
            if changed:
                conn.execute(
                    "UPDATE chamber_control SET setpoint_c=?, source=?, rationale=?, updated_at_utc=?,"
                    " last_changed_at_utc=?, experiment_id=COALESCE(?, experiment_id),"
                    " version=version+1 WHERE chamber_id=?",
                    (applied, source, rationale, iso(now), iso(now), experiment_id, chamber_id),
                )
            if not reasons:
                reasons.append("applied" if changed else "unchanged")
            result = {
                "chamber_id": chamber_id,
                "requested_c": requested,
                "previous_c": previous,
                "applied_c": applied if not rejected else prev_safe,
                "changed": changed,
                "clamped": clamped,
                "step_limited": step_limited,
                "rejected": rejected,
                "reason": "; ".join(reasons),
                "source": source,
                "rationale": rationale,
                "at_utc": iso(now),
                "control_mode": row.get("control_mode") or "auto",
            }
            conn.execute(
                "INSERT INTO setpoint_changes(chamber_id, at_utc, requested_c, previous_c, applied_c,"
                " clamped, step_limited, rejected, reason, source, rationale)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (chamber_id, iso(now), requested, previous, result["applied_c"], int(clamped),
                 int(step_limited), int(rejected), result["reason"], source, rationale),
            )
            self.db.emit("setpoint", result, chamber_id, conn=conn)
        return result

    def set_control_mode(self, chamber_id: str, mode: str, source: str, rationale: str = "") -> dict:
        """auto | hold | idle. Never changes the setpoint value; 'idle' asks for Peltier off."""
        self.require_chamber(chamber_id)
        if mode not in ("auto", "hold", "idle"):
            raise CommandError(f"invalid control_mode {mode!r}", 422)
        now = iso(self.clock())
        with self.db.tx() as conn:
            prev = conn.execute("SELECT control_mode, setpoint_c FROM chamber_control WHERE chamber_id=?",
                                (chamber_id,)).fetchone()
            if (prev["control_mode"] or "auto") != mode:
                conn.execute("UPDATE chamber_control SET control_mode=?, updated_at_utc=?,"
                             " version=version+1 WHERE chamber_id=?", (mode, now, chamber_id))
                conn.execute(
                    "INSERT INTO setpoint_changes(chamber_id, at_utc, requested_c, previous_c, applied_c,"
                    " clamped, step_limited, rejected, reason, source, rationale)"
                    " VALUES (?,?,?,?,?,0,0,0,?,?,?)",
                    (chamber_id, now, None, prev["setpoint_c"], prev["setpoint_c"],
                     f"control_mode {prev['control_mode'] or 'auto'} -> {mode}", str(source)[:64],
                     str(rationale)[:1000]))
                self.db.emit("setpoint", {"chamber_id": chamber_id, "control_mode": mode,
                                          "previous_control_mode": prev["control_mode"] or "auto",
                                          "source": source}, chamber_id, conn=conn)
        return {"chamber_id": chamber_id, "control_mode": mode}

    # ------------------------------------------------------------------ sequences / expiry
    def _next_seq(self, conn, chamber_id: str) -> int:
        """One monotonic counter per chamber across all command types (v1.1)."""
        row = conn.execute("SELECT value FROM sequences WHERE chamber_id=? AND command_type='all'",
                           (chamber_id,)).fetchone()
        if row is None:  # first use (or DB from v1.0 with per-type counters): continue above them
            start = conn.execute(
                "SELECT MAX(m) FROM (SELECT MAX(value) AS m FROM sequences WHERE chamber_id=?"
                " UNION ALL SELECT MAX(sequence_number) FROM commands WHERE chamber_id=?)",
                (chamber_id, chamber_id)).fetchone()[0] or 0
            conn.execute("INSERT INTO sequences(chamber_id, command_type, value) VALUES (?, 'all', ?)",
                         (chamber_id, start + 1))
            return start + 1
        conn.execute("UPDATE sequences SET value=value+1 WHERE chamber_id=? AND command_type='all'",
                     (chamber_id,))
        return row["value"] + 1

    def _insert_command(self, conn, chamber_id: str, command_type: str, ttl_s: float,
                        payload: dict, source: str, reason: str) -> dict:
        now = self.clock()
        seq = self._next_seq(conn, chamber_id)
        cmd = {
            "command_id": str(uuid.uuid4()),
            "chamber_id": chamber_id,
            "command_type": command_type,
            "action": ACTION_OF[command_type],
            "sequence_number": seq,
            "issued_at_utc": iso(now),
            "expires_at_utc": iso(now + timedelta(seconds=ttl_s)),
            "status": "issued",
            "source": str(source)[:64],
            "reason": str(reason)[:200],
        }
        conn.execute(
            "INSERT INTO commands(command_id, chamber_id, command_type, action, sequence_number,"
            " issued_at_utc, expires_at_utc, status, source, reason, payload_json,"
            " status_updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cmd["command_id"], chamber_id, command_type, cmd["action"], seq, cmd["issued_at_utc"],
             cmd["expires_at_utc"], "issued", cmd["source"], cmd["reason"], dumps(payload), iso(now)),
        )
        return {**cmd, **payload}

    def sweep_expired(self) -> int:
        """Mark issued-but-never-acked commands past expiry as expired_unacked."""
        now = iso(self.clock())
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT command_id, chamber_id, command_type, action FROM commands"
                " WHERE status='issued' AND expires_at_utc <= ?", (now,)
            ).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE commands SET status='expired_unacked', status_updated_at_utc=?"
                    " WHERE command_id=?", (now, r["command_id"]),
                )
                self.db.emit("command", {"command_id": r["command_id"], "command_type": r["command_type"],
                                         "action": r["action"], "chamber_id": r["chamber_id"],
                                         "status": "expired_unacked"}, r["chamber_id"], conn=conn)
        return len(rows)

    # ------------------------------------------------------------------ desired control
    def peltier_hint(self, chamber_id: str, setpoint_c: float, control_mode: str = "auto") -> str:
        if control_mode == "idle":
            return "idle"
        tel = self.latest_telemetry(chamber_id)
        if not tel:
            return "unknown"
        age = (self.clock() - parse_iso(tel["received_at_utc"])).total_seconds()
        if age > self.hw.telemetry.stale_after_s:
            return "unknown"
        if tel.get("door_open"):
            return "idle"
        temp = tel.get("chamber_temperature_c")
        if temp is None:
            return "unknown"
        band = self.hw.temperature.hint_deadband_c
        if temp < setpoint_c - band:
            return "heat"
        if temp > setpoint_c + band:
            return "cool"
        return "hold"

    def command_object(self, row: dict) -> dict:
        """v1.1 wire representation of a commands row (contracts/command_v1.schema.json)."""
        payload = row.get("payload")
        if payload is None:
            payload = json.loads(row["payload_json"])
        ctype = row["command_type"]
        if ctype == "control":
            params = {"setpoint_c": self._clamp(float(payload["temperature_setpoint_c"])),
                      "control_mode": payload.get("control_mode") or "auto"}
        elif ctype == "motor":
            params = {"duration_ms": int(round(float(payload["duration_s"]) * 1000)),
                      "pattern": payload.get("pattern") or "wiggle"}
        elif ctype == "motor_stop":
            params = {"target_command_id": payload.get("target_command_id")}
        else:
            params = {}
        return {
            "schema_version": SCHEMA_VERSION,
            "command_id": row["command_id"],
            "chamber_id": row["chamber_id"],
            "experiment_id": payload.get("experiment_id"),
            "action": ACTION_OF.get(ctype, "noop"),
            "parameters": params,
            "issued_at_utc": row["issued_at_utc"],
            "expires_at_utc": row["expires_at_utc"],
            "sequence_number": row["sequence_number"],
        }

    def pending_commands(self, chamber_id: str) -> list[dict]:
        now = iso(self.clock())
        rows = self.db.query(
            "SELECT * FROM commands WHERE chamber_id=? AND status='issued' AND expires_at_utc > ?"
            " ORDER BY sequence_number", (chamber_id, now))
        return [self.command_object(r) for r in rows]

    def desired_control(self, chamber_id: str) -> dict:
        self.require_chamber(chamber_id)
        self.sweep_expired()
        now = self.clock()
        cfg = self.hw.commands
        ctl = self.current_control(chamber_id)
        setpoint, mode = ctl["setpoint_c"], ctl["control_mode"]
        with self.db.tx() as conn:
            latest = conn.execute(
                "SELECT * FROM commands WHERE chamber_id=? AND command_type='control'"
                " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,),
            ).fetchone()
            reuse = False
            if latest is not None:
                payload = json.loads(latest["payload_json"])
                remaining = (parse_iso(latest["expires_at_utc"]) - now).total_seconds()
                # An identical setpoint the device rejected/failed is retried at the normal re-issue
                # cadence (~45 s), not on every poll (would flood a device that keeps refusing).
                reuse = (
                    latest["status"] in ("issued", "accepted", "rejected", "failed")
                    and remaining > cfg.reissue_when_remaining_s
                    and payload.get("temperature_setpoint_c") == setpoint
                    and (payload.get("control_mode") or "auto") == mode
                )
            if reuse:
                cmd = dict(latest)
                payload = json.loads(cmd["payload_json"])
            else:
                if latest is not None and latest["status"] == "issued":
                    conn.execute(
                        "UPDATE commands SET status='superseded', status_updated_at_utc=?"
                        " WHERE command_id=?", (iso(now), latest["command_id"]),
                    )
                payload = {
                    "temperature_setpoint_c": setpoint,
                    "control_mode": mode,
                    "experiment_id": ctl.get("experiment_id"),
                    "setpoint_source": ctl.get("source"),
                }
                cmd = self._insert_command(conn, chamber_id, "control", cfg.ttl_s, payload,
                                           ctl.get("source") or "server", ctl.get("rationale") or "")
                self.db.emit("command", {k: cmd[k] for k in (
                    "command_id", "command_type", "action", "chamber_id", "sequence_number",
                    "issued_at_utc", "expires_at_utc", "status", "temperature_setpoint_c",
                    "control_mode", "experiment_id", "setpoint_source")}, chamber_id, conn=conn)
            conn.execute(
                "UPDATE commands SET delivered_count=delivered_count+1,"
                " first_delivered_at_utc=COALESCE(first_delivered_at_utc, ?) WHERE command_id=?",
                (iso(now), cmd["command_id"]),
            )
        remaining = max(0, int((parse_iso(cmd["expires_at_utc"]) - now).total_seconds()))
        t = self.hw.temperature
        return {
            "schema_version": SCHEMA_VERSION,
            "command_type": "control",
            "command_id": cmd["command_id"],
            "sequence_number": cmd["sequence_number"],
            "server_epoch": self.db.meta("server_epoch"),
            "issued_at_utc": cmd["issued_at_utc"],
            "expires_at_utc": cmd["expires_at_utc"],
            "ttl_s": remaining,
            "server_time_utc": iso(now),
            "chamber_id": chamber_id,
            "experiment_id": payload.get("experiment_id"),
            "temperature_setpoint_c": self._clamp(float(payload["temperature_setpoint_c"])),
            "control_mode": payload.get("control_mode") or "auto",
            "peltier_mode_hint": self.peltier_hint(chamber_id, setpoint, mode),
            "motor": self.motor_directive(chamber_id),
            "limits": {
                "min_setpoint_c": t.min_setpoint_c,
                "max_setpoint_c": t.max_setpoint_c,
                "max_step_c_per_update": t.max_step_c_per_update,
                "motor_max_duration_s": self.hw.motor.max_duration_s,
            },
            "poll_interval_s": self.hw.polling.desired_control_interval_s,
            "commands": self.pending_commands(chamber_id),
        }

    # ------------------------------------------------------------------ motor / capture
    def issue_motor_command(
        self, chamber_id: str, duration_s: float | None = None, pattern: str = "wiggle",
        reason: str = "", source: str = "dashboard", experiment_id: str | None = None,
        supersede: bool = True,
    ) -> dict:
        self.require_chamber(chamber_id)
        m = self.hw.motor
        duration = m.default_duration_s if duration_s is None else float(duration_s)
        if not math.isfinite(duration) or duration <= 0:
            raise CommandError("motor duration must be a positive finite number", 422)
        if duration > m.max_duration_s:
            raise CommandError(
                f"motor duration {duration:g} s exceeds max_duration_s {m.max_duration_s:g}", 422
            )
        if pattern not in m.patterns:
            raise CommandError(f"unknown motor pattern {pattern!r}; allowed: {m.patterns}", 422)
        now = self.clock()
        payload = {"action": "run", "duration_s": duration, "pattern": pattern,
                   "experiment_id": experiment_id}
        with self.db.tx() as conn:
            # A newer motor command supersedes any not-yet-started one (thinking pulses don't).
            if supersede:
                conn.execute(
                    "UPDATE commands SET status='superseded', status_updated_at_utc=? WHERE chamber_id=?"
                    " AND command_type='motor' AND status='issued'", (iso(now), chamber_id),
                )
            cmd = self._insert_command(conn, chamber_id, "motor", m.ttl_s, payload, source, reason)
            cmd["ran"] = False
            self.db.emit("command", cmd, chamber_id, conn=conn)
        return cmd

    def withdraw_motor_commands(self, command_ids: list[str], reason: str = "withdrawn") -> list[str]:
        """Not-yet-picked-up motor commands -> superseded (silently, no stop traffic)."""
        if not command_ids:
            return []
        now = iso(self.clock())
        out = []
        with self.db.tx() as conn:
            for cid in command_ids:
                row = conn.execute("SELECT chamber_id, status FROM commands WHERE command_id=? AND"
                                   " command_type='motor'", (cid,)).fetchone()
                if row and row["status"] == "issued":
                    conn.execute("UPDATE commands SET status='superseded', status_updated_at_utc=?,"
                                 " reason=? WHERE command_id=?", (now, reason[:200], cid))
                    self.db.emit("command", {"command_id": cid, "command_type": "motor", "action": "animate_motor",
                                             "chamber_id": row["chamber_id"], "status": "superseded",
                                             "reason": reason}, row["chamber_id"], conn=conn)
                    out.append(cid)
        return out

    def issue_capture_command(self, chamber_id: str, source: str = "dashboard", reason: str = "") -> dict:
        self.require_chamber(chamber_id)
        with self.db.tx() as conn:
            cmd = self._insert_command(conn, chamber_id, "capture", self.hw.capture_image.ttl_s, {},
                                       source, reason)
            self.db.emit("command", cmd, chamber_id, conn=conn)
        return cmd

    def _stop_confirmed(self, motor_command_id: str) -> bool:
        if self.db.query_one(
                "SELECT 1 FROM acks WHERE command_id=? AND status IN ('cancelled','completed',"
                "'failed','rejected') LIMIT 1", (motor_command_id,)):
            return True
        return bool(self.db.query_one(
            "SELECT 1 FROM commands WHERE command_type='motor_stop' AND acked=1"
            " AND json_extract(payload_json, '$.target_command_id')=? LIMIT 1", (motor_command_id,)))

    def motor_directive(self, chamber_id: str) -> dict:
        now = self.clock()
        none = {"command_id": None, "action": "none", "run": False, "duration_s": 0.0,
                "pattern": None, "sequence_number": None, "ttl_s": None, "expires_at_utc": None}
        row = self.db.query_one(
            "SELECT * FROM commands WHERE chamber_id=? AND command_type='motor'"
            " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,),
        )
        if row is None:
            return none
        payload = json.loads(row["payload_json"])
        expires = parse_iso(row["expires_at_utc"])
        base = {"command_id": row["command_id"], "sequence_number": row["sequence_number"],
                "pattern": payload.get("pattern")}
        if row["status"] == "issued" and now < expires:
            return {**none, **base, "action": "run", "run": True,
                    "duration_s": float(payload["duration_s"]),
                    "ttl_s": int((expires - now).total_seconds()),
                    "expires_at_utc": row["expires_at_utc"]}
        if row["status"] == "cancelled":
            window = timedelta(seconds=self.hw.motor.stop_directive_window_s)
            if (not self._stop_confirmed(row["command_id"])
                    and now < parse_iso(row["status_updated_at_utc"]) + window):
                return {**none, **base, "action": "stop", "run": False}
        return none

    def cancel_command(self, command_id: str) -> dict:
        now = iso(self.clock())
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row is None:
                raise CommandError(f"unknown command_id {command_id!r}", 404)
            if row["command_type"] != "motor":
                raise CommandError(
                    "only animate_motor commands can be cancelled; to change temperature request a "
                    "new setpoint (POST /api/v1/chambers/{id}/setpoint)", 409)
            if row["status"] in TERMINAL_MOTOR - {"cancelled"} or row["status"] == "superseded":
                return {"command_id": command_id, "status": row["status"], "cancelled": False,
                        "reason": f"command already {row['status']}"}
            conn.execute(
                "UPDATE commands SET status='cancelled', status_updated_at_utc=? WHERE command_id=?",
                (now, command_id),
            )
            stop = self._insert_command(
                conn, row["chamber_id"], "motor_stop", self.hw.motor.stop_directive_window_s,
                {"target_command_id": command_id}, "cancel", f"cancel {command_id}")
            evt = {"command_id": command_id, "command_type": "motor", "action": "animate_motor",
                   "chamber_id": row["chamber_id"], "status": "cancelled",
                   "stop_command_id": stop["command_id"]}
            self.db.emit("command", evt, row["chamber_id"], conn=conn)
            self.db.emit("command", stop, row["chamber_id"], conn=conn)
        return {**evt, "cancelled": True}

    # ------------------------------------------------------------------ acks
    def record_ack(self, ack: ControlAckIn, principal: Principal | None = None) -> dict:
        device_id = self.authorize(principal, ack.chamber_id, ack.device_id)
        now = self.clock()
        result = ack.result or {}
        applied = ack.applied_setpoint_c
        if applied is None:
            v = result.get("applied_setpoint_c", result.get("setpoint_c"))
            applied = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        message = ack.message or ack.detail
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (ack.command_id,)).fetchone()
            if row is None:
                raise CommandError(f"unknown command_id {ack.command_id!r}", 404)
            if row["chamber_id"] != ack.chamber_id:
                raise CommandError(
                    f"command {ack.command_id} belongs to chamber {row['chamber_id']}, "
                    f"not {ack.chamber_id}", 409)
            late = ack.status == "accepted" and now > parse_iso(row["expires_at_utc"])
            new_status = row["status"]
            if row["status"] != "cancelled" or ack.status in ("cancelled", "completed", "failed"):
                new_status = ack.status
            conn.execute(
                "UPDATE commands SET status=?, acked=1, status_updated_at_utc=? WHERE command_id=?",
                (new_status, iso(now), ack.command_id),
            )
            conn.execute(
                "INSERT INTO acks(command_id, chamber_id, status, received_at_utc, device_timestamp_utc,"
                " late, payload_json) VALUES (?,?,?,?,?,?,?)",
                (ack.command_id, ack.chamber_id, ack.status, iso(now),
                 iso(ack.timestamp_utc) if ack.timestamp_utc else None, int(late),
                 dumps({**ack.model_dump(mode="json"), "effective_device_id": device_id})),
            )
            if device_id:
                conn.execute("UPDATE devices SET last_seen_at_utc=?, last_uptime_ms=COALESCE(?, last_uptime_ms)"
                             " WHERE device_id=?", (iso(now), ack.device_uptime_ms, device_id))
            action = row["action"] or ACTION_OF.get(row["command_type"])
            evt = {"command_id": ack.command_id, "command_type": row["command_type"], "action": action,
                   "chamber_id": ack.chamber_id, "status": ack.status, "command_status": new_status,
                   "late": late, "applied_setpoint_c": applied, "message": message,
                   "device_id": device_id, "result": ack.result,
                   "motor_ran": (ack.status == "completed") if row["command_type"] == "motor" else None}
            self.db.emit("ack", evt, ack.chamber_id, conn=conn)
            cmd_source = row["source"]
        try:
            self.door_rule.on_ack(ack.chamber_id, cmd_source, ack.status, message)
        except Exception:
            logging.getLogger("fermentai.api.door").exception("door rule failed")
        return {"ok": True, "command_id": ack.command_id, "command_type": row["command_type"],
                "action": action, "command_status": new_status, "late": late,
                "server_time_utc": iso(now)}

    def _decorate_command(self, r: dict) -> dict:
        loads_cols(r, "payload_json")
        r["acked"] = bool(r["acked"])
        r["action"] = r.get("action") or ACTION_OF.get(r["command_type"])
        r["acks"] = [loads_cols(a, "payload_json") for a in self.db.query(
            "SELECT * FROM acks WHERE command_id=? ORDER BY id", (r["command_id"],))]
        if r["command_type"] == "motor":
            # PLAN_MASTER §13: a motor has run only once the DEVICE acked `completed`.
            r["ran"] = any(a["status"] == "completed" for a in r["acks"])
        return r

    def list_commands(self, chamber_id: str | None = None, command_type: str | None = None,
                      status: str | None = None, limit: int = 100, action: str | None = None) -> list[dict]:
        self.sweep_expired()
        sql, params = "SELECT * FROM commands WHERE 1=1", []
        for col, val in (("chamber_id", chamber_id), ("command_type", command_type), ("status", status),
                         ("action", action)):
            if val:
                sql += f" AND {col}=?"
                params.append(val)
        sql += " ORDER BY issued_at_utc DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [self._decorate_command(r) for r in self.db.query(sql, params)]

    # ------------------------------------------------------------------ telemetry
    def ingest_telemetry(self, t: TelemetryIn, principal: Principal | None = None) -> dict:
        device_id = self.authorize(principal, t.chamber_id, t.device_id)
        now = self.clock()
        if device_id and t.boot_id is not None and t.sequence_number is not None:
            dup = self.db.query_one(
                "SELECT id, received_at_utc, quality_flags_json FROM telemetry WHERE device_id=? AND"
                " boot_id=? AND sequence_number=?", (device_id, t.boot_id, t.sequence_number))
            if dup:
                return {"ok": True, "telemetry_id": dup["id"], "chamber_id": t.chamber_id,
                        "received_at_utc": dup["received_at_utc"], "server_time_utc": iso(now),
                        "quality_flags": json.loads(dup["quality_flags_json"]), "duplicate": True}
        flags = list(t.quality_flags)
        ts_iso: str | None = None
        if t.timestamp_utc is None:
            flags.append("timestamp_unavailable")  # left unset; see received_at_utc
        else:
            if t.timestamp_utc.tzinfo is None:
                flags.append("timestamp_without_timezone_assumed_utc")
            ts_iso = iso(t.timestamp_utc)
            skew = abs((parse_iso(ts_iso) - now).total_seconds())
            if skew > self.hw.telemetry.clock_skew_flag_s:
                flags.append(f"device_clock_skew_{int(skew)}s")
        if t.ph is not None and not (0.0 <= t.ph <= 14.0):
            flags.append("ph_out_of_range")
        if t.relative_humidity_percent is not None and not (0 <= t.relative_humidity_percent <= 100):
            flags.append("humidity_out_of_range")
        flags = list(dict.fromkeys(flags))
        source = SOURCE_TO_PROVENANCE[t.source]
        data = t.model_dump(mode="json")
        data["source_as_sent"] = t.source
        data["source"] = source
        data["device_id"] = device_id
        cols = {c: getattr(t, c) for c in TELEMETRY_COLUMNS}
        cols["peltier_mode"] = PELTIER_CANONICAL.get(t.peltier_mode, t.peltier_mode)
        for b in ("door_open", "motor_running"):
            cols[b] = None if cols[b] is None else int(cols[b])
        health = (t.diagnostics.sensor_health if t.diagnostics else {}) or {}
        prev_door = self.door_rule.previous_door(t.chamber_id)
        with self.db.tx() as conn:
            tid = conn.execute(
                f"INSERT INTO telemetry(chamber_id, experiment_id, timestamp_utc, received_at_utc, source,"
                f" {', '.join(TELEMETRY_COLUMNS)}, quality_flags_json, payload_json, device_id, boot_id,"
                f" sequence_number, timestamp_quality, device_uptime_ms)"
                f" VALUES ({','.join('?' * (len(TELEMETRY_COLUMNS) + 12))})",
                (t.chamber_id, t.experiment_id, ts_iso, iso(now), source, *cols.values(),
                 dumps(flags), dumps(data), device_id, t.boot_id, t.sequence_number,
                 t.timestamp_quality, t.device_uptime_ms),
            ).lastrowid
            for fname, (unit, sensors) in READING_FIELDS.items():
                val = getattr(t, fname)
                if val is None:
                    continue
                quality = health.get(fname) or next((health[s] for s in sensors if s in health), None)
                conn.execute(
                    "INSERT INTO sensor_readings(telemetry_id, chamber_id, device_id, experiment_id, field,"
                    " value, unit, provenance, quality, timestamp_utc, received_at_utc, device_uptime_ms)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, t.chamber_id, device_id, t.experiment_id, fname, float(val), unit,
                     t.field_provenance.get(fname) or source, quality, ts_iso, iso(now),
                     t.device_uptime_ms),
                )
            if device_id:
                prev = conn.execute("SELECT last_boot_id FROM devices WHERE device_id=?",
                                    (device_id,)).fetchone()
                fw = t.firmware_version or (t.diagnostics.firmware_version if t.diagnostics else None)
                conn.execute(
                    "UPDATE devices SET last_seen_at_utc=?, first_seen_at_utc=COALESCE(first_seen_at_utc, ?),"
                    " last_boot_id=COALESCE(?, last_boot_id), last_sequence_number=COALESCE(?, last_sequence_number),"
                    " last_uptime_ms=COALESCE(?, last_uptime_ms), firmware_version=COALESCE(?, firmware_version),"
                    " last_source=? WHERE device_id=?",
                    (iso(now), iso(now), t.boot_id, t.sequence_number, t.device_uptime_ms, fw, source,
                     device_id))
                if prev and t.boot_id and prev["last_boot_id"] and prev["last_boot_id"] != t.boot_id:
                    self.db.emit("device", {"device_id": device_id, "chamber_id": t.chamber_id,
                                            "event": "rebooted", "boot_id": t.boot_id,
                                            "previous_boot_id": prev["last_boot_id"]},
                                 t.chamber_id, conn=conn)
            evt = {**data, "telemetry_id": tid, "timestamp_utc": ts_iso, "received_at_utc": iso(now),
                   "quality_flags": flags, "peltier_mode": cols["peltier_mode"]}
            self.db.emit("telemetry", evt, t.chamber_id, conn=conn)
        extra = (data.get("diagnostics") or {})
        opens = extra.get("door_opens")
        try:
            self.door_rule.on_telemetry(
                t.chamber_id, prev_door, t.door_open, opens if isinstance(opens, int) else None,
                legacy="legacy_status_log" in flags)
        except Exception:  # the door rule must never break telemetry ingest
            logging.getLogger("fermentai.api.door").exception("door rule failed")
        return {"ok": True, "telemetry_id": tid, "chamber_id": t.chamber_id,
                "received_at_utc": iso(now), "server_time_utc": iso(now), "quality_flags": flags,
                "duplicate": False}

    @staticmethod
    def _telemetry_row(r: dict) -> dict:
        loads_cols(r, "quality_flags_json", "payload_json")
        for b in ("door_open", "motor_running"):
            if r.get(b) is not None:
                r[b] = bool(r[b])
        p = r.get("payload") or {}
        r["field_provenance"] = p.get("field_provenance") or {}
        r["time_basis"] = "device" if r.get("timestamp_utc") else "server_receipt"
        return r

    def latest_telemetry(self, chamber_id: str) -> dict | None:
        r = self.db.query_one(
            "SELECT * FROM telemetry WHERE chamber_id=? ORDER BY id DESC LIMIT 1", (chamber_id,))
        return self._telemetry_row(r) if r else None

    def telemetry_history(self, chamber_id: str, limit: int = 500, since_id: int = 0) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM (SELECT * FROM telemetry WHERE chamber_id=? AND id>? ORDER BY id DESC"
            " LIMIT ?) ORDER BY id", (chamber_id, since_id, limit))
        return [self._telemetry_row(r) for r in rows]

    def readings(self, chamber_id: str, field: str | None = None, limit: int = 500) -> list[dict]:
        self.require_chamber(chamber_id)
        sql, params = "SELECT * FROM sensor_readings WHERE chamber_id=?", [chamber_id]
        if field:
            sql += " AND field=?"
            params.append(field)
        return self.db.query(f"SELECT * FROM ({sql} ORDER BY id DESC LIMIT ?) ORDER BY id",
                             [*params, limit])

    # ------------------------------------------------------------------ state
    def _images_for_state(self, chamber_id: str, tel: dict | None) -> tuple[dict | None, dict | None]:
        """latest_image only if it comes from the device (and provenance) of the latest telemetry,
        or if there is no telemetry device to compare with. A newer image from another device /
        source is returned separately so the dashboard can hide or label it."""
        def fmt(r):
            return {**r, "url": f"/api/v1/images/{r['id']}"} if r else None
        cols = "id, timestamp_utc, received_at_utc, source, device_id"
        newest = self.db.query_one(f"SELECT {cols} FROM images WHERE chamber_id=? ORDER BY id DESC LIMIT 1",
                                   (chamber_id,))
        tel_dev = tel.get("device_id") if tel else None
        if newest is None or not tel_dev:
            return fmt(newest), None
        match = self.db.query_one(
            f"SELECT {cols} FROM images WHERE chamber_id=? AND device_id=? AND source=? ORDER BY id DESC LIMIT 1",
            (chamber_id, tel_dev, tel["source"]))
        stale = None
        if match is None or match["id"] != newest["id"]:
            stale = {**fmt(newest), "reason": (
                f"image from device {newest['device_id'] or 'unknown'} ({newest['source']}) but latest "
                f"telemetry is from {tel_dev} ({tel['source']})")}
        return fmt(match), stale

    def chamber_state(self, chamber_id: str) -> dict:
        self.require_chamber(chamber_id)
        now = self.clock()
        tel = self.latest_telemetry(chamber_id)
        age = (now - parse_iso(tel["received_at_utc"])).total_seconds() if tel else None
        ctl = self.current_control(chamber_id)
        last_ctl = self.db.query_one(
            "SELECT command_id, sequence_number, issued_at_utc, expires_at_utc, status, acked"
            " FROM commands WHERE chamber_id=? AND command_type='control'"
            " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,))
        last_motor = self.db.query_one(
            "SELECT * FROM commands WHERE chamber_id=? AND command_type='motor'"
            " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,))
        if last_motor:
            last_motor = self._decorate_command(last_motor)
            last_motor.pop("acks", None)
        img, stale_img = self._images_for_state(chamber_id, tel)
        devs = self.list_devices(chamber_id)
        last_change = self.db.query_one(
            "SELECT * FROM setpoint_changes WHERE chamber_id=? ORDER BY id DESC LIMIT 1", (chamber_id,))
        cfg = self.hw.chambers[chamber_id]
        return {
            "chamber_id": chamber_id,
            "label": cfg.label,
            "location": cfg.location,
            "kind": cfg.kind,
            "device_id": cfg.device_id,
            "devices": [d for d in devs if d["active"]],
            "inactive_devices": [{"device_id": d["device_id"], "reason": d["inactive_reason"],
                                  "last_source": d["last_source"]} for d in devs if not d["active"]],
            "server_time_utc": iso(now),
            "latest_telemetry": tel,
            "telemetry_age_s": age,
            "telemetry_stale": age is None or age > self.hw.telemetry.stale_after_s,
            "provenance": tel["source"] if tel else None,
            "desired_setpoint_c": ctl["setpoint_c"],
            "control_mode": ctl["control_mode"],
            "setpoint_source": ctl["source"],
            "setpoint_rationale": ctl["rationale"],
            "setpoint_updated_at_utc": ctl["updated_at_utc"],
            "experiment_id": ctl.get("experiment_id"),
            "device_reported_setpoint_c": tel.get("temperature_setpoint_c") if tel else None,
            "peltier_mode_hint": self.peltier_hint(chamber_id, ctl["setpoint_c"], ctl["control_mode"]),
            "last_setpoint_change": last_change,
            "last_control_command": last_ctl,
            "last_motor_command": last_motor,
            "motor_ran": bool(last_motor and last_motor.get("ran")),
            "pending_commands": self.pending_commands(chamber_id),
            "latest_image": img,
            "stale_image_from_other_device": stale_img,
            "limits": {
                "min_setpoint_c": self.hw.temperature.min_setpoint_c,
                "max_setpoint_c": self.hw.temperature.max_setpoint_c,
            },
        }


# --------------------------------------------------------------------------- module-level API
_default_service: ControlService | None = None


def set_default_service(svc: ControlService | None) -> None:
    global _default_service
    _default_service = svc


def get_default_service() -> ControlService:
    """The app's service if running in-process, else one built from env settings (shared DB)."""
    global _default_service
    if _default_service is None:
        from .config import Settings

        s = Settings.from_env()
        _default_service = ControlService(Database(s.db_path), s.hardware)
    return _default_service


def set_desired_setpoint(chamber_id: str, setpoint_c: float, source: str, rationale: str = "") -> dict:
    """THE ONLY entry point for agents/LLM/judge-derived schedules to influence temperature.

    Enforces hardware.yaml bounds, max step per update and min update interval; logs everything.
    """
    return get_default_service().set_desired_setpoint(chamber_id, setpoint_c, source, rationale)
