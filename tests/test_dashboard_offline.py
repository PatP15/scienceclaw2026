"""Offline guarantees for the dashboard (PLAN.md §8, §16 #8): no CDN, no cloud fonts, no external URLs."""

import re
from pathlib import Path

import pytest

from fermentai.dashboard import vendor

STATIC = vendor.STATIC_DIR
TEXT_EXTS = {".html", ".css", ".js", ".mjs", ".svg", ".json", ".txt"}
ABS_URL_RE = re.compile(r"\b(?:https?|wss?|ftp)://[^\s\"'`)<>]+", re.I)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")
# XML namespace identifiers are never fetched by the browser.
XML_NAMESPACES = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/2000/xmlns/")


def own_files():
    """All dashboard text assets we author (vendored third-party code is checked separately)."""
    files = [p for p in STATIC.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXTS
             and "vendor" not in p.relative_to(STATIC).parts]
    assert files, f"no dashboard assets found under {STATIC}"
    return files


def strip_comments(text: str, suffix: str) -> str:
    if suffix in (".html", ".svg"):
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if suffix in (".css", ".js", ".mjs", ".html"):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if suffix in (".js", ".mjs"):
        # line comments: `//` not preceded by ':' (so "http://..." inside strings is NOT stripped)
        text = re.sub(r"(?m)(?<![:\"'`\\w])//.*$", "", text)
    return text


def is_allowed(url: str) -> bool:
    if url.startswith(XML_NAMESPACES):
        return True
    host = re.sub(r"^[a-z]+://", "", url, flags=re.I).split("/")[0].split(":")[0]
    return host in LOCAL_HOSTS or url.split("://", 1)[-1].startswith(LOCAL_HOSTS)


@pytest.mark.parametrize("path", own_files(), ids=lambda p: str(p.relative_to(STATIC)))
def test_no_external_urls(path: Path):
    text = strip_comments(path.read_text(encoding="utf-8"), path.suffix.lower())
    bad = [u for u in ABS_URL_RE.findall(text) if not is_allowed(u)]
    assert not bad, f"{path.relative_to(STATIC)} references external URLs: {bad}"


@pytest.mark.parametrize("path", own_files(), ids=lambda p: str(p.relative_to(STATIC)))
def test_no_protocol_relative_or_font_imports(path: Path):
    text = strip_comments(path.read_text(encoding="utf-8"), path.suffix.lower())
    # protocol-relative URLs in src/href/url()/import would hit the internet too
    assert not re.search(r"""(?:src|href)\s*=\s*["']//""", text), "protocol-relative src/href"
    assert not re.search(r"""url\(\s*["']?//""", text), "protocol-relative url()"
    assert not re.search(r"""\bfrom\s+["'](?!\./|\.\./|/)""", text), "bare/remote ES module import"
    assert "@import" not in text, "@import is not allowed (inline or link local CSS)"
    assert "@font-face" not in text, "web fonts are not allowed; use the system font stack"
    for host in ("fonts.googleapis", "fonts.gstatic", "cdn.", "unpkg", "jsdelivr", "cdnjs", "plot.ly"):
        assert host not in text, f"reference to {host}"


def test_index_only_loads_local_assets():
    html = (STATIC / "index.html").read_text()
    for attr in re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html):
        assert not re.match(r"^[a-z]+:|^//", attr, re.I) or attr.startswith(("#", "data:")), attr


def test_plotly_vendored_from_installed_package():
    assert vendor.PLOTLY_JS.is_file(), "run: uv run python -m fermentai.dashboard.vendor"
    head = vendor.PLOTLY_JS.read_bytes()[:200].decode("utf-8", "replace")
    assert "plotly.js" in head
    assert vendor.check() == []


def test_only_plotly_is_vendored():
    vendored = sorted(p.name for p in vendor.VENDOR_DIR.iterdir() if p.is_file())
    assert vendored == ["plotly.min.js"]


def test_our_code_never_uses_plotly_features_that_fetch():
    """plotly.min.js contains URLs only for map tiles / geo topojson / the Chart Studio link.
    Those code paths are unreachable unless we use map/geo traces or enable the cloud link."""
    js = "\n".join(p.read_text() for p in (STATIC / "js").glob("*.js"))
    for trace in ("scattergeo", "choropleth", "scattermap", "densitymap", "mapbox", "topojson", "geo:"):
        assert trace not in js, f"{trace} would make plotly fetch remote map data"
    util = (STATIC / "js" / "util.js").read_text()
    assert "showLink: false" in util and "showSendToCloud: false" in util and "showEditInChartStudio: false" in util


def test_system_fonts_only():
    css = (STATIC / "css" / "dashboard.css").read_text()
    assert "--font: -apple-system" in css
    assert "font-face" not in css


def test_data_snapshots_match_their_sources():
    """static/snapshots/ copies (for data dirs the API does not serve) must not go stale."""
    assert vendor.snapshot_problems() == []


def test_live_cards_omit_missing_values():
    """Lab cards show ONLY sensors that report a value from the current device (null -> no tile, never a
    number); missing sensors are listed once as "not fitted" in the collapsed device drawer. The card badge
    comes from telemetry provenance (measured -> real/physical), and a camera frame from another device is
    never shown."""
    live = (STATIC / "js" / "live.js").read_text()
    util = (STATIC / "js" / "util.js").read_text()
    assert "SENSORS.filter((s) => s.get(tel) !== null)" in live
    assert "not fitted:" in live and "device clock unsynced" in live
    assert "not fermentation evidence" in live and "hs.some((x) => x !== null)" in live   # safety strip only if a heatsink reports
    # camera is hidden behind one flag; the image-from-other-device rule stays for when it returns
    assert "const SHOW_CAMERA = false;" in live and "if (SHOW_CAMERA) s.on(\"image\"" in live
    assert "stale_image_from_other_device" in live and "SHOW_CAMERA ? [[\"starter_height_mm\", \"rise camera\"]]" in live
    assert 's.on("door"' in live and "door-flash" in live
    assert 's.startsWith("measured")' in util
