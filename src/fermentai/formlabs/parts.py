"""User-supplied parts: STL inspection (units / mesh sanity), job files and the print sheet.

STLs from outside the repo are treated as untrusted *data*: they are copied unchanged into
``data/stl/external/`` and only parsed as meshes (trimesh). Nothing is rescaled or repaired on
disk — the inspection reports what it finds, and repair (if any) is PreForm's, recorded per
import in the manifest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .prepare import PROFILES, REPO_ROOT, PartSpec, sha256_file

EXTERNAL_DIR = REPO_ROOT / "data" / "stl" / "external"
JOBS_DIR = REPO_ROOT / "data" / "stl" / "print" / "jobs"
#: Plausible largest dimension for a desk-scale SLA print, mm (outside -> ask the user).
PLAUSIBLE_MM = (5.0, 300.0)


def _units_verdict(max_extent: float) -> dict:
    lo, hi = PLAUSIBLE_MM
    alt = {"if_inches_mm": round(max_extent * 25.4, 2), "if_cm_mm": round(max_extent * 10, 2),
           "if_m_mm": round(max_extent * 1000, 2)}
    if lo <= max_extent <= hi:
        return {"verdict": "mm plausible", "plausible": True,
                "reason": f"largest extent {max_extent:.2f} within {lo:g}-{hi:g} mm", **alt}
    return {"verdict": "ASK USER: implausible size if mm", "plausible": False,
            "reason": f"largest extent {max_extent:.4g} outside {lo:g}-{hi:g} mm", **alt}


def _ray_thickness(mesh: Any, samples: int = 3000, seed: int = 0) -> dict:
    """Local wall thickness: distance along the inward normal to the opposite surface."""
    import numpy as np

    rng = np.random.default_rng(seed)
    pts, fidx = mesh.sample(samples, return_index=True, seed=int(rng.integers(1 << 31)))
    normals = mesh.face_normals[fidx]
    origins = pts - normals * 1e-4
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
    if len(ray_idx) == 0:
        return {"samples": samples, "hits": 0}
    d = np.linalg.norm(locs - origins[ray_idx], axis=1)
    d = d[d > 1e-3]
    if not len(d):
        return {"samples": samples, "hits": 0}
    q = np.percentile(d, [0, 1, 5, 25, 50, 75])
    return {"samples": samples, "hits": len(d),
            "min_mm": round(float(q[0]), 3), "p01_mm": round(float(q[1]), 3),
            "p05_mm": round(float(q[2]), 3), "p25_mm": round(float(q[3]), 3),
            "median_mm": round(float(q[4]), 3), "p75_mm": round(float(q[5]), 3),
            "method": "ray from surface sample along -normal to the next surface (trimesh); "
                      "approximate, informational"}


def inspect_stl(path: str | Path, platform_mm: tuple[float, float, float] = (200, 125, 210),
                ) -> dict:
    """Inspect one STL without modifying it. Units are assumed mm and sanity-checked."""
    import trimesh

    path = Path(path)
    raw = trimesh.load(path, force="mesh", process=False)
    m = trimesh.load(path, force="mesh")  # merges duplicate vertices (STL is a triangle soup)
    ext = [round(float(x), 3) for x in m.extents]
    degenerate = int((m.area_faces < 1e-12).sum())
    comps = m.split(only_watertight=False)
    clean = m.copy()
    clean.update_faces(clean.nondegenerate_faces(height=1e-9))
    clean.remove_unreferenced_vertices()
    clean.merge_vertices()
    clean_comps = clean.split(only_watertight=False)
    fits = sorted(ext[:2]) <= sorted(platform_mm[:2]) and ext[2] <= platform_mm[2]
    rep = {
        "file": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "triangles": len(raw.faces),
        "units_assumed": "mm (STL has no units; PreForm import uses MILLIMETERS)",
        "bbox_min_mm": [round(float(x), 3) for x in m.bounds[0]],
        "bbox_max_mm": [round(float(x), 3) for x in m.bounds[1]],
        "extents_mm": ext,
        "units_check": _units_verdict(max(ext)),
        "as_supplied": {
            "watertight": bool(m.is_watertight),
            "winding_consistent": bool(m.is_winding_consistent),
            "components": len(comps),
            "zero_volume_components": int(sum(abs(c.volume) < 1e-6 for c in comps)),
            "degenerate_zero_area_triangles": degenerate,
            "volume_mm3": round(float(m.volume), 3),
        },
        "after_dropping_zero_area_triangles": {
            "note": "analysis only — the file on disk is unchanged",
            "watertight": bool(clean.is_watertight),
            "components": len(clean_comps),
            "volume_mm3": round(float(clean.volume), 3),
            "euler_number": int(clean.euler_number),
        },
        "fits_form4_platform_unrotated": bool(fits),
    }
    rep["wall_thickness"] = _ray_thickness(clean if clean.is_watertight else m)
    defects = []
    if not rep["as_supplied"]["watertight"]:
        if rep["after_dropping_zero_area_triangles"]["watertight"] and \
                rep["after_dropping_zero_area_triangles"]["components"] == 1:
            defects.append(f"{degenerate} zero-area triangles make the mesh non-manifold as "
                           "supplied; without them it is one watertight solid with the same "
                           "volume -> cosmetic; PreForm's import repair handles it")
        else:
            defects.append("not watertight even after dropping zero-area triangles -> needs a "
                           "real repair; ask the user")
    thin = rep["wall_thickness"].get("p01_mm")
    if thin is not None and thin < 0.5:
        defects.append(f"1% of sampled walls are thinner than 0.5 mm ({thin} mm)")
    rep["defects"] = defects
    rep["printable_verdict"] = (
        "printable as supplied (after PreForm import repair of degenerate triangles)"
        if rep["units_check"]["plausible"]
        and rep["after_dropping_zero_area_triangles"]["watertight"] else
        "needs a decision from the user")
    return rep


def write_parts_report(paths: list[Path], out: Path = EXTERNAL_DIR / "parts_report.json",
                       originals: dict[str, str] | None = None,
                       notes: dict[str, str] | None = None) -> dict:
    doc = {
        "provenance": "imported_human: user-supplied STL files, copied unchanged and inspected "
                      "with trimesh (no rescaling, no repair on disk)",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": "fermentai.formlabs.parts.inspect_stl",
        "parts": {},
    }
    for p in paths:
        rep = inspect_stl(p)
        if originals and p.name in originals:
            rep["copied_from"] = originals[p.name]
        if notes and p.stem in notes:
            rep["inspector_note"] = notes[p.stem]
        doc["parts"][p.stem] = rep
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


# ---------------------------------------------------------------------------------------------
# Job files
# ---------------------------------------------------------------------------------------------


def load_job(path: str | Path) -> dict:
    """Load a YAML/JSON job file -> {"name", "profile", "parts": [PartSpec], ...}.

    Job keys: ``name``, ``profile``, ``spacing_mm`` [5], ``layout_attempts``, ``role``
    (primary | alternative), ``note``, ``build_name_template`` [``{name}_job{k}_{profile}``].
    Each part: ``stl`` (repo-relative or absolute), ``count``, ``name``, ``label``,
    ``orientation`` (open_face_up | upright | preform_auto), ``supports`` (raft | mini_raft |
    mini_rafts_on_bp | none), ``repair`` (error | error_then_repair | repair), ``provenance``,
    or ``chamber: A`` to pull hash/validation from chain_summary.json.
    """
    import yaml

    from .prepare import load_chamber_inputs

    path = Path(path)
    spec = yaml.safe_load(path.read_text())
    profile = spec.get("profile", "form4-grey-v5")
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    parts: list[PartSpec] = []
    for item in spec["parts"]:
        if "chamber" in item:
            (p,) = load_chamber_inputs(names=(item["chamber"],))
            p.count = int(item.get("count", 1))
            if item.get("label"):
                p.label = item["label"]
            p.supports = item.get("supports", p.supports)
            p.orientation = item.get("orientation", p.orientation)
            p.__post_init__()
        else:
            stl = Path(item["stl"])
            stl = stl if stl.is_absolute() else REPO_ROOT / stl
            p = PartSpec(name=item.get("name", stl.stem), stl=stl,
                         display_name=item.get("name", stl.stem), count=item.get("count", 1),
                         orientation=item.get("orientation", "preform_auto"),
                         repair=item.get("repair", "error_then_repair"),
                         supports=item.get("supports", "raft"),
                         label=item.get("label"),
                         provenance=item.get("provenance", "imported_human"))
            if stl.is_file():
                p.sha256_actual = sha256_file(stl)
        parts.append(p)
    return {"name": spec.get("name", path.stem), "profile": profile, "parts": parts,
            "spacing_mm": float(spec.get("spacing_mm", 5.0)),
            "layout_attempts": spec.get("layout_attempts"),
            "role": spec.get("role", "primary"), "note": spec.get("note"),
            "build_name_template": spec.get("build_name_template", "{name}_job{k}_{profile}"),
            "source": str(path.resolve().relative_to(REPO_ROOT))
            if path.resolve().is_relative_to(REPO_ROOT) else str(path)}


def parse_part_arg(arg: str) -> PartSpec:
    """``path[:count[:orientation]]`` -> PartSpec (CLI ``--part``)."""
    bits = arg.split(":")
    stl = Path(bits[0]).expanduser()
    stl = stl if stl.is_absolute() else (Path.cwd() / stl)
    count = int(bits[1]) if len(bits) > 1 and bits[1] else 1
    orient = bits[2] if len(bits) > 2 and bits[2] else "preform_auto"
    p = PartSpec(name=stl.stem, stl=stl.resolve(), display_name=stl.stem, count=count,
                 orientation=orient, repair="error_then_repair", provenance="imported_human")
    if p.stl.is_file():
        p.sha256_actual = sha256_file(p.stl)
    return p


# ---------------------------------------------------------------------------------------------
# One-page print sheet
# ---------------------------------------------------------------------------------------------


MATERIAL_NOTES = {
    "FLGPCL05": [
        ("Clear shows everything: support touchpoints, raft marks and any trapped resin or cups "
        "are visible through the part. Sand touchpoints (wet, fine grit) and clear-coat or "
        "polish if you want glass-like faces."),
        ("Clear V5 TDS reference: Form Wash 5 min in ≥99% IPA; post-cure 15 min at 60 °C in a "
        "Form Cure. Clear yellows slightly with longer or hotter cure (and with UV over time): "
        "for display parts, do not over-cure; check the current Clear V5 TDS."),
    ],
    "FLGPBK05": [
        ("Black V5 TDS reference: Form Wash 5 min in ≥99% IPA; post-cure 15 min at 60 °C in a "
        "Form Cure (some TDS versions: 5 min at room temperature). Check the current sheet."),
    ],
    "FLGPGR05": [
        ("Grey V5 TDS reference: Form Wash 5 min in ≥99% IPA; cure per the current Grey V5 TDS "
        "(published versions list 5 min at room temperature and 15 min at 60 °C)."),
    ],
}


def render_print_sheet(run: str, sections: list[dict], source: str | None,
                       server_version: str | None) -> str:
    """Markdown print sheet for the human operator: one section per job (e.g. per resin)."""
    lines = [
        f"# Print sheet — {run}",
        "",
        (f"Prepared with the Formlabs PreForm API ({source}, PreForm {server_version}). "
        "**Nothing has been sent to a printer.** Printing requires a person at PreForm and at "
        "the printer. All times and volumes are PreForm estimates."),
        "",
    ]
    primary = [s for s in sections if s["job"].get("role", "primary") == "primary"]
    tot_s = sum(b["estimates"]["print_time_s"] or 0 for s in primary for b in s["builds"])
    tot_ml = {}
    for s in primary:
        lab = PROFILES[s["profile"]].material_label
        tot_ml[lab] = tot_ml.get(lab, 0) + sum(b["estimates"]["material_volume_ml"] or 0
                                               for b in s["builds"])
    nb = sum(len(s["builds"]) for s in primary)
    lines += [f"**To print: {nb} build(s)**, ≈ {tot_s / 3600:.2f} h of printer time; resin: "
              + ", ".join(f"{lab} ≈ {ml:.0f} mL" for lab, ml in tot_ml.items()) + ".", ""]
    for sec in sections:
        job, prof = sec["job"], PROFILES[sec["profile"]]
        alt = job.get("role") == "alternative"
        lines += [(f"## {'ALTERNATIVE — ' if alt else ''}{prof.material_label} "
                  f"(`{prof.material_code}`) · {prof.printer_label} · "
                  f"{prof.layer_thickness_mm} mm — job `{job['name']}`"), ""]
        if job.get("note"):
            lines += [f"_{job['note']}_", ""]
        for k, j in enumerate(sec["builds"], 1):
            e = j["estimates"]
            form = j["outputs"]["form"]
            lines += [
                f"### Build {k} of {len(sec['builds'])}: `{Path(form).name}`",
                "",
                f"- **Open:** `{_rel(form)}`",
                f"- **Parts ({len(j['placed'])}):** "
                + "; ".join(f"{p} — {j['labels'][p]}" if j["labels"].get(p) else p
                            for p in j["placed"]),
                "- **Orientation / supports:** " + _orientation_note(j),
                (f"- **Estimate:** {e['print_time_h']} h ({e['print_time_s']} s), "
                f"{e['material_volume_ml']} mL resin (models {e['models_volume_ml']}, raft "
                f"{e['raft_volume_ml']}, supports {e['supports_volume_ml_derived']}), "
                f"{e['layer_count']} layers"),
                (f"- **Preview:** `{_rel(j['outputs'].get('preview_iso', ''))}`, "
                f"`{_rel(j['outputs'].get('preview_top', ''))}`"),
                f"- **Manifest:** `{_rel(j['manifest'])}`",
            ]
            repaired = [k2 for k2, v in (j.get("imports") or {}).items()
                        if v.get("needed_repair")]
            if repaired:
                lines.append(f"- **Mesh repair on import (PreForm):** {', '.join(repaired)}")
            lines.extend(f"- **Note:** {n}" for n in j.get("placement_notes", []))
            lines.append("- **PreForm warnings:** " + ("none" if not j["warnings"] else ""))
            lines += [f"  - {w}" for w in j["warnings"]]
            lines.append("")
        lines += [f"**{prof.material_label} notes:**", ""]
        lines += [f"- {n}" for n in MATERIAL_NOTES.get(prof.material_code, [])]
        lines.append("")
    lines += [
        "## How to print each build (PreForm desktop)",
        "",
        "_Menu names as in PreForm 3.x; the Insider build may label them slightly differently._",
        "",
        "1. PreForm › **File › Open…** › choose the `.form` above (it opens as its own job).",
        ("2. Check the job: the job-info / print-validation panel shows the warnings listed "
        "above (cups, unsupported areas). Inspect the flagged parts; do not add or move supports "
        "on the crumb faces."),
        ("3. Confirm the job setup (printer type, resin, 0.1 mm) matches the section heading — "
        "Clear and Black are different cartridges *and* need their own resin tank."),
        ("4. Click **Print** › select the Form 4 from the printer list (or its Dashboard group) "
        "› **Upload** (adds the job to the printer's queue)."),
        ("5. At the printer: load the matching cartridge, its resin tank and a clean build "
        "platform › select the job on the touchscreen › **confirm** › Print."),
        "",
        "## After printing",
        "",
        ("- Remove the build platform and release the parts (flex the platform or use the "
        "part-removal tool). Slabs printed directly on the platform need a careful, flat "
        "release."),
        ("- Wash in a Form Wash (≥99% IPA, see the resin notes above); let parts dry completely "
        "before curing; cure in a Form Cure per the resin notes."),
        ("- Clip supports after curing and sand the touchpoints. The crumb faces carry no "
        "supports; on the hats and Clawds touchpoints are on the surfaces PreForm chose."),
        "- **Display only — not food-safe. Never place prints in contact with bread.**",
        "",
    ]
    return "\n".join(lines)


def _orientation_note(j: dict) -> str:
    pol = j.get("orientation_policy_per_model") or {}
    sup = (j.get("support_parameters") or {}).get("per_model") or {}
    bits = []
    for k, v in pol.items():
        s = sup.get(k, "FULL_RAFT")
        on_bp = s.startswith("none")
        if v == "open_face_up":
            bits.append(f"{k}: crumb face up, " + ("directly on the build platform (no "
                        "supports, no raft)" if on_bp else f"{s}, supports only under the "
                        "flat floor"))
        elif v == "upright":
            bits.append(f"{k}: upright (as modelled), {s}")
        else:
            bits.append(f"{k}: PreForm auto-orient (tilted), {s}; supports touch visible "
                        "surfaces")
    return "; ".join(bits) or "n/a"


def _rel(p: str | Path) -> str:
    if not p:
        return ""
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)
