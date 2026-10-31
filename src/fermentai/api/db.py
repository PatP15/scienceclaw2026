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

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber_id TEXT NOT NULL,
    experiment_id TEXT,
    timestamp_utc TEXT NOT NULL,          -- device time, or server receive time if missing
    received_at_utc TEXT NOT NULL,
    source TEXT NOT NULL,                 -- real | simulated | replay
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
    payload_json TEXT NOT NULL
);
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
"""


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
