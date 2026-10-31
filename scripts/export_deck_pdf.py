"""Export the FermentAI investor deck (docs/pitch/deck) to PDF.

Produces:
  docs/pitch/FermentAI_pitch.pdf        one 1920x1080 page per slide
  docs/pitch/FermentAI_pitch_notes.pdf  presenter copy: slide at half size + speaker notes

Each slide in docs/pitch/deck/slides/<id>.html is a single <section> on a 1920x1080 canvas.
Its trailing <aside> holds the speaker notes and is stripped from the slide page. Images are
referenced as /_blob/<id> (uploaded copies); they are swapped for the current repo figures.

Rendering uses headless Chrome (--print-to-pdf); merging uses pypdf:

    uv run --with pypdf python scripts/export_deck_pdf.py
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DECK = REPO / "docs" / "pitch" / "deck"
EXPORT = DECK / "export"
OUT_DECK = REPO / "docs" / "pitch" / "FermentAI_pitch.pdf"
OUT_NOTES = REPO / "docs" / "pitch" / "FermentAI_pitch_notes.pdf"

# Slide id -> repo figure (figure_sources.md; the README's pipeline entry is stale:
# the brigade strip the pipeline slide describes is data/figures/architecture.png).
FIGURES = {
    "cover": "data/figures/closed_loop.png",
    "pipeline": "data/figures/architecture.png",
    "partners": "data/figures/tech_highlights.png",
    "traction": "data/figures/benchmark_summary.png",
}

W, H = 1920, 1080
NOTES_W, NOTES_H = 1120, 1440  # presenter page: 960x540 slide + notes, 80px margins

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "chrome",
]

RESET_CSS = """
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3, p, ul, ol { margin: 0; }
section.slide { width: %dpx; height: %dpx; overflow: hidden; position: relative; }
/* A figure with a fixed height in a flex column would overflow the canvas when the headline
   wraps (partners); let it shrink instead (object-fit: contain keeps its aspect ratio). */
section.slide img { min-height: 0; min-width: 0; flex-shrink: 1; }
""" % (W, H)


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    sys.exit("No Chrome/Chromium found")


def font_links(deck: dict) -> str:
    return "\n".join(
        f'<link rel="stylesheet" href="{html.escape(f["href"])}">' for f in deck.get("faces", {}).values()
    )


def split_slide(slide_id: str) -> tuple[str, str]:
    """Return (section HTML without <aside>, speaker notes text)."""
    src = (DECK / "slides" / f"{slide_id}.html").read_text(encoding="utf-8")
    m = re.search(r"<aside[^>]*>(.*?)</aside>", src, flags=re.S)
    notes = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    section = re.sub(r"\s*<aside[^>]*>.*?</aside>", "", src, flags=re.S).strip()

    def fix_img(tag: re.Match) -> str:
        t = tag.group(0)
        df = re.search(r'data-file="([^"]+)"', t)
        rel = df.group(1) if df else FIGURES[slide_id]
        path = (REPO / rel).resolve()
        if not path.exists():
            print(f"WARNING: {slide_id}: missing figure {rel}", file=sys.stderr)
        return re.sub(r'src="[^"]*"', f'src="{path.as_uri()}"', t)

    section = re.sub(r"<img\b[^>]*>", fix_img, section)
    # Fix the canvas size on the root <section>.
    section = re.sub(r"<section\b", '<section class="slide"', section, count=1)
    return section, notes


def page(title: str, head: str, css: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
{head}
<style>{RESET_CSS}{css}</style></head>
<body>{body}</body></html>
"""


def slide_page(slide_id: str, section: str, fonts: str) -> str:
    css = f"@page {{ size: {W}px {H}px; margin: 0; }} html, body {{ width: {W}px; height: {H}px; }}"
    return page(slide_id, fonts, css, section)


def notes_page(slide_id: str, n: int, total: int, section: str, notes: str, fonts: str) -> str:
    css = f"""
@page {{ size: {NOTES_W}px {NOTES_H}px; margin: 0; }}
html, body {{ width: {NOTES_W}px; height: {NOTES_H}px; background: #ffffff; }}
.wrap {{ padding: 80px; font-family: 'IBM Plex Sans', Arial, sans-serif; color: #1b1f24; }}
.thumb {{ width: {W // 2}px; height: {H // 2}px; overflow: hidden; border: 1px solid #c9c3b8; box-sizing: content-box; }}
.thumb section.slide {{ transform: scale(0.5); transform-origin: 0 0; }}
.meta {{ margin-top: 40px; font-family: 'Rubik', Arial, sans-serif; font-size: 22px; font-weight: 600;
        letter-spacing: 1.5px; text-transform: uppercase; color: #1f77b4; }}
.notes {{ margin-top: 20px; font-size: 27px; line-height: 1.5; }}
"""
    body = f"""<div class="wrap">
<div class="thumb">{section}</div>
<p class="meta">Slide {n} / {total} · {html.escape(slide_id)} · speaker notes</p>
<p class="notes">{html.escape(notes)}</p>
</div>"""
    return page(f"{slide_id} notes", fonts, css, body)


def print_pdf(chrome: str, src: Path, dst: Path) -> None:
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--allow-file-access-from-files",
        "--no-pdf-header-footer",
        "--virtual-time-budget=15000",
        "--run-all-compositor-stages-before-draw",
        f"--print-to-pdf={dst}",
        src.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not dst.exists():
        sys.exit(f"Chrome failed on {src.name}:\n{r.stderr[-2000:]}")


def merge(parts: list[Path], out: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    for p in parts:
        r = PdfReader(str(p))
        if len(r.pages) != 1:
            print(f"WARNING: {p.name} rendered {len(r.pages)} pages (overflow?)", file=sys.stderr)
        w.add_page(r.pages[0])
    w.add_metadata({"/Title": "FermentAI Investor Pitch", "/Author": "FermentAI"})
    with out.open("wb") as f:
        w.write(f)


def main() -> None:
    deck = json.loads((DECK / "deck.json").read_text(encoding="utf-8"))
    order = deck["order"]
    fonts = font_links(deck)
    chrome = find_chrome()
    EXPORT.mkdir(parents=True, exist_ok=True)

    slide_pdfs, note_pdfs = [], []
    for i, sid in enumerate(order, 1):
        section, notes = split_slide(sid)
        for kind, text, bucket in (
            ("slide", slide_page(sid, section, fonts), slide_pdfs),
            ("notes", notes_page(sid, i, len(order), section, notes, fonts), note_pdfs),
        ):
            h = EXPORT / f"{i:02d}_{sid}_{kind}.html"
            h.write_text(text, encoding="utf-8")
            pdf = h.with_suffix(".pdf")
            print_pdf(chrome, h, pdf)
            bucket.append(pdf)
        print(f"rendered {i}/{len(order)}: {sid}")

    merge(slide_pdfs, OUT_DECK)
    merge(note_pdfs, OUT_NOTES)
    print(f"wrote {OUT_DECK.relative_to(REPO)} and {OUT_NOTES.relative_to(REPO)}")


if __name__ == "__main__":
    main()
