"""OPTIONAL Formlabs PreForm integration — DESCRIPTION ONLY. This module never submits prints.

The Formlabs Local API (https://developer.formlabs.com/api-reference/) is served by a locally
running PreForm Server (default http://localhost:44388). A typical flow would be:

  1. POST /scene/                       create a scene for the target machine/material
  2. POST /scene/import-model/          import data/stl/crumb_<name>.stl (units: mm)
  3. POST /scene/auto-orient/, /scene/auto-support/, /scene/auto-layout/   (optional)
  4. GET  /scene/  + print validation   inspect the prepared job
  5. POST /scene/save-form/             save a .form file for a human to review in PreForm
  6. POST /scene/print/                 ** NEVER automated here ** — requires a human

Policy (PLAN.md §12): export validated STLs first; printer settings and supports are
secondary; do not send print jobs without explicit human confirmation. Printed resin parts
are display objects, not food-contact items.

Recommended orientation for these crumb slabs: flat base on the build platform with the
open (cut) face toward the tank; there are no sealed cavities (validated), so no drain holes
are required. Craters with slight undercuts drain freely in that orientation.
"""

from __future__ import annotations

from pathlib import Path

PREFORM_DEFAULT_URL = "http://localhost:44388"


def describe_print_preparation(stl_path: str | Path, material: str = "FLGPGR05") -> dict:
    """Return (not execute) the request plan for preparing ``stl_path`` in PreForm."""
    return {
        "executes_requests": False,
        "submits_print": False,
        "server": PREFORM_DEFAULT_URL,
        "steps": [
            {"method": "POST", "path": "/scene/", "body": {"material_code": material,
                                                          "layer_thickness_mm": 0.1}},
            {"method": "POST", "path": "/scene/import-model/",
             "body": {"file": str(Path(stl_path).resolve()), "units": "MILLIMETERS"}},
            {"method": "POST", "path": "/scene/auto-layout/", "body": {}},
            {"method": "POST", "path": "/scene/save-form/",
             "body": {"file": str(Path(stl_path).with_suffix(".form"))}},
        ],
        "note": "Material code and request bodies are illustrative; check the API reference. "
                "Printing requires a human to open the .form file and press print.",
    }


def submit_print(*_args, **_kwargs):  # pragma: no cover - intentionally disabled
    raise RuntimeError("Automated print submission is disabled; a human must start prints.")
