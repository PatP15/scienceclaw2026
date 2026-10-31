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
  duration/pattern; they cannot express a setpoint or Peltier mode.
* The ESP32 enforces its own compiled-in limits independently (firmware_contract/).
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

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
        self._ensure_chambers()

    # ------------------------------------------------------------------ chambers
    @property
    def chamber_ids(self) -> list[str]:
        return list(self.hw.chambers)

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

    def _clamp(self, v: float) -> float:
        t = self.hw.temperature
        return min(max(v, t.min_setpoint_c), t.max_setpoint_c)

    def current_control(self, chamber_id: str) -> dict:
        self.require_chamber(chamber_id)
        row = self.db.query_one("SELECT * FROM chamber_control WHERE chamber_id=?", (chamber_id,))
        row["setpoint_c"] = self._clamp(row["setpoint_c"])
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

    # ------------------------------------------------------------------ sequences / expiry
    def _next_seq(self, conn, chamber_id: str, command_type: str) -> int:
        conn.execute(
            "INSERT INTO sequences(chamber_id, command_type, value) VALUES (?,?,1)"
            " ON CONFLICT(chamber_id, command_type) DO UPDATE SET value=value+1",
            (chamber_id, command_type),
        )
        return conn.execute(
            "SELECT value FROM sequences WHERE chamber_id=? AND command_type=?",
            (chamber_id, command_type),
        ).fetchone()[0]

    def sweep_expired(self) -> int:
        """Mark issued-but-never-acked commands past expiry as expired_unacked."""
        now = iso(self.clock())
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT command_id, chamber_id, command_type FROM commands"
                " WHERE status='issued' AND expires_at_utc <= ?", (now,)
            ).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE commands SET status='expired_unacked', status_updated_at_utc=?"
                    " WHERE command_id=?", (now, r["command_id"]),
                )
                self.db.emit("command", {"command_id": r["command_id"], "command_type": r["command_type"],
                                         "chamber_id": r["chamber_id"], "status": "expired_unacked"},
                             r["chamber_id"], conn=conn)
        return len(rows)

    # ------------------------------------------------------------------ desired control
    def peltier_hint(self, chamber_id: str, setpoint_c: float) -> str:
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

    def desired_control(self, chamber_id: str) -> dict:
        self.require_chamber(chamber_id)
        self.sweep_expired()
        now = self.clock()
        cfg = self.hw.commands
        ctl = self.current_control(chamber_id)
        setpoint = ctl["setpoint_c"]
        new_cmd_event = None
        with self.db.tx() as conn:
            latest = conn.execute(
                "SELECT * FROM commands WHERE chamber_id=? AND command_type='control'"
                " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,),
            ).fetchone()
            reuse = False
            if latest is not None:
                payload = json.loads(latest["payload_json"])
                remaining = (parse_iso(latest["expires_at_utc"]) - now).total_seconds()
                reuse = (
                    latest["status"] in ("issued", "accepted")
                    and remaining > cfg.reissue_when_remaining_s
                    and payload.get("temperature_setpoint_c") == setpoint
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
                seq = self._next_seq(conn, chamber_id, "control")
                payload = {
                    "temperature_setpoint_c": setpoint,
                    "experiment_id": ctl.get("experiment_id"),
                    "setpoint_source": ctl.get("source"),
                }
                cmd = {
                    "command_id": str(uuid.uuid4()),
                    "chamber_id": chamber_id,
                    "command_type": "control",
                    "sequence_number": seq,
                    "issued_at_utc": iso(now),
                    "expires_at_utc": iso(now + timedelta(seconds=cfg.ttl_s)),
                    "status": "issued",
                    "source": ctl.get("source") or "server",
                    "reason": ctl.get("rationale") or "",
                }
                conn.execute(
                    "INSERT INTO commands(command_id, chamber_id, command_type, sequence_number,"
                    " issued_at_utc, expires_at_utc, status, source, reason, payload_json,"
                    " status_updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cmd["command_id"], chamber_id, "control", seq, cmd["issued_at_utc"],
                     cmd["expires_at_utc"], "issued", cmd["source"], cmd["reason"], dumps(payload),
                     iso(now)),
                )
                new_cmd_event = {**{k: cmd[k] for k in ("command_id", "command_type", "chamber_id",
                                                        "sequence_number", "issued_at_utc",
                                                        "expires_at_utc", "status")}, **payload}
                self.db.emit("command", new_cmd_event, chamber_id, conn=conn)
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
            "peltier_mode_hint": self.peltier_hint(chamber_id, setpoint),
            "motor": self.motor_directive(chamber_id),
            "limits": {
                "min_setpoint_c": t.min_setpoint_c,
                "max_setpoint_c": t.max_setpoint_c,
                "max_step_c_per_update": t.max_step_c_per_update,
                "motor_max_duration_s": self.hw.motor.max_duration_s,
            },
            "poll_interval_s": self.hw.polling.desired_control_interval_s,
        }

    # ------------------------------------------------------------------ motor
    def issue_motor_command(
        self, chamber_id: str, duration_s: float | None = None, pattern: str = "wiggle",
        reason: str = "", source: str = "dashboard",
    ) -> dict:
        self.require_chamber(chamber_id)
        m = self.hw.motor
        duration = m.default_duration_s if duration_s is None else float(duration_s)
        if not math.isfinite(duration) or duration <= 0:
            raise CommandError("motor duration_s must be a positive finite number", 422)
        if duration > m.max_duration_s:
            raise CommandError(
                f"motor duration_s {duration:g} exceeds max_duration_s {m.max_duration_s:g}", 422
            )
        if pattern not in m.patterns:
            raise CommandError(f"unknown motor pattern {pattern!r}; allowed: {m.patterns}", 422)
        now = self.clock()
        payload = {"action": "run", "duration_s": duration, "pattern": pattern}
        with self.db.tx() as conn:
            # A newer motor command supersedes any not-yet-started one.
            conn.execute(
                "UPDATE commands SET status='superseded', status_updated_at_utc=? WHERE chamber_id=?"
                " AND command_type='motor' AND status='issued'", (iso(now), chamber_id),
            )
            seq = self._next_seq(conn, chamber_id, "motor")
            cmd = {
                "command_id": str(uuid.uuid4()),
                "chamber_id": chamber_id,
                "command_type": "motor",
                "sequence_number": seq,
                "issued_at_utc": iso(now),
                "expires_at_utc": iso(now + timedelta(seconds=m.ttl_s)),
                "status": "issued",
                "source": str(source)[:64],
                "reason": str(reason)[:200],
                **payload,
            }
            conn.execute(
                "INSERT INTO commands(command_id, chamber_id, command_type, sequence_number,"
                " issued_at_utc, expires_at_utc, status, source, reason, payload_json,"
                " status_updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cmd["command_id"], chamber_id, "motor", seq, cmd["issued_at_utc"],
                 cmd["expires_at_utc"], "issued", cmd["source"], cmd["reason"], dumps(payload),
                 iso(now)),
            )
            self.db.emit("command", cmd, chamber_id, conn=conn)
        return cmd

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
            device_confirmed = self.db.query_one(
                "SELECT 1 FROM acks WHERE command_id=? AND status IN ('cancelled','completed',"
                "'failed','rejected') LIMIT 1", (row["command_id"],))
            if not device_confirmed and now < parse_iso(row["status_updated_at_utc"]) + window:
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
                    "only motor commands can be cancelled; to change temperature request a new "
                    "setpoint (POST /api/v1/chambers/{id}/setpoint)", 409)
            if row["status"] in TERMINAL_MOTOR - {"cancelled"} or row["status"] == "superseded":
                return {"command_id": command_id, "status": row["status"], "cancelled": False,
                        "reason": f"command already {row['status']}"}
            conn.execute(
                "UPDATE commands SET status='cancelled', status_updated_at_utc=? WHERE command_id=?",
                (now, command_id),
            )
            evt = {"command_id": command_id, "command_type": "motor", "chamber_id": row["chamber_id"],
                   "status": "cancelled"}
            self.db.emit("command", evt, row["chamber_id"], conn=conn)
        return {**evt, "cancelled": True}

    # ------------------------------------------------------------------ acks
    def record_ack(self, ack: ControlAckIn) -> dict:
        self.require_chamber(ack.chamber_id)
        now = self.clock()
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
                 ack.model_dump_json()),
            )
            evt = {"command_id": ack.command_id, "command_type": row["command_type"],
                   "chamber_id": ack.chamber_id, "status": ack.status, "command_status": new_status,
                   "late": late, "applied_setpoint_c": ack.applied_setpoint_c, "message": ack.message}
            self.db.emit("ack", evt, ack.chamber_id, conn=conn)
        return {"ok": True, "command_id": ack.command_id, "command_type": row["command_type"],
                "command_status": new_status, "late": late, "server_time_utc": iso(now)}

    def list_commands(self, chamber_id: str | None = None, command_type: str | None = None,
                      status: str | None = None, limit: int = 100) -> list[dict]:
        self.sweep_expired()
        sql, params = "SELECT * FROM commands WHERE 1=1", []
        for col, val in (("chamber_id", chamber_id), ("command_type", command_type), ("status", status)):
            if val:
                sql += f" AND {col}=?"
                params.append(val)
        sql += " ORDER BY issued_at_utc DESC, rowid DESC LIMIT ?"
        params.append(limit)
        out = []
        for r in self.db.query(sql, params):
            loads_cols(r, "payload_json")
            r["acked"] = bool(r["acked"])
            r["acks"] = [loads_cols(a, "payload_json") for a in self.db.query(
                "SELECT * FROM acks WHERE command_id=? ORDER BY id", (r["command_id"],))]
            out.append(r)
        return out

    # ------------------------------------------------------------------ telemetry
    def ingest_telemetry(self, t: TelemetryIn) -> dict:
        self.require_chamber(t.chamber_id)
        now = self.clock()
        flags = list(t.quality_flags)
        if t.timestamp_utc is None:
            ts = now
            flags.append("timestamp_missing_server_time_used")
        else:
            ts = t.timestamp_utc
            if ts.tzinfo is None:
                flags.append("timestamp_without_timezone_assumed_utc")
            skew = abs((parse_iso(iso(ts)) - now).total_seconds())
            if skew > self.hw.telemetry.clock_skew_flag_s:
                flags.append(f"device_clock_skew_{int(skew)}s")
        if t.ph is not None and not (0.0 <= t.ph <= 14.0):
            flags.append("ph_out_of_range")
        if t.relative_humidity_percent is not None and not (0 <= t.relative_humidity_percent <= 100):
            flags.append("humidity_out_of_range")
        flags = list(dict.fromkeys(flags))
        data = t.model_dump(mode="json")
        cols = {c: getattr(t, c) for c in TELEMETRY_COLUMNS}
        for b in ("door_open", "motor_running"):
            cols[b] = None if cols[b] is None else int(cols[b])
        with self.db.tx() as conn:
            tid = conn.execute(
                f"INSERT INTO telemetry(chamber_id, experiment_id, timestamp_utc, received_at_utc, source,"
                f" {', '.join(TELEMETRY_COLUMNS)}, quality_flags_json, payload_json)"
                f" VALUES ({','.join('?' * (len(TELEMETRY_COLUMNS) + 7))})",
                (t.chamber_id, t.experiment_id, iso(ts), iso(now), t.source, *cols.values(),
                 dumps(flags), dumps(data)),
            ).lastrowid
            evt = {**data, "telemetry_id": tid, "timestamp_utc": iso(ts), "received_at_utc": iso(now),
                   "quality_flags": flags}
            self.db.emit("telemetry", evt, t.chamber_id, conn=conn)
        return {"ok": True, "telemetry_id": tid, "chamber_id": t.chamber_id,
                "received_at_utc": iso(now), "server_time_utc": iso(now), "quality_flags": flags}

    @staticmethod
    def _telemetry_row(r: dict) -> dict:
        loads_cols(r, "quality_flags_json", "payload_json")
        for b in ("door_open", "motor_running"):
            if r.get(b) is not None:
                r[b] = bool(r[b])
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

    # ------------------------------------------------------------------ state
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
            "SELECT command_id, sequence_number, issued_at_utc, expires_at_utc, status, acked,"
            " payload_json FROM commands WHERE chamber_id=? AND command_type='motor'"
            " ORDER BY issued_at_utc DESC, sequence_number DESC LIMIT 1", (chamber_id,))
        img = self.db.query_one(
            "SELECT id, timestamp_utc, source FROM images WHERE chamber_id=? ORDER BY id DESC LIMIT 1",
            (chamber_id,))
        last_change = self.db.query_one(
            "SELECT * FROM setpoint_changes WHERE chamber_id=? ORDER BY id DESC LIMIT 1", (chamber_id,))
        cfg = self.hw.chambers[chamber_id]
        return {
            "chamber_id": chamber_id,
            "label": cfg.label,
            "device_id": cfg.device_id,
            "server_time_utc": iso(now),
            "latest_telemetry": tel,
            "telemetry_age_s": age,
            "telemetry_stale": age is None or age > self.hw.telemetry.stale_after_s,
            "provenance": tel["source"] if tel else None,
            "desired_setpoint_c": ctl["setpoint_c"],
            "setpoint_source": ctl["source"],
            "setpoint_rationale": ctl["rationale"],
            "setpoint_updated_at_utc": ctl["updated_at_utc"],
            "experiment_id": ctl.get("experiment_id"),
            "device_reported_setpoint_c": tel.get("temperature_setpoint_c") if tel else None,
            "peltier_mode_hint": self.peltier_hint(chamber_id, ctl["setpoint_c"]),
            "last_setpoint_change": last_change,
            "last_control_command": last_ctl,
            "last_motor_command": loads_cols(last_motor, "payload_json"),
            "latest_image": (
                {**img, "url": f"/api/v1/images/{img['id']}"} if img else None
            ),
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
