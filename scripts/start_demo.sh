#!/usr/bin/env bash
# Start the FermentAI backend + dashboard on the LAN (no internet required).
#
#   scripts/start_demo.sh                           # backend only
#   scripts/start_demo.sh --with-simulated-devices  # + labelled SIMULATED ESP32s for A and B
#   scripts/start_demo.sh --with-simulated-devices --sim-images
#   scripts/start_demo.sh --with-serial-bridge      # + USB-serial bridge for ESP32s on USB
#   scripts/start_demo.sh --with-serial-bridge --serial-port /dev/cu.usbmodem2101=A --serial-port /dev/cu.usbmodem2201=B
#       (no --serial-port: auto-discover /dev/cu.usbmodem*; protocol contracts/serial_v1.md)
#
# Env (from .env if present): FERMENTAI_HOST (0.0.0.0), FERMENTAI_PORT (8000),
# FERMENTAI_API_TOKEN, FERMENTAI_DB_PATH. See docs/network_setup.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_SIM=0
WITH_BRIDGE=0
SIM_ARGS=()
BRIDGE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-simulated-devices) WITH_SIM=1 ;;
    --sim-images) SIM_ARGS+=(--images) ;;
    --with-serial-bridge) WITH_BRIDGE=1 ;;
    --serial-port) shift; [[ $# -gt 0 ]] || { echo "--serial-port needs PATH[=CHAMBER]" >&2; exit 2; }
                   BRIDGE_ARGS+=(--port "$1") ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ -f .env ]]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi
HOST="${FERMENTAI_HOST:-0.0.0.0}"
PORT="${FERMENTAI_PORT:-8000}"
export FERMENTAI_API_TOKEN="${FERMENTAI_API_TOKEN:-change-me-local-device-token}"

if [[ "$FERMENTAI_API_TOKEN" == "change-me-local-device-token" ]]; then
  echo "WARNING: using the default device token. Set FERMENTAI_API_TOKEN in .env before the demo."
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $PORT is already in use (another server running?)." >&2
  exit 1
fi

# --- LAN addresses ------------------------------------------------------------------------
IPS=()
while read -r ip; do [[ -n "$ip" ]] && IPS+=("$ip"); done < <(
  ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2}')
LOCAL_HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"

echo "=================================================================="
echo " FermentAI local backend"
echo "  bind:        http://$HOST:$PORT"
echo "  this Mac:    http://127.0.0.1:$PORT/"
for ip in "${IPS[@]}"; do
  echo "  iPad (LAN):  http://$ip:$PORT/?token=$FERMENTAI_API_TOKEN"
done
echo "  mDNS:        http://$LOCAL_HOST.local:$PORT/   (Bonjour; rename via System Settings > General > Sharing)"
echo "  ESP32 base:  http://${IPS[0]:-<mac-ip>}:$PORT  (Authorization: Bearer \$FERMENTAI_API_TOKEN)"
echo "  health:      http://127.0.0.1:$PORT/api/v1/health"
echo "  The ?token= in the iPad URL lets the dashboard submit feedback / motor commands."
echo "=================================================================="

SIM_PID=""
BRIDGE_PID=""
cleanup() {
  if [[ -n "$SIM_PID" ]]; then kill "$SIM_PID" 2>/dev/null || true; fi
  if [[ -n "$BRIDGE_PID" ]]; then kill "$BRIDGE_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

if [[ "$WITH_SIM" == 1 ]]; then
  (
    # wait for the server, then start the SIMULATED devices (source="simulated")
    for _ in $(seq 1 60); do
      curl -fsS "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1 && break
      sleep 0.5
    done
    exec uv run python scripts/simulate_device.py --base-url "http://127.0.0.1:$PORT" ${SIM_ARGS[@]+"${SIM_ARGS[@]}"}
  ) &
  SIM_PID=$!
  echo "Started SIMULATED devices for A and B (pid $SIM_PID)."
fi

if [[ "$WITH_BRIDGE" == 1 ]]; then
  (
    # wait for the server, then attach the USB-serial bridge (status: /api/v1/bridge/status)
    for _ in $(seq 1 60); do
      curl -fsS "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1 && break
      sleep 0.5
    done
    exec uv run python -m fermentai.bridge --base-url "http://127.0.0.1:$PORT" ${BRIDGE_ARGS[@]+"${BRIDGE_ARGS[@]}"}
  ) &
  BRIDGE_PID=$!
  echo "Started USB-serial bridge (pid $BRIDGE_PID) ${BRIDGE_ARGS[*]:-(auto-discovering /dev/cu.usbmodem*)}."
  echo "  Close any serial monitor on those ports first; status: http://127.0.0.1:$PORT/api/v1/bridge/status"
fi

uv run uvicorn fermentai.api.app:app --host "$HOST" --port "$PORT" \
  --timeout-graceful-shutdown 3 --log-level info
