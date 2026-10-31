"""The FastAPI app serves the dashboard at / and every asset it references resolves locally;
every API path the dashboard JS calls exists on the real router."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import pytest
from fastapi.testclient import TestClient

from fermentai.api.testing import auth, make_app, telemetry
from fermentai.dashboard.vendor import STATIC_DIR

IMPORT_RE = re.compile(r"""(?:^|[;\s])(?:import|export)\s[^"'`;]*?from\s*["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)""", re.M)
API_LITERAL_RE = re.compile(r"""[`"'](/api/v1/[^`"']*)[`"']""")


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path, static_dir=STATIC_DIR)


@pytest.fixture
def client(app):
    return TestClient(app)


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("src", "href") and v and not v.startswith(("#", "data:", "mailto:")):
                self.refs.append(v)


def test_root_serves_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>FermentAI Lab Dashboard</title>" in r.text
    assert "What did the bread taste like?" in r.text
    for section in ("Live Laboratories", "Digital Twin", "NVIDIA Molecular Explorer"):
        assert section in r.text


def collect_assets(client):
    """Crawl index.html refs + ES-module imports; return {url: response}."""
    index = client.get("/").text
    p = _Refs()
    p.feed(index)
    todo = [urljoin("/", ref) for ref in p.refs]
    seen = {}
    while todo:
        url = todo.pop()
        path = urlparse(url).path
        if path in seen:
            continue
        r = client.get(path)
        seen[path] = r
        if path.endswith(".js") and r.status_code == 200 and "vendor/" not in path:
            for a, b in IMPORT_RE.findall(r.text):
                todo.append(urljoin(path, a or b))
    return seen


def test_every_referenced_asset_resolves(client):
    assets = collect_assets(client)
    assert "/vendor/plotly.min.js" in assets and "/js/app.js" in assets and "/css/dashboard.css" in assets
    for mod in ("api", "util", "live", "twin", "molecular", "taste", "messages"):
        assert f"/js/{mod}.js" in assets, f"{mod}.js not reachable from index.html imports"
    bad = {u: r.status_code for u, r in assets.items() if r.status_code != 200}
    assert not bad, bad


def test_module_scripts_have_javascript_mime(client):
    # iPad Safari refuses ES modules served with a non-JavaScript MIME type.
    for path, r in collect_assets(client).items():
        if path.endswith(".js"):
            assert "javascript" in r.headers["content-type"], (path, r.headers["content-type"])
        if path.endswith(".css"):
            assert r.headers["content-type"].startswith("text/css")


def test_all_static_files_are_reachable(client):
    for p in STATIC_DIR.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            rel = p.relative_to(STATIC_DIR).as_posix()
            assert client.get(f"/{rel}").status_code == 200, rel


def _api_paths_in_js():
    out = set()
    for p in (STATIC_DIR / "js").glob("*.js"):
        for lit in API_LITERAL_RE.findall(p.read_text()):
            path = lit.split("?")[0]
            if path.endswith("/"):  # URL prefix constant, e.g. FILES = "/api/v1/molecular/files/"
                path += "X"
            path = re.sub(r"/\$\{[^}]*\}(?=/|$)", "/X", path)  # whole-segment params -> placeholder
            path = re.sub(r"\$\{[^}]*\}", "", path)  # query-string suffixes like ${qs}
            out.add(path)
    return sorted(out)


def test_dashboard_api_calls_match_real_routes(app):
    """Contract check: every /api/v1/... literal used by the dashboard JS matches a real route."""
    # Use the OpenAPI path table (works however the router is mounted/wrapped).
    templates = list(app.openapi()["paths"])
    regexes = [re.compile("^" + re.sub(r"\{[^}:]+:path\}", ".+", re.sub(r"\{[^}:]+\}", "[^/]+", t)) + "$")
               for t in templates]
    assert any(rx.match("/api/v1/health") for rx in regexes)
    paths = _api_paths_in_js()
    assert "/api/v1/taste-feedback" in paths and "/api/v1/events/stream" in paths
    missing = [p for p in paths if not any(rx.match(p) for rx in regexes)]
    # Contracted routes that may not be deployed yet. The dashboard must detect them at runtime
    # (via /openapi.json) and degrade gracefully; drop an entry once the API ships the route.
    optional = {"/api/v1/chat", "/api/v1/chat/X"}
    chat_js = (STATIC_DIR / "js" / "chat.js").read_text()
    assert 'apiPaths.has("/api/v1/chat")' in chat_js and "Chat not available yet" in chat_js
    missing = [p for p in missing if p not in optional]
    assert not missing, f"dashboard calls endpoints that do not exist: {missing}"


def test_api_reads_used_by_dashboard_work_unauthenticated(client):
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("A", source="simulated"))
    for path in ("/api/v1/health", "/api/v1/chambers/A/state", "/api/v1/chambers/A/telemetry?limit=10",
                 "/api/v1/agents/all/messages?limit=5", "/api/v1/experiments?limit=8",
                 "/api/v1/taste-feedback?limit=1", "/api/v1/predictions", "/api/v1/molecular/summary",
                 "/api/v1/stl", "/api/v1/commands?command_type=motor&limit=5", "/api/v1/demos",
                 "/api/v1/agents/A/state", "/api/v1/agents/B/state"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_sse_stream_named_events_shape(client):
    client.post("/api/v1/telemetry", headers=auth(), json=telemetry("B", source="simulated"))
    with client.stream("GET", "/api/v1/events/stream?since=0&max_events=1&timeout_s=2") as r:
        body = "".join(r.iter_text())
    assert "event: telemetry" in body and '"chamber_id":"B"' in body


def test_benchmark_snapshot_served(client):
    r = client.get("/snapshots/benchmarks/SNAPSHOT.json")
    assert r.status_code == 200
    for name in r.json()["files"]:
        assert client.get(f"/snapshots/benchmarks/{name}").status_code == 200


def test_replay_examples_come_from_api_not_bundled():
    js = "\n".join(p.read_text() for p in (STATIC_DIR / "js").glob("*.js"))
    assert "/api/v1/demos" in js
    assert not (STATIC_DIR / "js" / "examples.js").exists()


NAMED_IMPORT_RE = re.compile(r"""import\s*\{([^}]*)\}\s*from\s*["'](\.{1,2}/[^"']+)["']""")
EXPORT_RE = re.compile(r"""^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)""", re.M)


def test_every_named_import_is_exported():
    """A missing export only fails at module-link time in the browser (blank page), so check statically."""
    js_dir = STATIC_DIR / "js"
    exports = {p.name: set(EXPORT_RE.findall(p.read_text())) for p in js_dir.glob("*.js")}
    problems = []
    for p in js_dir.glob("*.js"):
        for names, target in NAMED_IMPORT_RE.findall(p.read_text()):
            tgt = (p.parent / target).resolve()
            if tgt.name not in exports:
                problems.append(f"{p.name}: imports missing module {target}")
                continue
            for n in (x.strip().split(" as ")[0].strip() for x in names.split(",")):
                if n and n not in exports[tgt.name]:
                    problems.append(f"{p.name}: '{n}' is not exported by {tgt.name}")
    assert not problems, problems
