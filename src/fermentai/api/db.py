"""Tiny stdlib-sqlite3 persistence layer (WAL mode, one short-lived connection per operation).

Multiple processes (API server, agent processes) may share the same DB file safely.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TELEMETRY_DDL = """CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber_id TEXT NOT NULL,
    experiment_id TEXT,
    timestamp_utc TEXT,                   -- device time; NULL when the device clock is unsynced
    received_at_utc TEXT NOT NULL,        -- server receipt time (always)
    source TEXT NOT NULL,                 -- measured | simulated | replay  (v1.0 "real" stored as "measured")
    chamber_temperature_c REAL,
    starter_temperature_c REAL,
    relative_humidity_percent REAL,
    gas_resistance_ohms REAL,
    ph REAL,
    ph_method TEXT,
    starter_height_mm REAL,
    door_open INTEGER,
    peltier_mode TEXT,
    temperature_setpoint_c REAL,
    motor_running INTEGER,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL,
    device_id TEXT,
    boot_id TEXT,
    sequence_number INTEGER,
    timestamp_quality TEXT,
    device_uptime_ms INTEGER
);
"""

SCHEMA = TELEMETRY_DDL + """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS ix_telemetry_chamber ON telemetry(chamber_id, id);

CREATE TABLE IF NOT EXISTS chamber_control (
    chamber_id TEXT PRIMARY KEY,
    setpoint_c REAL NOT NULL,
    source TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    updated_at_utc TEXT NOT NULL,
    last_changed_at_utc TEXT,
    experiment_id TEXT,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS setpoint_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber_id TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    requested_c REAL,
    previous_c REAL NOT NULL,
    applied_c REAL NOT NULL,
    clamped INTEGER NOT NULL,
    step_limited INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    rationale TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    chamber_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (chamber_id, command_type)
);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    chamber_id TEXT NOT NULL,
    command_type TEXT NOT NULL,           -- control | motor
    sequence_number INTEGER NOT NULL,
    issued_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,                 -- issued|accepted|rejected|completed|failed|cancelled|expired|superseded|expired_unacked
    source TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    delivered_count INTEGER NOT NULL DEFAULT 0,
    first_delivered_at_utc TEXT,
    acked INTEGER NOT NULL DEFAULT 0,
    status_updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_commands_chamber ON commands(chamber_id, command_type, issued_at_utc);

CREATE TABLE IF NOT EXISTS acks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    chamber_id TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    device_timestamp_utc TEXT,
    late INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    title TEXT NOT NULL,
    hypothesis TEXT,
    status TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    provenance TEXT NOT NULL,
    feedback_id INTEGER,
    schedules_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taste_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    text TEXT NOT NULL,
    loaf_a_notes TEXT,
    loaf_b_notes TEXT,
    judge_name TEXT,
    mode TEXT NOT NULL,
    mode_label TEXT,
    pipeline_available INTEGER,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    feedback_id INTEGER,
    sender TEXT,
    recipient TEXT,
    sender_key TEXT,
    recipient_key TEXT,
    kind TEXT,
    content_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msgs_sender ON agent_messages(sender_key);
CREATE INDEX IF NOT EXISTS ix_msgs_recipient ON agent_messages(recipient_key);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    type TEXT NOT NULL,
    chamber_id TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    experiment_id TEXT,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT
);

-- ---- v1.1 / PLAN_MASTER section 13 tables ------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    chamber_id TEXT NOT NULL,             -- binding: a device may only write its own chamber
    registered INTEGER NOT NULL DEFAULT 0, -- 1 = listed in configs/hardware.yaml
    first_seen_at_utc TEXT,
    last_seen_at_utc TEXT,
    last_boot_id TEXT,
    last_sequence_number INTEGER,
    last_uptime_ms INTEGER,
    firmware_version TEXT,
    last_source TEXT
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telemetry_id INTEGER NOT NULL,
    chamber_id TEXT NOT NULL,
    device_id TEXT,
    experiment_id TEXT,
    field TEXT NOT NULL,                  -- telemetry field name (= sensor role)
    value REAL,
    unit TEXT,
    provenance TEXT NOT NULL,             -- measured | simulated | replay | manual | estimated
    quality TEXT,                         -- ok | missing | stale | invalid | uncalibrated | NULL
    timestamp_utc TEXT,
    received_at_utc TEXT NOT NULL,
    device_uptime_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_readings_chamber_field ON sensor_readings(chamber_id, field, id);

CREATE TABLE IF NOT EXISTS temperature_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    chamber_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    steps_json TEXT NOT NULL              -- [{"t_h":..,"setpoint_c":..}, ...] (proposal; clamped only when applied)
);

CREATE TABLE IF NOT EXISTS scientist_hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    feedback_id INTEGER,
    experiment_id TEXT,
    agent_id TEXT,
    statement TEXT NOT NULL,
    provenance TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT,
    n_points INTEGER,
    source TEXT,
    feedback_id INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    stage TEXT,
    mode TEXT,
    sous_chef TEXT,
    judge_name TEXT,
    turns INTEGER NOT NULL DEFAULT 0,
    transcript_len INTEGER NOT NULL DEFAULT 0,
    handoff_key TEXT,                     -- identity of the last persisted hand-off (cycle_id/hash)
    feedback_id INTEGER,                  -- taste_feedback row created by the last hand-off
    state_json TEXT                       -- last chat_turn result (transcript, stage, ...)
);

CREATE TABLE IF NOT EXISTS molecular_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at_utc TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    system TEXT,
    energy_ev REAL,
    max_force_ev_per_a REAL,
    converged INTEGER,
    provenance TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""

# Columns added after the first release; added with ALTER TABLE on existing DBs (idempotent).
ADDED_COLUMNS = {
    "telemetry": [("device_id", "TEXT"), ("boot_id", "TEXT"), ("sequence_number", "INTEGER"),
                  ("timestamp_quality", "TEXT"), ("device_uptime_ms", "INTEGER")],
    "chamber_control": [("control_mode", "TEXT NOT NULL DEFAULT 'auto'")],
    "commands": [("action", "TEXT")],
    "taste_feedback": [("chat_session_id", "TEXT")],
    "images": [("device_id", "TEXT")],
    "agent_messages": [("message_uid", "TEXT"), ("in_reply_to", "TEXT"), ("chamber_id", "TEXT"),
                       ("cycle_id", "TEXT")],
}

POST_MIGRATION = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_telemetry_dedup ON telemetry(device_id, boot_id, sequence_number)
    WHERE device_id IS NOT NULL AND boot_id IS NOT NULL AND sequence_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_telemetry_chamber ON telemetry(chamber_id, id);
UPDATE commands SET action = CASE command_type WHEN 'control' THEN 'set_setpoint'
    WHEN 'motor' THEN 'animate_motor' ELSE command_type END WHERE action IS NULL;
-- PLAN_MASTER section 13 names as read-only aliases of the existing tables
CREATE VIEW IF NOT EXISTS chambers AS SELECT * FROM chamber_control;
CREATE VIEW IF NOT EXISTS camera_images AS SELECT * FROM images;
CREATE VIEW IF NOT EXISTS experiment_definitions AS SELECT * FROM experiments;
CREATE VIEW IF NOT EXISTS simulation_runs AS SELECT * FROM simulations;
CREATE VIEW IF NOT EXISTS hardware_commands AS SELECT * FROM commands;
CREATE VIEW IF NOT EXISTS command_acknowledgments AS SELECT * FROM acks;
CREATE VIEW IF NOT EXISTS scientist_messages AS SELECT * FROM agent_messages;
CREATE VIEW IF NOT EXISTS sensory_feedback AS SELECT * FROM taste_feedback;
"""


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing DB up to the current schema. Safe to run repeatedly."""
    done: list[str] = []
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    done.append(f"{table}.{name}")
                except sqlite3.OperationalError as e:  # another process migrated concurrently
                    if "duplicate column" not in str(e):
                        raise
    info = {r[1]: r for r in conn.execute("PRAGMA table_info(telemetry)")}
    if info["timestamp_utc"][3]:  # NOT NULL from the first release -> rebuild as nullable
        cols = ", ".join(info)
        conn.execute("ALTER TABLE telemetry RENAME TO telemetry_v10_old")
        conn.executescript(TELEMETRY_DDL)
        conn.execute(f"INSERT INTO telemetry ({cols}) SELECT {cols} FROM telemetry_v10_old")
        conn.execute("DROP TABLE telemetry_v10_old")
        done.append("telemetry.timestamp_utc nullable")
    conn.executescript(POST_MIGRATION)
    return done


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self.migrations_applied = migrate(conn)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('server_epoch', ?)",
                (uuid.uuid4().hex[:12],),
            )
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at_utc', ?)",
                (iso(utcnow()),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction taken with BEGIN IMMEDIATE (serialises read-modify-write)."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    # ---- helpers -----------------------------------------------------------------------
    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self.tx() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def meta(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    # ---- events --------------------------------------------------------------------------
    def emit(self, type_: str, payload: dict, chamber_id: str | None = None, conn=None) -> int:
        params = (iso(utcnow()), type_, chamber_id, dumps(payload))
        sql = "INSERT INTO events(ts_utc, type, chamber_id, payload_json) VALUES (?,?,?,?)"
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self.execute(sql, params)

    def events_since(self, after_id: int, limit: int = 100, types: list[str] | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE id > ?"
        params: list = [after_id]
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            params += types
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [event_row(r) for r in self.query(sql, params)]

    def last_event_id(self) -> int:
        row = self.query_one("SELECT COALESCE(MAX(id), 0) AS m FROM events")
        return int(row["m"]) if row else 0


def event_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "type": r["type"],
        "ts_utc": r["ts_utc"],
        "chamber_id": r["chamber_id"],
        "data": json.loads(r["payload_json"]),
    }


def loads_cols(row: dict | None, *cols: str) -> dict | None:
    """Decode *_json columns in-place into their un-suffixed names."""
    if row is None:
        return None
    for c in cols:
        if c in row:
            raw = row.pop(c)
            row[c.removesuffix("_json")] = json.loads(raw) if raw else None
    return row
