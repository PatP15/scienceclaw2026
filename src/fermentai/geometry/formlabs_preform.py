"""Formlabs PreForm integration — moved to :mod:`fermentai.formlabs` (thin re-export).

The real integration (official ``formlabs-sdk`` + PreFormServer, mock replay, manifest, human
print gate) lives in ``src/fermentai/formlabs/``; run ``scripts/prepare_print.py``. See
docs/formlabs_workflow.md. Nothing here ever submits a print.
"""

from __future__ import annotations

from pathlib import Path

from fermentai.formlabs import (  # noqa: F401  (re-exports)
    PITCH_CLAIM,
    PROFILES,
    MockPreFormServer,
    PreFormUnavailable,
    locate_preform_server,
    open_preform,
    prepare_build,
    submit_print,
)
from fermentai.formlabs.client import DEFAULT_PORT

PREFORM_DEFAULT_URL = f"http://localhost:{DEFAULT_PORT}"


def describe_print_preparation(stl_path: str | Path, material: str = "FLGPGR05") -> dict:
    """Return (not execute) the PreForm API v1 request plan used by fermentai.formlabs."""
    return {
        "executes_requests": False,
        "submits_print": False,
        "server": PREFORM_DEFAULT_URL,
        "steps": [
            {"method": "POST", "path": "/v1/scene/create/",
             "body": {"machine_type": "FORM-4-0", "material_code": material,
                      "layer_thickness_mm": 0.1}},
            {"method": "POST", "path": "/v1/scene/model/import/",
             "body": {"file": str(Path(stl_path).resolve()), "units": "MILLIMETERS"}},
            {"method": "POST", "path": "/v1/scene/model/auto-orient/"},
            {"method": "POST", "path": "/v1/scene/model/orient/",
             "body": {"orientation": {"z_direction": [0, 0, 1], "x_direction": [1, 0, 0]}}},
            {"method": "POST", "path": "/v1/scene/model/auto-support/"},
            {"method": "POST", "path": "/v1/scene/model/auto-layout/"},
            {"method": "POST", "path": "/v1/scene/calculate-print-time/"},
            {"method": "POST", "path": "/v1/scene/save/"},
        ],
        "note": "Executed for real by scripts/prepare_print.py; printing requires a human.",
    }
