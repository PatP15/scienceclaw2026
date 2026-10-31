"""Crawl the dashboard over real HTTP and scan every served asset for external URLs.

Reuses the dashboard workstream's own URL scanner (tests/test_dashboard_offline.py) when it is
importable, so both suites apply the same rule; otherwise falls back to an equivalent copy.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

try:  # the dashboard's own scanner (same rules as tests/test_dashboard_offline.py)
    from tests.test_dashboard_offline import ABS_URL_RE, is_allowed, strip_comments
    SCANNER = "tests.test_dashboard_offline"
except Exception:  # noqa: BLE001 - pragma: no cover - fallback copy
    SCANNER = "tests.integration.dashboard_scan (fallback copy)"
    ABS_URL_RE = re.compile(r"\b(?:https?|wss?|ftp)://[^\s\"'`)<>]+", re.IGNORECASE)
    _LOCAL = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")
    _NS = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/2000/xmlns/")

    def strip_comments(text: str, suffix: str) -> str:
        if suffix in (".html", ".svg"):
            text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        if suffix in (".css", ".js", ".mjs", ".html"):
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        if suffix in (".js", ".mjs"):
            text = re.sub(r"(?m)(?<![:\"'`\\w])//.*$", "", text)
        return text

    def is_allowed(url: str) -> bool:
        if url.startswith(_NS):
            return True
        host = re.sub(r"^[a-z]+://", "", url, flags=re.IGNORECASE).split("/")[0].split(":")[0]
        return host in _LOCAL

IMPORT_RE = re.compile(r"""(?:^|[;\s])(?:import|export)\s[^"'`;]*?from\s*["']([^"']+)["']"""
                       r"""|import\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s*["']([^"']+)["']""", re.MULTILINE)
CSS_URL_RE = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("src", "href") and v and not v.startswith(("#", "data:", "mailto:", "javascript:")):
                self.refs.append(v)


def crawl(client: httpx.Client) -> dict[str, httpx.Response]:
    """GET / then every src/href, ES-module import and CSS url() reachable from it (same origin)."""
    index = client.get("/")
    seen: dict[str, httpx.Response] = {"/": index}
    p = _Refs()
    p.feed(index.text)
    todo = [urljoin("/", r) for r in p.refs]
    while todo:
        url = todo.pop()
        parsed = urlparse(url)
        if parsed.scheme or parsed.netloc:   # absolute URL -> reported by the scanner, not fetched
            seen.setdefault(url, None)  # type: ignore[arg-type]
            continue
        path = parsed.path
        if path in seen:
            continue
        r = client.get(path)
        seen[path] = r
        if r.status_code != 200 or "/vendor/" in path:
            continue
        if path.endswith((".js", ".mjs")):
            for groups in IMPORT_RE.findall(r.text):
                ref = next((g for g in groups if g), None)
                if ref:
                    todo.append(urljoin(path, ref))
        elif path.endswith(".css"):
            for ref in CSS_URL_RE.findall(r.text):
                if not ref.startswith("data:"):
                    todo.append(urljoin(path, ref))
    return seen


def external_urls(path: str, text: str) -> list[str]:
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ".html"
    return [u for u in ABS_URL_RE.findall(strip_comments(text, suffix)) if not is_allowed(u)]
