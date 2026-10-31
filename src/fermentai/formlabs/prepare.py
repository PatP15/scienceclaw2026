"""Prepare the two crumb-chamber specimens for a Formlabs SLA print with the PreForm API.

Pipeline (every step is a real PreForm API call through the official ``formlabs-sdk``):

  validate print setting -> create scene -> import STLs -> auto-orient (recorded as PreForm's
  suggestion) -> enforce "open crumb face up" -> auto-support -> auto-layout -> printability
  checks -> scene material usage + full print-time calculation -> render previews -> save .form

The crumb face (the scientific surface: pore craters cut by the "bread knife") is the model's
+Z face at z = 12 mm; the solid floor is the flat z = 0 face. Orienting open-face-up puts the
flat floor toward the build platform, so supports and raft attach only to the floor (hidden when
displayed) and the crumb face is support-free. Craters open away from the platform; PreForm's
cup check may still flag a few pockets under overhanging pore lips, which are reported as
warnings rather than hidden.

Nothing here ever prints. :func:`submit_print` exists only to make the human gate explicit: it
needs ``--i-confirm-print`` *and* ``FERMENTAI_FORMLABS_CONFIRM_PRINT=yes``; otherwise it prints
a summary and returns a non-zero exit code.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAIN_SUMMARY = REPO_ROOT / "data" / "stl" / "chain_summary.json"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "stl" / "print"
FIXTURE = Path(__file__).with_name("fixtures") / "preform_3.64.2_session.json"

CONFIRM_ENV = "FERMENTAI_FORMLABS_CONFIRM_PRINT"
CONFIRM_VALUE = "yes"


@dataclass(frozen=True)
class PrintProfile:
    """One printer/material/layer-thickness choice from PreForm's print-setting catalog."""

    key: str
    printer_label: str
    machine_type: str
    material_label: str
    material_code: str
    layer_thickness_mm: float = 0.1
    print_setting: str = "DEFAULT"
    rationale: str = ""


PROFILES: dict[str, PrintProfile] = {
    "form4-grey-v5": PrintProfile(
        "form4-grey-v5", "Form 4", "FORM-4-0", "Grey V5", "FLGPGR05", 0.1, "DEFAULT",
        "Form 4 build platform (200 x 125 mm in PreForm's catalog) fits both 80 x 80 mm slabs "
        "flat, side by side, crumb face up. Grey V5 is Formlabs' standard matte grey resin: the "
        "best general-purpose resin for showing surface relief (pore craters) under display "
        "lighting and in photographs. 0.1 mm layers are the default speed/detail balance; the "
        "smallest designed feature (~1.2 mm walls) is >10 layers."),
    "form4-white-v5": PrintProfile(
        "form4-white-v5", "Form 4", "FORM-4-0", "White V5", "FLGPWH05", 0.1, "DEFAULT",
        "As form4-grey-v5 but White V5 (looks more like crumb; shows less shading detail)."),
    "form4l-grey-v5": PrintProfile(
        "form4l-grey-v5", "Form 4L", "FRML-4-0", "Grey V5", "FLGPGR05", 0.1, "DEFAULT",
        "Large-format alternative (353 x 196 mm platform)."),
}
DEFAULT_PROFILE = "form4-grey-v5"


@dataclass
class ChamberInput:
    name: str  # "A" / "B"
    stl: Path
    sha256_expected: str | None
    stl_bytes_expected: int | None
    validation_json: Path | None
    validation_passed: bool | None
    temperature_c: float | None = None
    sha256_actual: str | None = None

    @property
    def sha256_matches(self) -> bool | None:
        if self.sha256_expected is None or self.sha256_actual is None:
            return None
        return self.sha256_expected == self.sha256_actual

    @property
    def model_name(self) -> str:
        return f"crumb_chamber_{self.name}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_chamber_inputs(chain_summary: Path = CHAIN_SUMMARY,
                        names: tuple[str, ...] = ("A", "B")) -> list[ChamberInput]:
    """Read the STL provenance written by the geometry chain and hash the STL files on disk."""
    chain = json.loads(Path(chain_summary).read_text())
    root = Path(chain_summary).resolve().parents[2]
    out = []
    for n in names:
        c = chain["chambers"][n]
        stl = (root / c["stl"]).resolve()
        vj = (root / c["validation_json"]).resolve() if c.get("validation_json") else None
        ci = ChamberInput(name=n, stl=stl, sha256_expected=c.get("stl_sha256"),
                          stl_bytes_expected=c.get("stl_bytes"), validation_json=vj,
                          validation_passed=c.get("validation_passed"),
                          temperature_c=c.get("temperature_c"))
        if stl.is_file():
            ci.sha256_actual = sha256_file(stl)
        out.append(ci)
    return out


def _d(obj: Any) -> Any:
    """SDK response -> plain JSON-able data."""
    if obj is None:
        return None
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, (list, tuple)):
        return [_d(x) for x in obj]
    return obj


def _per_model(resp: Any) -> dict:
    d = _d(resp) or {}
    return d.get("per_model_results", d)


def _norm_angle(a: float) -> float:
    a = math.fmod(a, 360.0)
    return a - 360.0 if a > 180.0 else (a + 360.0 if a < -180.0 else a)


def _face_up(orientation: dict, tol_deg: float = 0.5) -> bool:
    """True when the model's own +Z still points up (rotation only about the scene Z axis)."""
    return (abs(_norm_angle(orientation.get("x", 0.0))) < tol_deg
            and abs(_norm_angle(orientation.get("y", 0.0))) < tol_deg)


@dataclass
class StepLog:
    steps: list[dict] = field(default_factory=list)
    echo: Callable[[str], None] | None = None

    def run(self, name: str, endpoint: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        result = fn()
        dt = round(time.perf_counter() - t0, 3)
        self.steps.append({"step": name, "endpoint": endpoint, "duration_s": dt})
        if self.echo:
            self.echo(f"  [{dt:7.2f} s] {endpoint:<44} {name}")
        return result


def prepare_build(
    api: Any,
    profile: PrintProfile,
    chambers: list[ChamberInput],
    out_dir: Path,
    build_name: str,
    *,
    orientation: str = "open_face_up",
    model_spacing_mm: float = 5.0,
    thin_feature_threshold_mm: float = 0.5,
    views: tuple[str, ...] = ("iso", "top"),
    echo: Callable[[str], None] | None = None,
) -> dict:
    """Run the full PreForm preparation for ``chambers`` on one build platform.

    Returns a JSON-able dict with every API result needed for the manifest. Raises if the server
    reports a different printer/material than requested or a model fails a hard check.
    """
    from formlabs_preform_api.models import (
        DirectionVectors,
        ImportUnits,
        RaftType,
        RenderViewType,
        RepairBehavior,
    )

    if orientation not in ("open_face_up", "preform_auto"):
        raise ValueError(f"unknown orientation policy {orientation!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = StepLog(echo=echo)
    r: dict[str, Any] = {"build_name": build_name, "chambers": [c.name for c in chambers]}

    r["print_setting_validation"] = _d(log.run(
        "check printer/material/layer combination against PreForm's catalog",
        "POST /v1/print-setting/validate/",
        lambda: api.validate_print_setting(
            machine_type=profile.machine_type, material_code=profile.material_code,
            print_setting=profile.print_setting, layer_thickness_mm=profile.layer_thickness_mm)))
    if r["print_setting_validation"].get("valid") is False:
        raise RuntimeError(f"PreForm rejected print setting {profile}: "
                           f"{r['print_setting_validation']}")

    created = _d(log.run("create scene (Job Setup: printer, material, layer)",
                         "POST /v1/scene/create/",
                         lambda: api.create_scene(machine_type=profile.machine_type,
                                                  material_code=profile.material_code,
                                                  layer_thickness_mm=profile.layer_thickness_mm,
                                                  print_setting=profile.print_setting)))
    scene_id = created["scene_id"]
    ss = created.get("scene_settings", {})
    if (ss.get("machine_type"), ss.get("material_code")) != (profile.machine_type,
                                                             profile.material_code):
        raise RuntimeError(f"server scene settings {ss} != requested profile {profile.key} "
                           "(a mock fixture only replays the profile it was recorded with)")
    r["scene_id"] = scene_id
    r["scene_settings"] = ss

    model_ids: dict[str, str] = {}
    for c in chambers:
        imp = _d(log.run(f"import {c.stl.name} (mm, refuse silent mesh repair)",
                         "POST /v1/scene/model/import/",
                         lambda c=c: api.import_models(
                             scene_id=scene_id, file=str(c.stl), name=c.model_name,
                             units=ImportUnits.MILLIMETERS,
                             repair_behavior=RepairBehavior.ERROR)))
        model_ids[c.name] = imp["new_models"][0]["model_id"]
    r["model_ids"] = model_ids
    by_id = {v: k for k, v in model_ids.items()}

    def models_snapshot(label: str) -> dict:
        lm = _d(log.run(f"list models ({label})", "POST /v1/scene/model/list/",
                        lambda: api.list_models(scene_id=scene_id)))
        snap = {}
        for m in lm["models"]:
            bb = m["bounding_box"]
            snap[by_id.get(m["id"], m["id"])] = {
                "orientation_euler_deg": m["orientation"],
                "position_mm": m["position"],
                "bbox_min_mm": bb["min_corner"], "bbox_max_mm": bb["max_corner"],
                "height_mm": round(bb["max_corner"]["z"] - bb["min_corner"]["z"], 3),
                "has_supports": m["has_supports"], "in_bounds": m["in_bounds"],
                "open_face_up": _face_up(m["orientation"]),
                "raw_mesh_hash": m.get("raw_mesh_hash"),
            }
        return snap

    r["models_imported"] = models_snapshot("as imported")
    log.run("auto-orient (PreForm's suggestion, recorded)", "POST /v1/scene/model/auto-orient/",
            lambda: api.auto_orient_models(scene_id=scene_id))
    r["preform_auto_orient_suggestion"] = models_snapshot("after auto-orient")
    if orientation == "open_face_up":
        log.run("enforce open crumb face up (model +Z -> scene +Z)",
                "POST /v1/scene/model/orient/",
                lambda: api.orient_models(scene_id=scene_id, orientation=DirectionVectors(
                    z_direction=[0.0, 0.0, 1.0], x_direction=[1.0, 0.0, 0.0])))
    r["orientation_policy"] = orientation

    support_params = {"raft_type": "FULL_RAFT"}
    log.run("auto-generate supports (raft + supports under the flat floor only)",
            "POST /v1/scene/model/auto-support/",
            lambda: api.auto_support_models(scene_id=scene_id, raft_type=RaftType.FULL_RAFT))
    r["support_parameters"] = {**support_params,
                               "other": "PreForm defaults for Grey V5 / 0.1 mm (density, "
                                        "touchpoint size, slope multiplier)"}
    layout = _d(log.run("auto-layout on one build platform", "POST /v1/scene/model/auto-layout/",
                        lambda: api.auto_layout_models(scene_id=scene_id,
                                                       model_spacing_mm=model_spacing_mm)))
    r["layout"] = {"model_spacing_mm": model_spacing_mm,
                   "laid_out": [by_id.get(i, i) for i in layout.get("laid_out_model_ids", [])],
                   "ejected": [by_id.get(i, i) for i in layout.get("ejected_model_ids", [])]}
    if r["layout"]["ejected"]:
        raise RuntimeError(f"PreForm could not fit {r['layout']['ejected']} on one platform")
    r["models_final"] = models_snapshot("final")

    checks: dict[str, Any] = {}
    checks["in_bounds"] = _d(log.run("printability: fits build volume",
                                     "POST /v1/scene/model/validate/in-bounds/",
                                     lambda: api.validate_in_bounds(scene_id=scene_id)))
    checks["manifoldness"] = _per_model(log.run(
        "printability: closed solid", "POST /v1/scene/model/validate/manifoldness/",
        lambda: api.validate_manifoldness(scene_id=scene_id)))
    checks["supportedness"] = _per_model(log.run(
        "printability: undersupported area", "POST /v1/scene/model/validate/supportedness/",
        lambda: api.validate_supportedness(scene_id=scene_id)))
    checks["minima"] = _per_model(log.run(
        "printability: unsupported minima", "POST /v1/scene/model/validate/minima/",
        lambda: api.validate_minima(scene_id=scene_id)))
    checks["cups"] = _per_model(log.run(
        "printability: cups (suction / trapped resin)", "POST /v1/scene/model/validate/cups/",
        lambda: api.validate_cups(scene_id=scene_id)))
    checks["interferences"] = _d(log.run(
        "printability: model/support collisions", "POST /v1/scene/model/validate/interferences/",
        lambda: api.validate_interferences(scene_id=scene_id)))
    checks["thin_features"] = _per_model(log.run(
        f"printability: walls thinner than {thin_feature_threshold_mm} mm",
        "POST /v1/scene/model/validate/thin-features/",
        lambda: api.validate_thin_features(scene_id=scene_id,
                                           threshold_mm=thin_feature_threshold_mm)))
    checks["thin_feature_threshold_mm"] = thin_feature_threshold_mm
    r["printability_checks"] = {k: _rekey(v, by_id) for k, v in checks.items()}
    r["warnings"] = _warnings(r["printability_checks"])

    scene = _d(log.run("scene: material usage + layer count", "POST /v1/scene/get/",
                       lambda: api.get_scene(scene_id=scene_id)))
    times = _d(log.run("full print-time calculation (slices internally)",
                       "POST /v1/scene/calculate-print-time/",
                       lambda: api.calculate_print_time(scene_id=scene_id)))
    meta = _d(log.run("print-job metadata (per-model / raft volumes)", "POST /v1/scene/metadata/",
                      lambda: api.get_scene_metadata(scene_id=scene_id)))
    r["scene"] = {k: scene.get(k) for k in ("material_usage", "layer_count", "build_volume",
                                            "scene_settings")}
    r["print_time"] = times
    r["metadata"] = _metadata_subset(meta, by_id)
    r["estimates"] = _estimates(scene, times, meta)

    outputs: dict[str, str] = {}
    cameras = {"iso": {"yaw": 45.0, "pitch": 35.264}, "top": {"yaw": 0.0, "pitch": 90.0}}
    for view in views:
        kw = cameras[view]
        png = out_dir / f"{build_name}_{view}.png"
        log.run(f"render preview ({view})", "POST /v1/scene/render/",
                lambda png=png, kw=kw: api.render_scene(
                    scene_id=scene_id, file=str(png), image_size_px=1200,
                    view_type=RenderViewType.ZOOM_ON_MODELS, **kw))
        outputs[f"preview_{view}"] = str(png)
    form = out_dir / f"{build_name}.form"
    log.run("save prepared scene as .form (for a human to open in PreForm)",
            "POST /v1/scene/save/",
            lambda: api.save_form_file(file=str(form), scene_ids=[scene_id],
                                       project_name=build_name))
    outputs["form"] = str(form)
    reopened = _d(log.run("re-open the saved .form to verify it loads", "POST /v1/scene/open/",
                          lambda: api.open_form_file(file=str(form))))
    scenes = reopened.get("scenes") or [reopened]  # {project_name, scenes: [OpenedScene]}
    first = scenes[0] if scenes else {}
    rt_settings = first.get("scene_settings") or {}
    rt_models = len(first.get("new_model_ids") or [])
    r["form_roundtrip"] = {
        "project_name": reopened.get("project_name"),
        "scene_count": len(scenes),
        "scene_settings": rt_settings,
        "model_count": rt_models,
        "expected_model_count": len(chambers),
        "ok": (len(scenes) == 1 and rt_models == len(chambers)
               and rt_settings.get("machine_type") == profile.machine_type
               and rt_settings.get("material_code") == profile.material_code),
    }
    for sc in scenes:
        if sc.get("scene_id"):
            log.run("delete the verification scene", "POST /v1/scene/delete/",
                    lambda sc=sc: api.delete_scene(scene_id=sc["scene_id"]))
    r["outputs"] = outputs
    r["steps"] = log.steps
    r["job_submitted"] = False
    return r


def _warnings(checks: dict) -> list[str]:
    """Human-readable printability warnings reported by PreForm (never silently dropped)."""
    out = []
    for name, res in (checks.get("cups") or {}).items():
        if isinstance(res, dict) and res.get("cup_count"):
            out.append(f"chamber {name}: PreForm reports {res['cup_count']} cup(s) — concave "
                       "pockets under overhanging pore lips that can add peel suction. All pores "
                       "open to the cut face (mesh validation: no enclosed cavities), so resin "
                       "drains in the wash; a human should review them in PreForm.")
    for name, res in (checks.get("minima") or {}).items():
        if isinstance(res, dict) and res.get("minima_count"):
            out.append(f"chamber {name}: {res['minima_count']} unsupported minima")
    for name, res in (checks.get("supportedness") or {}).items():
        if isinstance(res, dict) and res.get("percent_unsupported"):
            out.append(f"chamber {name}: {res['percent_unsupported']}% undersupported area")
    for name, res in (checks.get("thin_features") or {}).items():
        if isinstance(res, dict) and res.get("has_thin_walls"):
            out.append(f"chamber {name}: thin walls ({res.get('thin_volume_mm3')} mm^3)")
    for name, res in (checks.get("manifoldness") or {}).items():
        if isinstance(res, dict) and res.get("is_manifold") is False:
            out.append(f"chamber {name}: not manifold ({res.get('status')})")
    ib = checks.get("in_bounds") or {}
    if ib.get("all_in_bounds") is False:
        out.append("not every model fits the build volume")
    if (checks.get("interferences") or {}).get("model_ids"):
        out.append(f"collisions: {checks['interferences']['model_ids']}")
    return out


def _rekey(v: Any, by_id: dict[str, str]) -> Any:
    if isinstance(v, dict):
        return {by_id.get(k, k): _rekey(x, by_id) for k, x in v.items()}
    return v


def _metadata_subset(meta: dict, by_id: dict[str, str]) -> dict:
    keep = ("MaterialName", "MaterialCode", "MaterialUsage_mL", "ModelsVolume_mL",
            "RaftVolume_mL", "LayerThickness_mm", "PrintSettingsName", "PrintSettingsCode",
            "RequiredTankName", "PreFormBuildType", "PreFormGitCommit", "PreFormVersionNumber",
            "PublicApiIntegrationName", "AllowedMachineTypeIds", "SceneHeight_mm", "Action_v2")
    out = {k: meta.get(k) for k in keep if k in meta}
    models = []
    for m in meta.get("Models", []):
        gid = (m.get("Guid") or "").strip("{}")
        models.append({"chamber": by_id.get(gid, gid), "name": m.get("DisplayName"),
                       "model_volume_ml": m.get("ModelVolume_mL"),
                       "raft_volume_ml": m.get("RaftVolume_mL"),
                       "total_volume_ml": m.get("Volume_mL"),
                       "model_bbox_size_mm": m.get("ModelBoundingBoxSize_worldxyz"),
                       "layers": [m.get("StartLayer"), m.get("EndLayer")]})
    out["models"] = models
    return out


def _estimates(scene: dict, times: dict, meta: dict) -> dict:
    mu = scene.get("material_usage") or {}
    total = mu.get("volume_ml")
    models = meta.get("ModelsVolume_mL")
    raft = meta.get("RaftVolume_mL")
    supports = (total - models - raft) if None not in (total, models, raft) else None
    secs = times.get("total_print_time_s")
    return {
        "print_time_s": secs,
        "print_time_h": round(secs / 3600, 2) if secs is not None else None,
        "print_time_source": "POST /v1/scene/calculate-print-time/ total_print_time_s",
        "material_volume_ml": round(total, 2) if total is not None else None,
        "material_volume_source": "POST /v1/scene/get/ material_usage.volume_ml "
                                  "(models + supports + raft)",
        "models_volume_ml": round(models, 2) if models is not None else None,
        "raft_volume_ml": round(raft, 2) if raft is not None else None,
        "supports_volume_ml_derived": round(supports, 2) if supports is not None else None,
        "layer_count": scene.get("layer_count"),
        "provenance": "estimated",
    }


# ---------------------------------------------------------------------------------------------
# The human gate
# ---------------------------------------------------------------------------------------------


def print_confirmation_status(i_confirm_print: bool, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    env_ok = env.get(CONFIRM_ENV) == CONFIRM_VALUE
    missing = []
    if not i_confirm_print:
        missing.append("--i-confirm-print flag")
    if not env_ok:
        missing.append(f"{CONFIRM_ENV}={CONFIRM_VALUE} environment variable")
    return {"flag": bool(i_confirm_print), "env": env_ok, "confirmed": not missing,
            "missing": missing}


def submit_print(
    form_file: str | Path,
    printer: str | None,
    *,
    i_confirm_print: bool = False,
    env: dict | None = None,
    manifest: dict | None = None,
    echo: Callable[[str], None] = print,
    open_preform_fn: Callable[..., Any] | None = None,
) -> int:
    """Upload a prepared .form to a printer's queue — only with two explicit human confirmations.

    Always prints a summary first. Returns 2 (and touches nothing) unless BOTH the
    ``--i-confirm-print`` flag and ``FERMENTAI_FORMLABS_CONFIRM_PRINT=yes`` are present. Even
    then it uses ``print_now=False`` (upload to the printer's queue); a person still starts the
    print on the printer touchscreen after loading resin and the build platform. Mock backends
    never print.
    """
    status = print_confirmation_status(i_confirm_print, env)
    echo("=== Formlabs print submission (human-gated) ===")
    echo(f"  .form file : {form_file}")
    echo(f"  printer    : {printer or '(none given)'}")
    if manifest:
        est = (manifest.get("build") or {}).get("estimates", {})
        echo(f"  profile    : {manifest.get('printer', {}).get('label')} / "
             f"{manifest.get('material', {}).get('label')} @ "
             f"{manifest.get('layer_thickness_mm')} mm")
        echo(f"  estimate   : {est.get('print_time_h')} h, {est.get('material_volume_ml')} mL")
    echo("  Printed resin parts are display objects - not for food contact.")
    if not status["confirmed"]:
        echo("REFUSED: printing requires human confirmation. Missing: "
             + ", ".join(status["missing"]) + ". No job was submitted.")
        return 2
    if not printer:
        echo("REFUSED: no --printer (serial name, IP or Fleet Control queue id). No job was "
             "submitted.")
        return 2
    if not Path(form_file).is_file():
        echo(f"REFUSED: {form_file} does not exist. No job was submitted.")
        return 2
    if open_preform_fn is None:
        from .client import open_preform as open_preform_fn
    with open_preform_fn("live", allow_print=True) as h:
        opened = _d(h.api.open_form_file(file=str(Path(form_file).resolve())))
        scenes = opened.get("scenes") or [opened]
        if len(scenes) != 1 or not scenes[0].get("scene_id"):
            echo(f"REFUSED: {form_file} did not open as exactly one scene. No job was submitted.")
            return 2
        scene_id = scenes[0]["scene_id"]
        job = _d(h.api.print_scene(scene_id=scene_id, printer=printer,
                                   job_name=Path(form_file).stem, print_now=False))
    echo(f"Uploaded to the printer queue (print_now=False): {job}. Start it on the printer.")
    return 0


def profile_dict(p: PrintProfile) -> dict:
    return asdict(p)
