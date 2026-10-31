"""Read-only access to generated artifacts under data/ (predictions, ALCHEMI analysis, STLs, findings)."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

SERVABLE = {".csv", ".json", ".jsonl", ".png", ".jpg", ".jpeg", ".svg", ".html", ".txt", ".md",
            ".stl", ".xyz", ".pdb", ".sdf", ".mol", ".log", ".yaml", ".yml", ".webp"}
mimetypes.add_type("model/stl", ".stl")
mimetypes.add_type("chemical/x-xyz", ".xyz")


def list_files(root: Path, url_prefix: str, exts: set[str] | None = None) -> list[dict]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        out.append({"path": rel, "name": p.name, "suffix": p.suffix.lower(), "bytes": st.st_size,
                    "modified_utc": st.st_mtime, "url": f"{url_prefix}/{rel}"})
    return out


def safe_file(root: Path, rel: str) -> Path:
    root = root.resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(404, f"file not found: {rel}")
    if p.suffix.lower() not in SERVABLE:
        raise HTTPException(415, f"file type {p.suffix} not served")
    return p


def serve(root: Path, rel: str) -> FileResponse:
    p = safe_file(root, rel)
    media, _ = mimetypes.guess_type(p.name)
    return FileResponse(p, media_type=media or "application/octet-stream")


def molecular_summary(alchemi_dir: Path, url_prefix: str) -> dict:
    analysis = alchemi_dir / "analysis"
    files = list_files(analysis, url_prefix)
    if not files:
        return {"available": False, "reason": f"no ALCHEMI analysis files under {analysis}",
                "files": [], "summaries": {}, "provenance": None}
    summaries = {}
    for f in files:
        if f["suffix"] == ".json" and f["bytes"] < 5_000_000:
            try:
                summaries[f["path"]] = json.loads((analysis / f["path"]).read_text())
            except (OSError, json.JSONDecodeError) as e:
                summaries[f["path"]] = {"error": f"unreadable: {e}"}
    return {"available": True, "files": files, "summaries": summaries,
            "provenance": "ALCHEMI outputs as cached on the Mac (see files for run metadata)"}


def find_findings(data_dir: Path, explicit: Path | None) -> Path | None:
    candidates = [explicit] if explicit else []
    candidates += [data_dir / "findings.jsonl", data_dir / "agents" / "findings.jsonl",
                   data_dir / "experiments" / "findings.jsonl", data_dir / "findings.json"]
    for c in candidates:
        if c and c.is_file():
            return c
    found = sorted(data_dir.rglob("findings*.json*")) if data_dir.is_dir() else []
    return found[0] if found else None


def read_findings(path: Path, limit: int) -> list:
    text = path.read_text()
    if path.suffix == ".jsonl":
        items = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    items.append({"unparsed": line})
    else:
        data = json.loads(text)
        items = data.get("findings", data) if isinstance(data, dict) else data
        items = items if isinstance(items, list) else [items]
    return items[-limit:]
