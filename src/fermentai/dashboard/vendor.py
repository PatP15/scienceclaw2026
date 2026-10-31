"""Vendored front-end assets for the offline dashboard.

The dashboard may load only files from ``static/`` (no CDN). Plotly is the single third-party
library; it is copied byte-for-byte from the installed ``plotly`` Python package:

    uv run python -m fermentai.dashboard.vendor           # (re)copy plotly.min.js
    uv run python -m fermentai.dashboard.vendor --check   # exit 1 if missing / differs
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


def check() -> list[str]:
    problems = []
    if not PLOTLY_JS.is_file():
        problems.append(f"missing {PLOTLY_JS}")
    else:
        src = plotly_source()
        if src is not None and sha256(src) != sha256(PLOTLY_JS):
            problems.append(f"{PLOTLY_JS} differs from the installed plotly package ({src})")
    return problems


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
