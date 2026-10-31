"""Display mapping of provenance values -> on-screen vocabulary (MEASURED / COMPUTED / MODELED /
SIMULATED DEVICE / REPLAY / estimated / manual (human)). Display only: stored values are untouched.

Runs the dashboard's own util.js in Node (skipped when Node >= 22 is not installed)."""

import json
import shutil
import subprocess

import pytest

from fermentai.dashboard.vendor import STATIC_DIR

UTIL = STATIC_DIR / "js" / "util.js"

# (stored value, context, expected category, expected label)
CASES = [
    ("measured", "device", "measured", "measured"),
    ("real", "device", "measured", "measured"),               # v1.0 alias of measured
    ("simulated", "device", "simulated_device", "simulated device"),
    ("replay", "device", "replay", "replay"),
    ("simulated", None, "modeled", "modeled"),                # kinetics forecasts, crumb geometry, benchmark
    ("real: ALCHEMI/AIMNet2 on NVIDIA RTX A6000", None, "computed", "computed"),
    ("real (AIMNet2 via nvalchemi-toolkit, remote GPU)", None, "computed", "computed"),
    ({"source": "preform_local_api", "provenance": "estimated"}, None, "computed", "computed"),
    ("alchemi-informed (bounded)", None, "estimated", "estimated"),
    ("assumption (documented coefficients)", None, "estimated", "estimated"),
    ("offline:deterministic_fallback", None, "estimated", "estimated"),
    ("manual", "device", "manual", "manual (human)"),
    ("judge_feedback", None, "manual", "manual (human)"),
    ("imported_human", None, "manual", "manual (human)"),
    ("", None, "unknown", "unknown"),
]


def _node():
    node = shutil.which("node")
    if not node:
        return None
    try:
        major = int(subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().lstrip("v").split(".")[0])
    except (ValueError, subprocess.SubprocessError):
        return None
    return node if major >= 22 else None


@pytest.mark.skipif(_node() is None, reason="needs Node >= 22 to import the ES module")
def test_normprov_display_mapping():
    script = (
        f"const m = await import({json.dumps(UTIL.as_uri())});"
        f"const cases = {json.dumps([[v, c] for v, c, _, _ in CASES])};"
        "console.log(JSON.stringify(cases.map(([v, c]) => { const n = m.normProv(v, c ?? undefined); return [n, m.PROV_LABEL[n]]; })));"
    )
    out = subprocess.run([_node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    for (value, ctx, cat, label), (gcat, glabel) in zip(CASES, got):
        assert (gcat, glabel) == (cat, label), f"{value!r} ctx={ctx}: got {gcat}/{glabel}, want {cat}/{label}"


@pytest.mark.skipif(_node() is None, reason="needs Node >= 22 to import the ES module")
def test_demo_mode_mapping():
    script = (
        f"const m = await import({json.dumps(UTIL.as_uri())});"
        "console.log(JSON.stringify(['measured','real','simulated','replay','estimated'].map((s) => m.demoMode(s))));"
    )
    out = subprocess.run([_node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["live", "live", "simulation", "replay", None]


def test_every_category_has_a_distinct_colour_and_legend_entry():
    css = (STATIC_DIR / "css" / "dashboard.css").read_text()
    html = (STATIC_DIR / "index.html").read_text()
    colours = {}
    for cat in ("measured", "computed", "modeled", "simulated_device", "replay", "estimated", "manual"):
        line = next(l for l in css.splitlines() if l.strip().startswith(f"--prov-{cat}:"))
        colours[cat] = line.split(":", 1)[1].split(";")[0].strip().lower()
        assert f".prov-{cat} " in css, f"missing .prov-{cat} class"
        assert f'class="badge prov-{cat}"' in html, f"legend lacks {cat}"
    assert len(set(colours.values())) == len(colours), colours
    for gone in ("prov-real", "prov-simulated\"", "prov-imported_human", "real · physical"):
        assert gone not in css + html, gone


@pytest.mark.skipif(_node() is None, reason="needs Node >= 22 to import the ES modules")
def test_all_modules_link_in_node():
    """Import every module except the browser entry point (app.js); linking fails on missing exports."""
    mods = [p for p in sorted((STATIC_DIR / "js").glob("*.js")) if p.name != "app.js"]
    script = ";".join(f"await import({json.dumps(p.as_uri())})" for p in mods) + ";console.log('ok')"
    out = subprocess.run([_node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-2000:]
