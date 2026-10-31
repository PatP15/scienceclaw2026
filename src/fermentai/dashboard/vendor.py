"""Vendored front-end assets for the offline dashboard.

The dashboard may load only files from ``static/`` (no CDN). Plotly is the single third-party
library; it is copied byte-for-byte from the installed ``plotly`` Python package:

    uv run python -m fermentai.dashboard.vendor           # (re)copy plotly.min.js + data snapshots
    uv run python -m fermentai.dashboard.vendor --check   # exit 1 if missing / differs

Data snapshots: the API has no route for ``data/benchmarks/`` (in-silico agent benchmark), so the
few files the Digital Twin panel shows are copied into ``static/snapshots/benchmarks/`` together
with a ``SNAPSHOT.json`` manifest (source path, sha256, copy time). ``--check`` and
tests/test_dashboard_offline.py fail if a snapshot no longer matches its source.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = STATIC_DIR / "vendor"
PLOTLY_JS = VENDOR_DIR / "plotly.min.js"
REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_DIR = STATIC_DIR / "snapshots"
# published name -> source (repo-relative)
SNAPSHOTS = {
    "benchmarks/summary.json": "data/benchmarks/summary.json",
    "benchmarks/run_manifest.json": "data/benchmarks/run_manifest.json",
    "benchmarks/paired_differences_well_specified.png": "data/benchmarks/paired_differences_well_specified.png",
}


def plotly_source() -> Path | None:
    """Path of plotly.min.js shipped inside the installed plotly Python package (or None)."""
    try:
        import plotly
    except ImportError:
        return None
    p = Path(plotly.__file__).resolve().parent / "package_data" / "plotly.min.js"
    return p if p.is_file() else None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vendor_plotly() -> Path:
    src = plotly_source()
    if src is None:
        raise SystemExit("plotly is not installed; cannot vendor plotly.min.js")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, PLOTLY_JS)
    return PLOTLY_JS


def sync_snapshots() -> list[Path]:
    """Copy the SNAPSHOTS sources into static/snapshots/ and write SNAPSHOT.json manifests."""
    import json
    from datetime import UTC, datetime

    out, manifests = [], {}
    for rel, src_rel in SNAPSHOTS.items():
        src = REPO_ROOT / src_rel
        if not src.is_file():
            continue
        dest = SNAPSHOT_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        out.append(dest)
        manifests.setdefault(dest.parent, {})[dest.name] = {"source": src_rel, "sha256": sha256(src)}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for d, files in manifests.items():
        (d / "SNAPSHOT.json").write_text(json.dumps(
            {"note": "Copied by fermentai.dashboard.vendor because no API route serves the source directory.",
             "copied_utc": now, "files": files}, indent=1) + "\n")
    return out


def snapshot_problems() -> list[str]:
    problems = []
    for rel, src_rel in SNAPSHOTS.items():
        src, dest = REPO_ROOT / src_rel, SNAPSHOT_DIR / rel
        if not src.is_file():
            continue  # source absent (e.g. a fresh checkout without benchmark outputs): nothing to compare
        if not dest.is_file():
            problems.append(f"missing snapshot {dest} (run: uv run python -m fermentai.dashboard.vendor)")
        elif sha256(src) != sha256(dest):
            problems.append(f"stale snapshot {dest}; source {src_rel} changed (run: uv run python -m fermentai.dashboard.vendor)")
    return problems


def check() -> list[str]:
    problems = []
    if not PLOTLY_JS.is_file():
        problems.append(f"missing {PLOTLY_JS}")
    else:
        src = plotly_source()
        if src is not None and sha256(src) != sha256(PLOTLY_JS):
            problems.append(f"{PLOTLY_JS} differs from the installed plotly package ({src})")
    return problems + snapshot_problems()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify only; do not copy")
    args = ap.parse_args(argv)
    if args.check:
        problems = check()
        for p in problems:
            print("FAIL:", p)
        if not problems:
            print(f"OK: {PLOTLY_JS} ({PLOTLY_JS.stat().st_size} bytes, sha256 {sha256(PLOTLY_JS)[:12]})")
        return 1 if problems else 0
    out = vendor_plotly()
    print(f"copied {out} ({out.stat().st_size} bytes)")
    for p in sync_snapshots():
        print(f"snapshot {p.relative_to(STATIC_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
