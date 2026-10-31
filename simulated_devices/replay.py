"""Replay mode: re-send RECORDED telemetry with its original timestamps, labelled source "replay".

Input formats:
  * .jsonl  - one telemetry object per line
  * .json   - a JSON array (e.g. the output of GET /api/v1/chambers/{id}/telemetry; rows with a
              `payload_json` object use that original device payload)
  * .csv    - header row of telemetry field names
Timestamps (`timestamp_utc`) are preserved exactly; pacing follows their deltas / --replay-speed
(0 = as fast as possible). Every record gets source "replay", quality flag "replay" and per-field
provenance "replay". Unknown columns are dropped so the server's strict schema accepts the rows.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from .device import parse_utc
from .network_client import V10_TELEMETRY_FIELDS, V11_TELEMETRY_EXTRAS, DeviceHttpClient

log = logging.getLogger("simulated_devices.replay")

_BOOL = {"door_open", "motor_running", "fan_running"}
_INT = {"sequence_number", "device_uptime_ms", "last_sequence_number"}
_STR = {"schema_version", "chamber_id", "experiment_id", "timestamp_utc", "ph_method", "peltier_mode",
        "source", "device_id", "firmware_version", "last_command_id", "boot_id", "timestamp_quality"}
_LIST = {"quality_flags", "fault_codes"}
_DICT = {"diagnostics", "field_provenance"}
ALLOWED = set(V10_TELEMETRY_FIELDS) | set(V11_TELEMETRY_EXTRAS)


def _coerce(k: str, v: Any) -> Any:
    if v is None or (isinstance(v, str) and v.strip() in ("", "null", "None", "NaN", "nan")):
        return [] if k in _LIST else None
    if not isinstance(v, str):
        return v
    if k in _BOOL:
        return v.strip().lower() in ("1", "true", "yes")
    if k in _INT:
        return int(float(v))
    if k in _STR:
        return v
    if k in _LIST:
        return json.loads(v) if v.strip().startswith("[") else [x for x in v.split(";") if x]
    if k in _DICT:
        return json.loads(v)
    return float(v)


def load_records(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(newline="") as f:
            rows = list(csv.DictReader(f))
    elif path.suffix == ".json":
        rows = json.loads(path.read_text())
    else:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    out = []
    for r in rows:
        if isinstance(r.get("payload_json"), dict):   # backend export row
            base = dict(r["payload_json"])
            base.setdefault("timestamp_utc", r.get("timestamp_utc"))
            r = base
        out.append({k: _coerce(k, v) for k, v in r.items() if k in ALLOWED})
    out.sort(key=lambda r: (parse_utc(r.get("timestamp_utc")) is None,
                            parse_utc(r.get("timestamp_utc")) or 0))
    return out


def to_replay_payload(rec: dict, default_chamber: str, session_id: str | None = None) -> dict:
    p = dict(rec)
    if session_id:  # fresh boot_id so (device_id, boot_id, seq) dedup never drops replayed rows
        p["boot_id"] = f"replay-{session_id}"
    p["schema_version"] = "1.0"
    p["chamber_id"] = p.get("chamber_id") or default_chamber
    p["source"] = "replay"
    p["quality_flags"] = list(dict.fromkeys([*(p.get("quality_flags") or []), "replay"]))
    if p.get("timestamp_utc") and not p.get("timestamp_quality"):
        p["timestamp_quality"] = "synced"
    skip = {"schema_version", "chamber_id", "experiment_id", "source", "quality_flags", "device_id",
            "firmware_version", "boot_id", "sequence_number", "diagnostics", "field_provenance",
            "timestamp_quality", "timestamp_utc", "fault_codes", "last_command_id",
            "last_sequence_number", "uptime_s", "device_uptime_ms"}
    p["field_provenance"] = {k: "replay" for k, v in p.items() if k not in skip and v is not None}
    return {k: v for k, v in p.items() if k in ALLOWED}


async def replay(path: Path, nets: dict[str, DeviceHttpClient], *, speed: float = 1.0,
                 default_chamber: str = "A") -> dict:
    recs = load_records(path)
    session = uuid.uuid4().hex[:10]
    stats = {"sent": 0, "failed": 0, "skipped": 0}
    prev_ts = None
    for rec in recs:
        payload = to_replay_payload(rec, default_chamber, session)
        net = nets.get(payload["chamber_id"])
        if net is None:
            stats["skipped"] += 1
            continue
        ts = parse_utc(payload.get("timestamp_utc"))
        if speed > 0 and ts is not None and prev_ts is not None:
            await asyncio.sleep(max(0.0, (ts - prev_ts).total_seconds()) / speed)
        prev_ts = ts or prev_ts
        res = await net.post_telemetry(payload)
        stats["sent" if res.ok else "failed"] += 1
    log.info("replay of %s finished: %s", path, stats)
    return stats
