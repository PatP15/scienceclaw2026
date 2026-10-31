"""All /api/v1 endpoints. See firmware_contract/README_FOR_ESP32_AGENT.md for the device subset."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from . import datafiles
from .auth import Principal, require_device, require_operator
from .config import Settings
from .db import dumps, iso, loads_cols, parse_iso
from .devices import ControlService, agent_key
from .events import EVENT_TYPES, event_stream
from .feedback import apply_schedules, jsonable, pipeline_available, run_feedback, store_experiment
from .schemas import (
    SCHEMA_VERSION,
    ControlAckIn,
    ControlAckOut,
    DesiredControl,
    ExperimentIn,
    ImageOut,
    MotorCommandIn,
    SetpointIn,
    SetpointResult,
    SimulationRunIn,
    TasteFeedbackIn,
    TelemetryAck,
    TelemetryIn,
)

router = APIRouter(prefix="/api/v1")
AUTH = [Depends(require_operator)]          # dashboard / agents / operator writes
DEVICE = Depends(require_device)             # ESP32 endpoints (device token or operator token)


def svc(request: Request) -> ControlService:
    return request.app.state.service


def settings(request: Request) -> Settings:
    return request.app.state.settings


def simulation_available() -> bool:
    try:
        from fermentai.fermentation.simulation import simulate  # noqa: F401
    except ImportError:
        return False
    return True


# ============================================================================ health
@router.get("/health", tags=["meta"])
def health(request: Request):
    s, c = settings(request), svc(request)
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "server_time_utc": iso(c.clock()),
        "server_epoch": c.db.meta("server_epoch"),
        "chambers": c.chamber_ids,
        "db_path": str(s.db_path),
        "auth": "bearer token on writes + desired-control; reads open on LAN",
        "default_token_in_use": s.token_is_default,
        "pipeline_available": pipeline_available(),
        "simulation_available": simulation_available(),
        "dashboard_static": request.app.state.dashboard_static,
        "llm_mode": s.llm_mode,
    }


# ============================================================================ device endpoints
@router.post("/telemetry", response_model=TelemetryAck, tags=["device"])
def post_telemetry(t: TelemetryIn, request: Request, principal: Principal = DEVICE):
    return svc(request).ingest_telemetry(t, principal)


@router.get("/chambers/{chamber_id}/desired-control", response_model=DesiredControl,
            tags=["device"])
def desired_control(chamber_id: str, request: Request, principal: Principal = DEVICE):
    c = svc(request)
    c.authorize(principal, chamber_id)
    return c.desired_control(chamber_id)


@router.post("/control-ack", response_model=ControlAckOut, tags=["device"])
def control_ack(ack: ControlAckIn, request: Request, principal: Principal = DEVICE):
    return svc(request).record_ack(ack, principal)


_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


@router.post("/images", response_model=ImageOut, tags=["device"])
async def post_image(request: Request, principal: Principal = DEVICE):
    """Either multipart/form-data (file, chamber_id, timestamp_utc, source[, experiment_id])
    or a raw `Content-Type: image/jpeg` body with the same fields as query parameters."""
    c, s = svc(request), settings(request)
    ctype = request.headers.get("content-type", "")
    max_bytes = s.hardware.images.max_bytes
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        upload = form.get("file") or form.get("image")
        if upload is None or isinstance(upload, str):
            raise HTTPException(422, "multipart field 'file' (JPEG) is required")
        body = await upload.read(max_bytes + 1)
    elif ctype.startswith("image/jpeg"):
        fields = dict(request.query_params)
        body = await request.body()
    else:
        raise HTTPException(415, "send multipart/form-data or Content-Type: image/jpeg")
    if len(body) > max_bytes:
        raise HTTPException(413, f"image exceeds {max_bytes} bytes")
    if not body.startswith(b"\xff\xd8"):
        raise HTTPException(422, "file is not a JPEG (missing FF D8 header)")
    chamber_id = fields.get("chamber_id") or ""
    c.authorize(principal, chamber_id, fields.get("device_id"))
    source = fields.get("source")
    if source not in ("measured", "real", "simulated", "replay"):
        raise HTTPException(422, "field 'source' must be one of measured | simulated | replay")
    source = "measured" if source == "real" else source
    now = c.clock()
    ts_raw = fields.get("timestamp_utc") or fields.get("timestamp")
    try:
        ts = parse_iso(ts_raw) if ts_raw else now
    except ValueError:
        raise HTTPException(422, f"bad timestamp_utc {ts_raw!r}; use ISO-8601 e.g. 2026-10-31T12:00:00Z")
    sha = hashlib.sha256(body).hexdigest()
    rel = Path("images") / chamber_id / f"{ts:%Y%m%dT%H%M%SZ}_{sha[:10]}.jpg"
    dest = s.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    exp = fields.get("experiment_id")
    iid = c.db.execute(
        "INSERT INTO images(chamber_id, timestamp_utc, received_at_utc, rel_path, bytes, sha256,"
        " experiment_id, source) VALUES (?,?,?,?,?,?,?,?)",
        (chamber_id, iso(ts), iso(now), rel.as_posix(), len(body), sha,
         _SAFE.sub("_", exp)[:64] if exp else None, source),
    )
    out = {"ok": True, "image_id": iid, "chamber_id": chamber_id, "timestamp_utc": iso(ts),
           "url": f"/api/v1/images/{iid}", "bytes": len(body), "sha256": sha}
    c.db.emit("image", {**out, "source": source}, chamber_id)
    return out


@router.get("/images", tags=["data"])
def list_images(request: Request, chamber_id: str | None = None, limit: int = Query(50, le=1000)):
    sql, params = "SELECT * FROM images", []
    if chamber_id:
        sql += " WHERE chamber_id=?"
        params.append(chamber_id)
    rows = svc(request).db.query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    return [{**r, "url": f"/api/v1/images/{r['id']}"} for r in rows]


@router.get("/images/{image_id}", tags=["data"])
def get_image(image_id: int, request: Request):
    row = svc(request).db.query_one("SELECT * FROM images WHERE id=?", (image_id,))
    if not row:
        raise HTTPException(404, "image not found")
    p = settings(request).data_dir / row["rel_path"]
    if not p.is_file():
        raise HTTPException(404, "image file missing on disk")
    return FileResponse(p, media_type="image/jpeg")


# ============================================================================ chambers
@router.get("/chambers", tags=["chambers"])
def chambers(request: Request):
    c = svc(request)
    return {cid: c.chamber_state(cid) for cid in c.chamber_ids}


@router.get("/chambers/{chamber_id}/state", tags=["chambers"])
def chamber_state(chamber_id: str, request: Request):
    return svc(request).chamber_state(chamber_id)


@router.get("/chambers/{chamber_id}/telemetry", tags=["chambers"])
def chamber_telemetry(chamber_id: str, request: Request, limit: int = Query(500, le=20000),
                      since_id: int = 0):
    c = svc(request)
    c.require_chamber(chamber_id)
    return c.telemetry_history(chamber_id, limit, since_id)


@router.post("/chambers/{chamber_id}/setpoint", response_model=SetpointResult, dependencies=AUTH,
             tags=["chambers"])
def post_setpoint(chamber_id: str, body: SetpointIn, request: Request):
    """Operator/agent setpoint request -> clamped, step-limited, rate-limited.
    Optional control_mode (auto|hold|idle) is applied first; it never changes the value."""
    c = svc(request)
    if body.control_mode is not None:
        c.set_control_mode(chamber_id, body.control_mode, body.source, body.rationale)
    return c.set_desired_setpoint(chamber_id, body.setpoint_c, body.source, body.rationale)


@router.get("/chambers/{chamber_id}/setpoint-history", tags=["chambers"])
def setpoint_history(chamber_id: str, request: Request, limit: int = Query(200, le=5000)):
    c = svc(request)
    c.require_chamber(chamber_id)
    return c.db.query("SELECT * FROM setpoint_changes WHERE chamber_id=? ORDER BY id DESC LIMIT ?",
                      (chamber_id, limit))


@router.post("/chambers/{chamber_id}/motor", dependencies=AUTH, tags=["chambers"])
def post_motor(chamber_id: str, body: MotorCommandIn, request: Request):
    """Bounded chef-hat animation. Separate command type; cannot carry setpoint/Peltier fields."""
    duration_s = body.duration_ms / 1000.0 if body.duration_ms is not None else body.duration_s
    return svc(request).issue_motor_command(chamber_id, duration_s, body.pattern, body.reason,
                                            body.source)


@router.post("/chambers/{chamber_id}/capture-image", dependencies=AUTH, tags=["chambers"])
def post_capture_image(chamber_id: str, request: Request):
    """v1.1 capture_image command (device uploads a fresh JPEG via POST /images and acks)."""
    return svc(request).issue_capture_command(chamber_id, "dashboard", "operator capture request")


@router.get("/chambers/{chamber_id}/readings", tags=["chambers"])
def chamber_readings(chamber_id: str, request: Request, field: str | None = None,
                     limit: int = Query(500, le=20000)):
    """Per-field sensor readings with unit, provenance and quality (v1.1)."""
    return svc(request).readings(chamber_id, field, limit)


@router.get("/devices", tags=["chambers"])
def devices(request: Request, chamber_id: str | None = None):
    """Device registry: device_id -> bound chamber, last boot/sequence/uptime."""
    return svc(request).list_devices(chamber_id)


# ============================================================================ commands
@router.get("/commands", tags=["commands"])
def list_commands(request: Request, chamber_id: str | None = None, command_type: str | None = None,
                  action: str | None = None,
                  status: str | None = None, limit: int = Query(100, le=5000)):
    return svc(request).list_commands(chamber_id, command_type, status, limit, action)


@router.delete("/commands/{command_id}", dependencies=AUTH, tags=["commands"])
def cancel_command(command_id: str, request: Request):
    return svc(request).cancel_command(command_id)


# ============================================================================ experiments
def _experiment_row(r: dict) -> dict:
    return loads_cols(r, "schedules_json", "payload_json")


@router.post("/experiments", dependencies=AUTH, tags=["experiments"])
def post_experiment(body: ExperimentIn, request: Request):
    c = svc(request)
    t = c.hw.temperature
    warnings = []
    for cid, steps in body.schedules.items():
        if cid not in c.hw.chambers:
            warnings.append(f"schedule for unknown chamber {cid!r}")
        for st in steps:
            if not (t.min_setpoint_c <= st.setpoint_c <= t.max_setpoint_c):
                warnings.append(f"{cid} t_h={st.t_h}: {st.setpoint_c} C outside limits; "
                                "will be clamped if applied")
    exp = body.model_dump(mode="json")
    rec = store_experiment(c, exp, proposed_by=body.proposed_by, provenance=body.provenance,
                           status=body.status)
    return {**rec, "warnings": warnings}


@router.get("/experiments", tags=["experiments"])
def list_experiments(request: Request, limit: int = Query(100, le=1000)):
    rows = svc(request).db.query("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,))
    return [_experiment_row(r) for r in rows]


@router.get("/experiments/{experiment_id}", tags=["experiments"])
def get_experiment(experiment_id: str, request: Request):
    r = svc(request).db.query_one("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,))
    if not r:
        raise HTTPException(404, "experiment not found")
    return _experiment_row(r)


@router.post("/experiments/{experiment_id}/apply", dependencies=AUTH, tags=["experiments"])
def apply_experiment(experiment_id: str, request: Request):
    """Operator confirmation: push the first step of each schedule via the clamped setpoint path."""
    c = svc(request)
    r = c.db.query_one("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,))
    if not r:
        raise HTTPException(404, "experiment not found")
    r = _experiment_row(r)
    applied = apply_schedules(c, r["schedules"], "operator_apply_experiment",
                              f"apply experiment {experiment_id}: {r.get('hypothesis') or r['title']}",
                              experiment_id)
    c.db.execute("UPDATE experiments SET status='running' WHERE experiment_id=?", (experiment_id,))
    c.db.emit("experiment", {"experiment_id": experiment_id, "status": "running", "applied": applied})
    return {"experiment_id": experiment_id, "status": "running", "applied_setpoints": applied}


# ============================================================================ simulations
@router.post("/simulations/run", dependencies=AUTH, tags=["simulations"])
def run_simulation(body: SimulationRunIn, request: Request):
    """Run fermentai.fermentation.simulation.simulate() on a schedule (default: the chamber's
    current setpoint held constant). Output is MODEL output, labelled provenance=simulated.

    Body: {chamber_id?, label?, schedule?: [{t_h, setpoint_c}], duration_h? (default 16),
           options?: {mode?: piecewise_constant|piecewise_linear, params?, dt_h?, initial?}}"""
    try:
        from fermentai.fermentation import simulation as sim
    except ImportError as e:
        raise HTTPException(503, f"fermentation simulator not available ({e}); "
                                 "cached predictions are under GET /api/v1/predictions")
    c = svc(request)
    opts = dict(body.options)
    if body.schedule:
        bps = [[st.t_h, st.setpoint_c] for st in sorted(body.schedule, key=lambda s: s.t_h)]
    else:
        cid = body.chamber_id or c.chamber_ids[0]
        bps = [[0.0, c.current_control(cid)["setpoint_c"]]]
    candidates = {
        "schedule": {"breakpoints": bps, "mode": opts.pop("mode", "piecewise_constant")},
        "t_end_h": body.duration_h or 16.0,
        **{k: opts[k] for k in ("params", "dt_h", "initial") if k in opts},
    }
    params = inspect.signature(sim.simulate).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = candidates
    else:
        kwargs = {k: v for k, v in candidates.items() if k in params}
    req = body.model_dump(mode="json", exclude_none=True)
    now = iso(c.clock())
    try:
        out = sim.simulate(**kwargs)
        summary = None
        if hasattr(sim, "summarize_prediction") and hasattr(out, "columns"):
            try:
                summary = jsonable(sim.summarize_prediction(out))
            except Exception as e:  # summary is optional
                summary = {"error": f"summary failed: {e}"}
        result = {"summary": summary, "timeseries": jsonable(out),
                  "attrs": jsonable(getattr(out, "attrs", {}))}
        status = "ok"
    except Exception as e:  # report, don't crash the server
        result, status = {"error": f"{type(e).__name__}: {e}"}, "error"
    sid = c.db.execute(
        "INSERT INTO simulations(created_at_utc, status, request_json, result_json) VALUES (?,?,?,?)",
        (now, status, dumps({"request": req, "kwargs": jsonable(kwargs)}), dumps(result)),
    )
    c.db.emit("simulation", {"simulation_id": sid, "status": status, "label": body.label,
                             "chamber_id": body.chamber_id, "provenance": "simulated",
                             "summary": result.get("summary")})
    if status == "error":
        raise HTTPException(422, {"simulation_id": sid, **result})
    return {"schema_version": SCHEMA_VERSION, "simulation_id": sid, "status": status,
            "provenance": "simulated", "schedule": candidates["schedule"], "result": result}


@router.get("/simulations", tags=["simulations"])
def list_simulations(request: Request, limit: int = Query(20, le=500)):
    rows = svc(request).db.query(
        "SELECT id, created_at_utc, status, request_json FROM simulations ORDER BY id DESC LIMIT ?",
        (limit,))
    return [loads_cols(r, "request_json") for r in rows]


@router.get("/simulations/{simulation_id}", tags=["simulations"])
def get_simulation(simulation_id: int, request: Request):
    """Stored run in the simulation_result_v1 shape (+ request, created_at_utc)."""
    r = svc(request).db.query_one("SELECT * FROM simulations WHERE id=?", (simulation_id,))
    if not r:
        raise HTTPException(404, "simulation not found")
    r = loads_cols(r, "request_json", "result_json")
    req = r.get("request") or {}
    return {"schema_version": SCHEMA_VERSION, "simulation_id": r["id"], "status": r["status"],
            "provenance": "simulated", "created_at_utc": r["created_at_utc"],
            "schedule": (req.get("kwargs") or {}).get("schedule"), "result": r.get("result"),
            "request": req.get("request")}


# ============================================================================ taste feedback
@router.post("/taste-feedback", dependencies=AUTH, tags=["feedback"])
def taste_feedback(body: TasteFeedbackIn, request: Request):
    return run_feedback(svc(request), settings(request), body)


@router.get("/taste-feedback", tags=["feedback"])
def list_feedback(request: Request, limit: int = Query(20, le=500)):
    rows = svc(request).db.query("SELECT * FROM taste_feedback ORDER BY id DESC LIMIT ?", (limit,))
    return [loads_cols(r, "result_json") for r in rows]


# ============================================================================ agents
@router.get("/agents/{agent_id}/messages", tags=["agents"])
def agent_messages(agent_id: str, request: Request, limit: int = Query(100, le=2000),
                   since_id: int = 0):
    """Messages sent by or to the agent. agent_id: A | B | scientist_A | ... | all."""
    db = svc(request).db
    if agent_id.lower() == "all":
        rows = db.query("SELECT * FROM agent_messages WHERE id>? ORDER BY id DESC LIMIT ?",
                        (since_id, limit))
    else:
        key = agent_key(agent_id)
        rows = db.query(
            "SELECT * FROM agent_messages WHERE id>? AND (sender_key=? OR recipient_key=?"
            " OR recipient_key IN ('all','both','broadcast')) ORDER BY id DESC LIMIT ?",
            (since_id, key, key, limit))
    return [loads_cols(r, "content_json") for r in reversed(rows)]


@router.get("/agents/{agent_id}/state", tags=["agents"])
def agent_state(agent_id: str, request: Request):
    """Calls fermentai.agents.state.get_agent_state(agent_id) when available; otherwise a
    clearly-labelled stub derived from the API's own records."""
    try:
        from fermentai.agents.state import get_agent_state
    except ImportError as e:
        get_agent_state, why = None, f"fermentai.agents.state not importable ({e})"
    if get_agent_state is not None:
        try:
            state = jsonable(get_agent_state(agent_id))
            if isinstance(state, dict):
                state.setdefault("available", True)
                state.setdefault("agent_id", agent_id)
                return state
            return {"agent_id": agent_id, "available": True, "state": state}
        except Exception as e:  # never break the dashboard on an agents-side error
            why = f"get_agent_state failed: {type(e).__name__}: {e}"
    c = svc(request)
    key = agent_key(agent_id)
    chamber = next((cid for cid in c.chamber_ids if cid.lower() == key), None)
    last = c.db.query_one(
        "SELECT * FROM agent_messages WHERE sender_key=? ORDER BY id DESC LIMIT 1", (key,))
    hyp = c.db.query_one(
        "SELECT * FROM scientist_hypotheses WHERE agent_id=? ORDER BY id DESC LIMIT 1", (agent_id,))
    n = c.db.query_one("SELECT COUNT(*) AS n FROM agent_messages WHERE sender_key=?", (key,))["n"]
    ctl = c.current_control(chamber) if chamber else None
    return {
        "agent_id": agent_id,
        "available": False,
        "stub": True,
        "reason": why,
        "chamber_id": chamber,
        "messages_sent": n,
        "latest_message": loads_cols(last, "content_json"),
        "latest_hypothesis": loads_cols(hyp, "payload_json"),
        "current_setpoint_c": ctl["setpoint_c"] if ctl else None,
        "experiment_id": ctl.get("experiment_id") if ctl else None,
        "provenance": "derived from API records (stub)",
    }


@router.get("/demos", tags=["data"])
def demos(request: Request):
    """Scripted/replay demo scenarios under data/demos (labelled replay by their authors)."""
    root = settings(request).data_dir / "demos"
    out = []
    for f in datafiles.list_files(root, "/api/v1/demos", {".json"}):
        try:
            d = json.loads((root / f["path"]).read_text())
            meta = d if isinstance(d, dict) else {}
            f.update({k: meta.get(k) for k in ("title", "description", "label", "provenance", "mode")
                      if k in meta})
        except (OSError, json.JSONDecodeError) as e:
            f["error"] = f"unreadable: {e}"
        out.append(f)
    return {"root": "data/demos", "files": out}


@router.get("/demos/{path:path}", tags=["data"])
def demo_file(path: str, request: Request):
    if not path.lower().endswith(".json"):
        raise HTTPException(415, "only .json demo files are served")
    return datafiles.serve(settings(request).data_dir / "demos", path)


# ============================================================================ events
@router.get("/events", tags=["events"])
def events(request: Request, since: int = 0, limit: int = Query(100, le=1000),
           types: str | None = None):
    """JSON polling fallback for the SSE stream."""
    tl = [t for t in types.split(",") if t] if types else None
    return svc(request).db.events_since(since, limit, tl)


@router.get("/events/stream", tags=["events"])
async def events_stream(request: Request, since: int | None = None, types: str | None = None,
                        format: str = "named", max_events: int | None = None,
                        timeout_s: float | None = None):
    """SSE. By default only events after connection; `since=0` replays history.
    Honors the Last-Event-ID header on reconnect. Event names: see events.EVENT_TYPES."""
    db = svc(request).db
    if since is None:
        last = request.headers.get("last-event-id")
        since = int(last) if last and last.isdigit() else db.last_event_id()
    tl = [t for t in types.split(",") if t] if types else None
    gen = event_stream(request, db, since, types=tl, named=(format != "plain"),
                       max_events=max_events, timeout_s=timeout_s)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/events/types", tags=["events"])
def event_types():
    return list(EVENT_TYPES)


# ============================================================================ read-only data
@router.get("/predictions", tags=["data"])
def predictions(request: Request):
    root = settings(request).data_dir / "predictions"
    return {"root": "data/predictions", "provenance": "simulated",
            "files": datafiles.list_files(root, "/api/v1/predictions")}


@router.get("/predictions/{path:path}", tags=["data"])
def prediction_file(path: str, request: Request):
    return datafiles.serve(settings(request).data_dir / "predictions", path)


@router.get("/molecular/summary", tags=["data"])
def molecular_summary(request: Request):
    return datafiles.molecular_summary(settings(request).data_dir / "alchemi",
                                       "/api/v1/molecular/files")


@router.get("/molecular/files/{path:path}", tags=["data"])
def molecular_file(path: str, request: Request):
    return datafiles.serve(settings(request).data_dir / "alchemi" / "analysis", path)


@router.get("/stl", tags=["data"])
def stl_list(request: Request):
    return {"root": "data/stl",
            "files": datafiles.list_files(settings(request).data_dir / "stl", "/api/v1/stl")}


@router.get("/stl/{path:path}", tags=["data"])
def stl_file(path: str, request: Request):
    return datafiles.serve(settings(request).data_dir / "stl", path)


@router.get("/findings", tags=["agents"])
def findings(request: Request, limit: int = Query(200, le=5000)):
    s = settings(request)
    p = datafiles.find_findings(s.data_dir, s.findings_path)
    if p is None:
        return {"available": False, "reason": "no findings log found", "findings": []}
    try:
        items = datafiles.read_findings(p, limit)
    except (OSError, json.JSONDecodeError) as e:
        return {"available": False, "reason": f"findings log unreadable: {e}", "findings": []}
    return {"available": True, "path": str(p.relative_to(s.data_dir.parent))
            if p.is_relative_to(s.data_dir.parent) else str(p), "findings": items}

