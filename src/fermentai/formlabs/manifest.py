"""Print manifest: what was prepared, with which API/server, from which STLs — and that nothing
was printed.

``source`` is the single most important field:

* ``"preform_local_api"`` — every number came from a real PreFormServer through the official SDK;
* ``"mock — PreFormServer not available on this Mac"`` (or another ``mock — ...`` reason) — the
  numbers are a *replay* of a recorded live session and the .form/.png files are placeholders.
"""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .prepare import REPO_ROOT, ChamberInput, PrintProfile, sha256_file

SCHEMA_VERSION = "fermentai.formlabs.print_manifest/1"
SOURCE_LIVE = "preform_local_api"
SOURCE_MOCK = "mock — PreFormServer not available on this Mac"
PITCH_CLAIM = ("Prepared and validated with the Formlabs PreForm API; printing requires human "
               "confirmation.")
FOOD_CONTACT = ("Printed resin parts are display objects only. Standard resins (Grey/White V5) "
                "are not food-safe; never place the prints in contact with bread.")
REQUIRED_FIELDS = ("schema_version", "source", "provenance", "job_submitted", "generated_utc",
                   "inputs", "printer", "material", "layer_thickness_mm", "build", "api",
                   "preform_server", "outputs")


def rel(path: str | Path | None, root: Path = REPO_ROOT) -> str | None:
    if path is None:
        return None
    p = Path(path)
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p)


def file_record(path: str | Path, backend: str) -> dict:
    p = Path(path)
    rec: dict[str, Any] = {"path": rel(p), "exists": p.is_file()}
    if p.is_file():
        rec.update(bytes=p.stat().st_size, sha256=sha256_file(p))
    rec["kind"] = "placeholder (mock)" if backend == "mock" else "written by PreFormServer"
    return rec


def build_manifest(
    *,
    backend: str,
    profile: PrintProfile,
    chambers: list[ChamberInput],
    build: dict,
    per_chamber: dict[str, dict] | None,
    server: dict,
    api: dict,
    transcript_path: Path | None,
    server_log_path: Path | None,
    mock_reason: str | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Assemble the manifest dict (pure function; :func:`write_manifest` writes it)."""
    live = backend == "live"
    source = SOURCE_LIVE if live else (f"mock — {mock_reason}" if mock_reason else SOURCE_MOCK)
    ss = build.get("scene_settings", {})
    meta = build.get("metadata", {})

    def outputs_of(b: dict) -> dict:
        return {k: file_record(v, backend) for k, v in (b.get("outputs") or {}).items()}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "provenance": "estimated" if live else "replay",
        "provenance_note": (
            "Print time and resin volume are PreForm's own estimates for the prepared scene "
            "(real API output, not a measurement of a finished print)." if live else
            "REPLAY of a recorded live session by MockPreFormServer: numbers are the recorded "
            "ones, the .form/.png outputs are placeholders. Not evidence of a new PreForm run."),
        "job_submitted": False,
        "print_submission": {
            "submitted": False,
            "policy": "Never automated. scripts/prepare_print.py submit needs --i-confirm-print "
                      "AND FERMENTAI_FORMLABS_CONFIRM_PRINT=yes, and even then only uploads to "
                      "the printer queue (print_now=False); a person starts the print.",
            "pitch_claim": PITCH_CLAIM,
        },
        "food_contact": FOOD_CONTACT,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "machine": platform.machine()},
        "inputs": [{
            "chamber": c.name,
            "model_name": c.model_name,
            "stl": rel(c.stl),
            "stl_sha256": c.sha256_actual,
            "stl_sha256_chain_summary": c.sha256_expected,
            "sha256_matches_chain_summary": c.sha256_matches,
            "validation_json": rel(c.validation_json),
            "mesh_validation_passed": c.validation_passed,
            "chamber_temperature_c": c.temperature_c,
            "geometry_provenance": "simulated (data/stl/chain_summary.json)",
        } for c in chambers],
        "printer": {"label": profile.printer_label, "machine_type": ss.get("machine_type"),
                    "build_volume_mm": (build.get("scene") or {}).get("build_volume")},
        "material": {"label": meta.get("MaterialName") or profile.material_label,
                     "code": ss.get("material_code"),
                     "required_tank": meta.get("RequiredTankName")},
        "layer_thickness_mm": ss.get("layer_thickness_mm"),
        "print_setting": ss.get("print_setting"),
        "profile": {"key": profile.key, "rationale": profile.rationale},
        "orientation": {
            "policy": build.get("orientation_policy"),
            "rationale": "Open crumb face up (model +Z = scene +Z): the flat solid floor faces "
                         "the build platform, so raft and supports touch only the floor; the "
                         "crumb face stays support-free and its craters open away from the "
                         "platform. PreForm's cup check still flags a few pockets under "
                         "overhanging pore lips (see printability_warnings). PreForm's own "
                         "auto-orient suggestion (~9-12 deg tilt) is recorded for comparison.",
            "preform_auto_orient_suggestion": {
                k: {"orientation_euler_deg": v["orientation_euler_deg"],
                    "open_face_up": v["open_face_up"], "height_mm": v["height_mm"]}
                for k, v in (build.get("preform_auto_orient_suggestion") or {}).items()},
            "final": {k: {"orientation_euler_deg": v["orientation_euler_deg"],
                          "open_face_up": v["open_face_up"], "height_mm": v["height_mm"],
                          "has_supports": v["has_supports"], "in_bounds": v["in_bounds"]}
                      for k, v in (build.get("models_final") or {}).items()},
        },
        "supports": build.get("support_parameters"),
        "layout": build.get("layout"),
        "printability_checks": build.get("printability_checks"),
        "printability_warnings": build.get("warnings", []),
        "build": {
            "name": build.get("build_name"),
            "chambers": build.get("chambers"),
            "estimates": build.get("estimates"),
            "print_time_breakdown_s": build.get("print_time"),
            "per_model": meta.get("models"),
            "outputs": outputs_of(build),
            "form_roundtrip": build.get("form_roundtrip"),
            "steps": build.get("steps"),
        },
        "per_chamber_builds": {
            name: {"estimates": b.get("estimates"), "outputs": outputs_of(b),
                   "printability_checks": b.get("printability_checks"),
                   "printability_warnings": b.get("warnings", []),
                   "form_roundtrip": b.get("form_roundtrip")}
            for name, b in (per_chamber or {}).items()
        },
        "api": api,
        "preform_server": server,
        "outputs": {
            "transcript": rel(transcript_path),
            "server_log": rel(server_log_path),
        },
        "notes": notes or [],
    }
    return manifest


def check_manifest(m: dict) -> list[str]:
    """Return a list of provenance problems (empty == OK)."""
    problems = [f"missing field {f}" for f in REQUIRED_FIELDS if f not in m]
    if m.get("job_submitted") is not False:
        problems.append("job_submitted must be False")
    src = m.get("source", "")
    if src != SOURCE_LIVE and not src.startswith("mock"):
        problems.append(f"unknown source {src!r}")
    if src == SOURCE_LIVE and m.get("provenance") != "estimated":
        problems.append("live manifest must carry provenance 'estimated'")
    if src.startswith("mock") and m.get("provenance") != "replay":
        problems.append("mock manifest must carry provenance 'replay'")
    for inp in m.get("inputs", []):
        if not inp.get("stl_sha256"):
            problems.append(f"input {inp.get('chamber')} has no STL hash")
    return problems


def write_manifest(path: Path, manifest: dict) -> Path:
    problems = check_manifest(manifest)
    if problems:
        raise ValueError(f"refusing to write an inconsistent manifest: {problems}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    return path
