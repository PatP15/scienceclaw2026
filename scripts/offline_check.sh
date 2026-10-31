#!/usr/bin/env bash
# FermentAI disconnected-mode check (PLAN.md §8, PLAN_MASTER.md §12).
#
# AUTOMATED PART (this script, software-only, ~30 s):
#   1. starts the backend bound to 0.0.0.0 (like scripts/start_demo.sh) under an outbound-network
#      audit hook (tests/integration/net_audit.py: PEP 578 hook on getaddrinfo/connect/sendto,
#      asyncio loop so no socket bypasses the hook), with a temp DB / findings log / data dir;
#   2. starts the two labelled SIMULATED chambers (python -m simulated_devices);
#   3. reaches the server through this Mac's LAN address (stand-in for the iPad) and loads the
#      dashboard + cached NVIDIA / prediction / STL endpoints;
#   4. runs the judge flow with curl: POST /taste-feedback -> waits until both motor commands
#      are acked accepted -> completed by the virtual devices (`ran` = true);
#   5. evidence: `lsof` of the server's sockets (every remote endpoint must be loopback/private),
#      a best-effort `nettop` snapshot, and the audit log; FAILS if the server process made any
#      DNS lookup or connection to a non-private address during the run.
#   The check is valid with or without internet: it proves the server never TRIED to go out.
#
#   scripts/offline_check.sh [--port N] [--evidence-dir DIR] [--keep-running]
#   exit 0 = PASS, 1 = FAIL
#
# PHYSICAL PART (manual, before the demo; record the result in docs/acceptance_tests.md):
#   a. Travel router: unplug the WAN cable / disable repeater uplink. Mac + iPad join ONLY the demo
#      SSID; iPad cellular OFF. On the Mac: `ping -c1 -t2 1.1.1.1` must FAIL and
#      `ping -c1 <router-ip>` must succeed. (No travel router: Mac Wi-Fi + a second AP you
#      control; never rely on an untested hotspot -- verify iPad->Mac client-to-client traffic.)
#   b. Run this script once (it must PASS with the WAN off too).
#   c. `scripts/start_demo.sh --with-simulated-devices` (or without the flag when the ESP32s are on).
#   d. On the iPad open the printed http://<mac-ip>:8000/?token=... URL. Check: all four tabs render
#      (fonts, charts, icons), A and B update live with SIMULATED/REAL badges, NVIDIA tab shows
#      the ALCHEMI summary, Digital Twin shows both predictions.
#   e. Submit the PLAN_MASTER §14 sentence on the Taste tab. Check: preference list, two agent
#      messages, next experiment, "Offline Policy" label; on the Mac
#      `curl -s http://127.0.0.1:8000/api/v1/commands?command_type=motor` shows both motor commands
#      accepted -> completed (hardware: chef hats move <= 10 s).
#   f. Write date, router model, devices (real/simulated) and pass/fail per step into
#      docs/acceptance_tests.md ("Physical disconnected-mode test" section).
set -uo pipefail   # no -e: every step is checked and reported explicitly

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT=""
EVIDENCE=""
KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE="$2"; shift 2 ;;
    --keep-running) KEEP=1; shift ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

PY="$(uv run python -c 'import sys; print(sys.executable)')"
[[ -n "$PORT" ]] || PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fermentai-offline.XXXXXX")"
EVIDENCE="${EVIDENCE:-$WORK/evidence}"
mkdir -p "$EVIDENCE" "$WORK/data"
for d in predictions alchemi stl demos benchmarks figures; do [[ -e "$ROOT/data/$d" ]] && ln -s "$ROOT/data/$d" "$WORK/data/$d"; done

TOKEN="offline-check-$RANDOM$RANDOM"
export FERMENTAI_API_TOKEN="$TOKEN" FERMENTAI_DEVICE_TOKEN_A="" FERMENTAI_DEVICE_TOKEN_B=""
export FERMENTAI_DB_PATH="$WORK/fermentai.sqlite3" FERMENTAI_DATA_DIR="$WORK/data"
export FERMENTAI_FINDINGS_PATH="$WORK/findings_log.jsonl" FERMENTAI_FINDINGS_LOG="$WORK/findings_log.jsonl"
export FERMENTAI_LLM_MODE=offline FERMENTAI_PORT="$PORT" PYTHONUNBUFFERED=1
unset ANTHROPIC_API_KEY || true
BASE="http://127.0.0.1:$PORT"
AUDIT="$EVIDENCE/net_audit.jsonl"
FAIL=0
SERVER_PID="" DEV_PID=""

note() { printf '%s\n' "$*" | tee -a "$EVIDENCE/summary.txt"; }
check() { if [[ "$1" == 0 ]]; then note "  PASS  $2"; else note "  FAIL  $2"; FAIL=1; fi; }
jq_py() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
cleanup() {
  [[ -n "$DEV_PID" ]] && kill "$DEV_PID" 2>/dev/null || true
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

note "FermentAI offline check  $(date -u +%Y-%m-%dT%H:%M:%SZ)  host=$(hostname -s)  port=$PORT"
note "evidence: $EVIDENCE"
if ping -c1 -t2 1.1.1.1 >/dev/null 2>&1; then
  note "  info  internet (1.1.1.1) is reachable from this Mac; the audit still proves the server never used it"
else
  note "  info  internet (1.1.1.1) NOT reachable: running truly disconnected"
fi

# 1. server on 0.0.0.0 under the audit hook --------------------------------------------------
"$PY" -m tests.integration.net_audit --log "$AUDIT" --host 0.0.0.0 --port "$PORT" \
  >"$EVIDENCE/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 120); do curl -fsS "$BASE/api/v1/health" >/dev/null 2>&1 && break; sleep 0.25; done
curl -fsS "$BASE/api/v1/health" >"$EVIDENCE/health.json"; check $? "server healthy on $BASE (pid $SERVER_PID)"
lsof -nP -a -p "$SERVER_PID" -iTCP -sTCP:LISTEN >"$EVIDENCE/lsof_listen.txt" 2>&1 || true
grep -q "\*:$PORT (LISTEN)" "$EVIDENCE/lsof_listen.txt"; check $? "listening on *:$PORT (all interfaces, LAN-reachable)"

# 2. virtual chambers ---------------------------------------------------------------------------
"$PY" -m simulated_devices --chambers A B --base-url "$BASE" --interval 1 --poll-interval 1 \
  --no-images >"$EVIDENCE/devices.log" 2>&1 &
DEV_PID=$!
ok=1
for _ in $(seq 1 120); do
  if curl -fsS "$BASE/api/v1/chambers" | jq_py 'int(all(d[c]["latest_telemetry"] for c in "AB"))' \
       2>/dev/null | grep -q 1; then ok=0; break; fi
  sleep 0.25
done
check $ok "telemetry from simulated chambers A and B"
curl -fsS "$BASE/api/v1/chambers" | jq_py "' '.join(c+':'+str(d[c]['provenance'])+'@'+str(d[c]['desired_setpoint_c'])+'C' for c in 'AB')" \
  | sed 's/^/  info  provenance@setpoint /' | tee -a "$EVIDENCE/summary.txt"

# 3. LAN address (iPad stand-in) + cached artifacts + dashboard -------------------------------
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [[ -n "$LAN_IP" ]]; then
  curl -fsS -m 5 "http://$LAN_IP:$PORT/api/v1/health" >/dev/null; check $? "reachable via LAN address http://$LAN_IP:$PORT"
  curl -fsS -m 5 "http://$LAN_IP:$PORT/" | grep -q "<title>"; check $? "dashboard index served over the LAN address"
else
  note "  info  no en0/en1 address (not on Wi-Fi?); LAN reachability not checked"
fi
for p in /api/v1/molecular/summary /api/v1/predictions /api/v1/stl /api/v1/demos /js/app.js /vendor/plotly.min.js; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE$p")"; [[ "$code" == 200 ]]; check $? "GET $p -> $code"
done

# 4. judge flow with curl -------------------------------------------------------------------------
SENTENCE="Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, but the crumb is too dense. I'd like a more sour loaf that's still fluffy."
curl -fsS -X POST "$BASE/api/v1/taste-feedback" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$("$PY" -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$SENTENCE")" \
  >"$EVIDENCE/taste_feedback_response.json"
check $? "POST /taste-feedback"
jq_py '"; ".join(d["result"]["sensory_interpretation"]["structured_preference"])' <"$EVIDENCE/taste_feedback_response.json" \
  | sed 's/^/  info  preference: /' | tee -a "$EVIDENCE/summary.txt"
jq_py 'd["result"]["provenance"]["decision_source"] + " / " + d["result"]["next_experiment"]["title"]' \
  <"$EVIDENCE/taste_feedback_response.json" | sed 's/^/  info  decision: /' | tee -a "$EVIDENCE/summary.txt"
IDS="$(jq_py '" ".join(m["command_id"] for m in d["motor_commands"])' <"$EVIDENCE/taste_feedback_response.json")"
ok=1
for _ in $(seq 1 120); do
  if curl -fsS "$BASE/api/v1/commands?command_type=motor" \
       | jq_py "int(all(any(c['command_id']==i and c['ran'] for c in d) for i in '$IDS'.split()))" | grep -q 1; then
    ok=0; break; fi
  sleep 0.25
done
check $ok "motor commands for A and B acked accepted -> completed (ran=true)"
curl -fsS "$BASE/api/v1/commands?command_type=motor" >"$EVIDENCE/motor_commands.json"

# 5. network evidence -------------------------------------------------------------------------------
lsof -nP -a -p "$SERVER_PID" -i >"$EVIDENCE/lsof_server_sockets.txt" 2>&1 || true
EXT="$(awk 'NR>1 && /->/ {split($9,a,"->"); print a[2]}' "$EVIDENCE/lsof_server_sockets.txt" \
       | sed -E 's/:[0-9]+$//; s/^\[//; s/\]$//' | sort -u \
       | "$PY" -c 'import sys,ipaddress
bad=[h for h in sys.stdin.read().split() if not (lambda ip: ip.is_private or ip.is_loopback or ip.is_link_local)(ipaddress.ip_address(h))]
print(" ".join(bad))')"
[[ -z "$EXT" ]]; check $? "lsof: every server socket peer is loopback/private ${EXT:+(external: $EXT)}"
if command -v nettop >/dev/null 2>&1; then
  (nettop -P -L 1 -p "$SERVER_PID" >"$EVIDENCE/nettop.txt" 2>&1 &) ; sleep 2; pkill -f "nettop -P -L 1 -p $SERVER_PID" 2>/dev/null || true
  note "  info  nettop snapshot -> $EVIDENCE/nettop.txt (best effort)"
fi

if [[ "$KEEP" == 1 ]]; then
  note "  info  --keep-running: server $BASE (token $TOKEN). Ctrl-C to stop."
  wait "$SERVER_PID" || true
fi
kill "$DEV_PID" 2>/dev/null || true; DEV_PID=""
kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""

"$PY" -m tests.integration.net_audit --check "$AUDIT" >"$EVIDENCE/net_audit_summary.json"
check $? "audit hook: no DNS lookups / connects to non-private hosts ($(jq_py 'd["events"]' <"$EVIDENCE/net_audit_summary.json") socket events logged)"

if [[ "$FAIL" == 0 ]]; then note "RESULT: PASS (offline check)"; else note "RESULT: FAIL (see $EVIDENCE)"; fi
exit "$FAIL"
