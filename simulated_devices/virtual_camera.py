"""Virtual side-view camera: synthetic JPEGs of a jar whose fill height tracks the simulated
starter height. Every frame is stamped "SIMULATED" - these are not photographs.

Regenerate the static fixtures with:
    uv run python -m simulated_devices.virtual_camera --write-fixtures
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
JAR_HEIGHT_MM = 100.0
W, H = 480, 360


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_jar_jpeg(chamber_id: str, starter_height_mm: float | None, *, caption: str = "",
                    door_open: bool = False, motor_running: bool = False, quality: int = 75) -> bytes:
    from PIL import Image, ImageDraw

    bg = (226, 222, 214) if not door_open else (245, 240, 225)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # shelf
    d.rectangle([0, 300, W, H], fill=(150, 135, 120))
    # jar silhouette (glass outline)
    x0, x1, y_bottom, y_top = 170, 310, 296, 76
    px_per_mm = (y_bottom - y_top) / JAR_HEIGHT_MM
    h = max(0.0, min(JAR_HEIGHT_MM, starter_height_mm or 0.0))
    fill_top = int(y_bottom - h * px_per_mm)
    if h > 0:
        d.rectangle([x0 + 6, fill_top, x1 - 6, y_bottom - 4], fill=(232, 212, 168))
        d.ellipse([x0 + 6, fill_top - 8, x1 - 6, fill_top + 8], fill=(240, 222, 182))
        for i in range(14):  # bubbles, deterministic positions
            bx = x0 + 18 + (i * 37) % (x1 - x0 - 36)
            by = fill_top + 12 + (i * 53) % max(8, (y_bottom - fill_top - 24))
            if by < y_bottom - 8:
                d.ellipse([bx, by, bx + 5, by + 5], outline=(200, 175, 130))
    d.rounded_rectangle([x0, y_top, x1, y_bottom], radius=14, outline=(90, 100, 110), width=4)
    d.rectangle([x0 - 6, y_top - 16, x1 + 6, y_top], fill=(120, 110, 100))  # lid
    # scale marks every 10 mm
    for mm in range(0, int(JAR_HEIGHT_MM) + 1, 10):
        y = int(y_bottom - mm * px_per_mm)
        d.line([x1 + 8, y, x1 + 18, y], fill=(60, 60, 60), width=1)
        d.text((x1 + 22, y - 6), f"{mm}", fill=(60, 60, 60), font=_font(10))
    # chef hat indicator
    if motor_running:
        d.ellipse([30, 40, 90, 80], fill=(255, 255, 255), outline=(80, 80, 80))
        d.text((28, 84), "motor", fill=(40, 40, 40), font=_font(12))
    # SIMULATED stamp
    d.rectangle([0, 0, W, 30], fill=(180, 20, 20))
    d.text((10, 5), "SIMULATED - synthetic frame, not a photo", fill=(255, 255, 255), font=_font(18))
    label = f"Chamber {chamber_id}   height {h:.1f} mm"
    d.text((10, 36), label, fill=(20, 20, 20), font=_font(16))
    if door_open:
        d.text((10, 58), "door open", fill=(150, 60, 0), font=_font(14))
    if caption:
        d.text((10, 330), caption[:60], fill=(255, 255, 255), font=_font(14))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def write_fixtures(out_dir: Path = FIXTURES_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for cid in ("A", "B"):
        for h in (25, 35, 45, 55):
            p = out_dir / f"jar_{cid}_{h:02d}mm.jpg"
            p.write_bytes(render_jar_jpeg(cid, float(h), caption="static fixture"))
            paths.append(p)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--out", type=Path, default=FIXTURES_DIR)
    a = ap.parse_args()
    if a.write_fixtures:
        for p in write_fixtures(a.out):
            print(p)
    else:
        ap.print_help()
