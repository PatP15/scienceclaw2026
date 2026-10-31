#!/usr/bin/env bash
# FermentAI acceptance run: every automated suite + contract check + offline check, then writes
# docs/test_report.md (counts, durations, environment). PLAN.md §16-17.
#
#   scripts/run_acceptance.sh                 # everything (~3 min on an M-series Mac)
#   scripts/run_acceptance.sh --no-offline    # skip scripts/offline_check.sh
#   scripts/run_acceptance.sh --report PATH   # write the report elsewhere
#
# Suites:
#   unit         uv run pytest tests --ignore=tests/integration
#   devices      uv run pytest simulated_devices/tests
#   integration  uv run pytest tests/integration          (real uvicorn + virtual ESP32s + agents)
#   contract     uv run python -m fermentai.api.contract --check
#   offline      scripts/offline_check.sh                 (0.0.0.0 bind, LAN URL, outbound audit)
#   firmware     not built here (owned by the firmware workstream); the report quotes
#                firmware/build_logs/*.log status lines
# Exit code: 0 only if every suite passed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
REPORT="docs/test_report.md"
RUN_OFFLINE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-offline) RUN_OFFLINE=0; shift ;;
    --report) REPORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

OUT="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/fermentai-acceptance.XXXXXX")" && pwd -P)"
echo "acceptance artifacts: $OUT"
uv sync --quiet >/dev/null 2>&1 || uv sync
RESULTS="$OUT/results.tsv"
: >"$RESULTS"

run() {  # run NAME CMD... ; records name, exit, seconds, log path
  local name="$1"; shift
  echo "=== $name: $*"
  local t0 t1 rc
  t0=$(python3 -c 'import time; print(time.time())' 2>/dev/null || date +%s)
  "$@" >"$OUT/$name.log" 2>&1; rc=$?
  t1=$(python3 -c 'import time; print(time.time())' 2>/dev/null || date +%s)
  local secs; secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
  tail -3 "$OUT/$name.log"
  printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$rc" "$secs" "$OUT/$name.log" "$*" >>"$RESULTS"
  echo "    -> exit $rc in ${secs}s"
}

PYTEST=(uv run pytest -q -p no:cacheprovider -rfEsxX)
run unit        "${PYTEST[@]}" tests --ignore=tests/integration --junitxml="$OUT/unit.xml"
run devices     "${PYTEST[@]}" simulated_devices/tests --junitxml="$OUT/devices.xml"
run integration "${PYTEST[@]}" tests/integration --junitxml="$OUT/integration.xml"
run contract    uv run python -m fermentai.api.contract --check
if [[ "$RUN_OFFLINE" == 1 ]]; then
  run offline   scripts/offline_check.sh --evidence-dir "$OUT/offline_evidence"
fi

uv run python - "$OUT" "$RESULTS" "$REPORT" <<'PY'
import datetime as dt
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from importlib import metadata
from pathlib import Path

out, results, report = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({e})"


rows = []
for line in results.read_text().splitlines():
    name, rc, secs, log, cmd = line.split("\t", 4)
    xml = out / f"{name}.xml"
    counts = ""
    if xml.is_file():
        s = ET.parse(xml).getroot()
        s = s if s.tag == "testsuite" else s.find("testsuite")
        tests, fail, err, skip = (int(s.get(k, 0)) for k in ("tests", "failures", "errors", "skipped"))
        logtxt = Path(log).read_text(errors="replace")
        xfail = len(re.findall(r"^XFAIL ", logtxt, re.M))
        xpass = len(re.findall(r"^XPASS ", logtxt, re.M))
        passed = tests - fail - err - skip
        counts = (f"{passed} passed, {fail} failed, {err} errors, {skip - xfail} skipped, "
                  f"{xfail} xfailed" + (f", {xpass} xpassed" if xpass else ""))
    tail = [ln for ln in Path(log).read_text(errors="replace").splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(tail) if re.search(r"passed|failed|PASS|FAIL|up to date", ln)),
                   tail[-1] if tail else "")
    rows.append((name, int(rc), float(secs), counts, summary.strip(), cmd, log))

fw = []
for f in sorted(Path("firmware/build_logs").glob("*.log")):
    txt = f.read_text(errors="replace").splitlines()
    status = next((ln for ln in reversed(txt) if re.search(r"succeeded|failed|SUCCESS|FAILED|OK", ln)), "")
    mtime = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    fw.append((f.name, status.strip().strip("=").strip(), mtime))

xfails = []
for name, *_ , log in rows:
    for ln in Path(log).read_text(errors="replace").splitlines():
        if ln.startswith(("XFAIL ", "XPASS ", "FAILED ", "ERROR ")):
            xfails.append(f"[{name}] {ln}")

def ver(p):
    try:
        return metadata.version(p)
    except metadata.PackageNotFoundError:
        return "-"

now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
commit = sh("git", "rev-parse", "--short", "HEAD")
dirty = sh("git", "status", "--porcelain")
ok = all(r[1] == 0 for r in rows)
total = sum(r[2] for r in rows)
L = []
L.append("# FermentAI test report\n")
L.append(f"Generated by `scripts/run_acceptance.sh` on {now}. Overall: **{'PASS' if ok else 'FAIL'}** "
         f"({total:.0f} s wall clock).\n")
L.append("This file is regenerated on every run; edit the generator, not the file. "
         "For PLAN §16 item-by-item status see `docs/acceptance_tests.md`.\n")
L.append("## Suites\n")
L.append("| Suite | Result | Duration | Counts | Last line of output |")
L.append("|---|---|---|---|---|")
for name, rc, secs, counts, summary, cmd, log in rows:
    L.append(f"| {name} | {'PASS' if rc == 0 else f'FAIL (exit {rc})'} | {secs:.1f} s | {counts or '-'} | "
             f"`{summary.replace('|', '/')[:140]}` |")
L.append("\nCommands:\n")
for name, rc, secs, counts, summary, cmd, log in rows:
    L.append(f"- **{name}**: `{cmd.replace(str(out) + '/', '<out>/')}`")
if xfails:
    L.append("\n## Expected failures / failures\n")
    L.append("Each XFAIL is a known bug documented in the test's reason (see `docs/STATUS.md` → Known bugs).\n")
    L.extend(f"- {x}" for x in xfails)
L.append("\n## Firmware build logs (not rebuilt by this script)\n")
L.append("| Log | Last status line | Modified |")
L.append("|---|---|---|")
for n, s, m in fw:
    L.append(f"| `firmware/build_logs/{n}` | {s.replace('|', '/')[:100] or '-'} | {m} |")
L.append("\n## Environment\n")
L.append(f"- Host: {platform.node()} — {platform.platform()} ({platform.machine()})")
L.append(f"- Python: {platform.python_version()} ({sys.executable.replace(str(Path.cwd()), '.')}); "
         f"uv: {sh('uv', '--version')}")
L.append(f"- Git: `{commit}`" + (f" + {len(dirty.splitlines())} uncommitted path(s)" if dirty else " (clean)"))
L.append("- Packages: " + ", ".join(f"{p} {ver(p)}" for p in (
    "fastapi", "uvicorn", "pydantic", "httpx", "numpy", "scipy", "pandas", "plotly", "trimesh",
    "pytest")))
L.append(f"- LLM mode during tests: offline (FERMENTAI_LLM_MODE unset/offline); no GPU, no Brev, "
         f"no internet required.")
L.append(f"- Raw logs and junit XML of this run: `{out}` (temporary).")
report.write_text("\n".join(L) + "\n")
print(f"wrote {report}")
sys.exit(0 if ok else 1)
PY
rc=$?
echo "report: $REPORT (exit $rc)"
exit $rc
