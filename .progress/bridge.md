## For the hardware session
1. While the bridge holds /dev/cu.usbmodem2101, motor tests go through the backend: `curl -X POST -H "Authorization: Bearer $FERMENTAI_API_TOKEN" -H 'Content-Type: application/json' -d '{"duration_s":3,"pattern":"wiggle"}' http://127.0.0.1:8000/api/v1/chambers/A/motor`. Verified on the board: console "OK running 3000 ms" -> ack accepted -> completed (inferred after 3 s).
2. fw 0.3.0 console: the bridge only ever writes `motor test <ms>` (cap 10000) and `setpoint <C>` (API-clamped; console "OK setpoint in force: X C"). It NEVER arms/disarms anything and never sends time/ping/JSON.
3. Setpoints DO reach the controller via `setpoint <C>`, but with armed(peltier=0) the ack is "rejected: Peltier disarmed on the device; setpoint stored locally" (verified on the board: 22.00 C stored). Arming stays a manual console step. control_mode idle/hold has no console command (needs the serial build).
4. Watch: `tail -f data/telemetry/bridge.log` ("legacy console: ...", "console reply: ...") and `GET /api/v1/commands?chamber_id=A`.
5. Stop the bridge to get the port back: `pkill -f "fermentai.bridge --port"`; restart: `nohup uv run python -m fermentai.bridge --port /dev/cu.usbmodem2101=A --base-url http://127.0.0.1:8000 > data/telemetry/bridge.log 2>&1 &`

# USB-serial bridge progress checkpoint
Owner scope: contracts/serial_v1.md (+contracts/README.md), src/fermentai/bridge/, scripts/serial_bridge.py,
scripts/start_demo.sh (--with-serial-bridge), simulated_devices/transport_serial.py (+ --transport serial
--port option only), tests/test_bridge_*.py. NEVER touch firmware/ or open /dev/cu.usbmodem2101; tests use pty.
## Status: COMPLETE
## Done so far
- src/fermentai/bridge/{protocol,serial_link,bridge,cli,testing,__init__,__main__}.py; scripts/serial_bridge.py
- simulated_devices/transport_serial.py + cli --transport serial --port
- contracts/serial_v1.md + contracts/README.md "Transports"; API GET /api/v1/bridge/status
- tests/test_bridge_protocol.py (15 pass), tests/test_bridge_e2e.py (4 pass)
- Gotcha fixed: tty.setraw default TCSAFLUSH deadlocks the loop -> TCSANOW.
## Next
- tests/test_bridge_device.py (raw firmware lines: measured source, tokens, conflict, logs-only,
  outbox, discovery/path reconnect, record/replay, status API stale, CLI config), start_demo.sh flag

## Final
- tests/test_bridge_device.py (9 pass): measured labelling + device token/X-Device-Id, NaN fix, ping/pong,
  commands once + resend after reboot + motor ran, conflict/mismatch/logs_only, outbox order on 503,
  discovery + path reconnect (symlinked pty), image chunks, record/replay, status API stale, CLI config.
- scripts/start_demo.sh --with-serial-bridge [--serial-port PATH=CH ...]
- Totals: bridge 28 tests; api+bridge+simulated_devices 165 passed; full repo 511 passed, 1 skipped, 1 xfailed.
- Live smoke: uvicorn + bridge CLI (explicit pty port) + VirtualChamber on pty master: telemetry, motor
  completed/ran, bridge_status "USB · connected".
- No new deps (pyserial NOT in venv; termios link used). Never opened /dev/cu.usbmodem2101; firmware/ untouched.
## Resume
uv run pytest tests/test_bridge_*.py ; protocol in contracts/serial_v1.md
## Legacy status-log converter (fw 0.3.0) — DONE
- src/fermentai/bridge/status_log.py + PortSession integration (--parse-status-log default on); legacy ports get
  no commands/pings/periodic time (console would print "ERR unknown command"); tests/test_bridge_status_log.py (7).
- REAL RUN: bridge running in background (nohup) on /dev/cu.usbmodem2101=A -> http://127.0.0.1:8000,
  log data/telemetry/bridge.log; measured chamber ~28-29 C every 30 s. Stop: pkill -f "fermentai.bridge --port".
- Running backend predates /api/v1/bridge/status (needs backend restart to expose it; file is written anyway).
## NEXT: chat endpoint (src/fermentai/api/chat.py written; routes + tests pending)

## Console command mapping + thinking pulses + state filtering — DONE
- bridge: legacy console motor test / setpoint mapping, reply parsing, busy queue, stop handling, no time/ping
  before serial_v1 hello, setpoint reject cache; tests/test_bridge_legacy_console.py (8)
- api: rejected/failed identical setpoint not re-issued every poll; thinking pulses (api/thinking.py,
  hardware.yaml thinking_pulses); latest_image device/provenance match + stale_image_from_other_device;
  device registry active/inactive; tests test_api_thinking.py (5), test_api_state_devices.py (2)
- REAL BOARD: motor 3 s round trip OK (x2); setpoint 22.00 -> "OK setpoint in force" -> rejected (Peltier disarmed).
- Bridge left running (nohup) on /dev/cu.usbmodem2101=A.
