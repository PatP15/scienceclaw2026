"""Live feasibility study: can 2 hats + crumb A + crumb B fit on ONE Form 4 plate in Clear V5?

Tries six support/orientation/spacing configurations with PreForm's own auto-layout and records
which fit. Usage: uv run python -m fermentai.formlabs.fit_study data/stl/print/clear_fit_study
"""
import json
import sys
from pathlib import Path

from formlabs_preform_api.models import DirectionVectors, ImportUnits, RaftType, RenderViewType
from formlabs_sdk import PreFormApiError

from .cli import _transcript_doc
from .client import open_preform
from .prepare import REPO_ROOT

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT / "data/stl/print/clear_fit_study")
OUT.mkdir(parents=True, exist_ok=True)
STL = {"A": REPO_ROOT / "data/stl/crumb_chamber_A.stl", "B": REPO_ROOT / "data/stl/crumb_chamber_B.stl",
       "hat_1": REPO_ROOT / "data/stl/external/hat.stl", "hat_2": REPO_ROOT / "data/stl/external/hat.stl"}
UP = DirectionVectors(z_direction=[0.0, 0.0, 1.0], x_direction=[1.0, 0.0, 0.0])
ON_SIDE = DirectionVectors(z_direction=[1.0, 0.0, 0.0], x_direction=[0.0, 0.0, 1.0])

ATTEMPTS = [
    # name, slab supports (raft type or None = directly on platform), hat orientation, hat raft, spacing
    ("1_default_full_rafts", RaftType.FULL_RAFT, "auto", RaftType.FULL_RAFT, 1.0),
    ("2_mini_rafts_tight", RaftType.MINI_RAFT, "auto", RaftType.MINI_RAFT, 0.5),
    ("3_slabs_on_platform", None, "auto", RaftType.MINI_RAFT, 0.5),
    ("4_slabs_on_platform_hats_upright", None, "upright", RaftType.MINI_RAFT, 0.5),
    ("5_slabs_on_platform_hats_on_side", None, "on_side", RaftType.MINI_RAFT, 0.5),
    ("6_slabs_on_platform_hats_upright_minirafts_on_bp", None, "upright",
     RaftType.MINI_RAFTS_ON_BP, 0.0),
]

results = []
with open_preform("live", port=44390, server_log=OUT / "clear_fit_preformserver.log") as h:
    api = h.api
    for name, slab_raft, hat_orient, hat_raft, spacing in ATTEMPTS:
        sc = api.create_scene(machine_type="FORM-4-0", material_code="FLGPCL05", layer_thickness_mm=0.1)
        sid = sc.scene_id
        ids = {}
        for k, p in STL.items():
            ids[k] = api.import_models(scene_id=sid, file=str(p), name=k, units=ImportUnits.MILLIMETERS).new_models[0].model_id
        slabs, hats = [ids["A"], ids["B"]], [ids["hat_1"], ids["hat_2"]]
        api.auto_orient_models(scene_id=sid)
        api.orient_models(scene_id=sid, orientation=UP, model_ids=slabs)
        if hat_orient == "upright":
            api.orient_models(scene_id=sid, orientation=UP, model_ids=hats)
        elif hat_orient == "on_side":
            api.orient_models(scene_id=sid, orientation=ON_SIDE, model_ids=hats)
        if slab_raft is not None:
            api.auto_support_models(scene_id=sid, model_ids=slabs, raft_type=slab_raft)
        api.auto_support_models(scene_id=sid, model_ids=hats, raft_type=hat_raft)
        foot = {}
        for m in api.list_models(scene_id=sid).to_dict()["models"]:
            bb = m["bounding_box"]
            foot[m["name"]] = [round(bb["max_corner"]["x"] - bb["min_corner"]["x"], 1),
                               round(bb["max_corner"]["y"] - bb["min_corner"]["y"], 1),
                               round(bb["max_corner"]["z"] - bb["min_corner"]["z"], 1)]
        res = {"attempt": name, "slab_supports": slab_raft.value if slab_raft else "none (directly on platform)",
               "hat_orientation": hat_orient, "hat_raft": hat_raft.value, "spacing_mm": spacing,
               "footprints_with_supports_mm": foot}
        try:
            api.auto_layout_models(scene_id=sid, model_spacing_mm=spacing, layout_attempts=20)
            res["fit"] = True
            res["cups"] = api.validate_cups(scene_id=sid).to_dict()
            res["in_bounds"] = api.validate_in_bounds(scene_id=sid).to_dict()["all_in_bounds"]
            api.render_scene(scene_id=sid, file=str(OUT / f"clear_fit_{name}.png"), image_size_px=900,
                             view_type=RenderViewType.ZOOM_ON_MODELS, yaw=0.0, pitch=90.0)
        except PreFormApiError as exc:
            res["fit"] = False
            res["error_code"] = exc.code
            res["message"] = str(exc)[:200]
        print(json.dumps({k: v for k, v in res.items() if k != "cups"}))
        results.append(res)
        api.delete_scene(scene_id=sid)
    transcript = h.transcript
    version = h.server_version

doc = {"generated_by": "python -m fermentai.formlabs.fit_study", "provenance": "real: live PreForm API feasibility study (PreFormServer "
                     f"{version}) — can 2 hats + crumb A + B share one Form 4 / Clear V5 plate?",
       "build_volume_mm": [200, 125, 210], "attempts": results,
       "any_fit": any(r["fit"] for r in results)}
(OUT / "clear_fit_attempts.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
(OUT / "clear_fit_preform_api_transcript.json").write_text(
    json.dumps(_transcript_doc(transcript, version), indent=1, default=str) + "\n")
print("any_fit", doc["any_fit"])
