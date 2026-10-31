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
