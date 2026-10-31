# Network setup: offline local-LAN demo

The demo depends on nothing outside one local Wi-Fi network. Four devices are on it:

```
          dedicated travel router / AP  (SSID e.g. "FermentAI-Lab", WPA2, WAN unplugged OK)
          192.168.8.1 (example)
   ┌────────────┬──────────────┬────────────────┬────────────────┐
   │ Mac        │ iPad         │ ESP32 chamber A│ ESP32 chamber B│
   │ 192.168.8.10 (DHCP reservation)            │                │
   │ FastAPI :8000, dashboard, agents, SQLite   │                │
```

## 1. Access point

1. Use a dedicated travel router (for example GL.iNet) or any AP you control. Don't use venue Wi-Fi.
   Venue networks often have client isolation, which blocks iPad→Mac and ESP32→Mac traffic.
2. Use **2.4 GHz with WPA2-PSK**. The ESP32-S3 only supports 2.4 GHz. Use a simple ASCII SSID and
   password, and don't enable WPA3-only mode. You can run 5 GHz alongside for the iPad and Mac.
3. **Turn client/AP isolation off.**
4. Add a **DHCP reservation** for the Mac's Wi-Fi MAC address (for example `192.168.8.10`).
   macOS uses "Private Wi-Fi Address" per network, so either turn it off for this SSID (Wi-Fi ›
   Details › Private Wi-Fi Address: Off) or reserve the private MAC the Mac actually shows on that
   network. Alternatively, set a manual IP on the Mac (System Settings › Network › Wi-Fi ›
   Details › TCP/IP › Configure IPv4: Manually).
5. The router's WAN or uplink can stay unplugged. Nothing in the demo needs it.

## 2. How devices find the Mac

| Client | Address it uses |
|---|---|
| ESP32 A/B | the **static IP** `http://192.168.8.10:8000`, compiled in or stored in NVS. mDNS is optional: `fermentai.local` works with the ESP-IDF mDNS component, but keep the IP as a fallback. |
| iPad | `http://<mac-ip>:8000/?token=<FERMENTAI_API_TOKEN>` as printed by `scripts/start_demo.sh`, or `http://fermentai.local:8000/` |

**mDNS name.** macOS Bonjour advertises `<LocalHostName>.local` automatically. To get
`fermentai.local`, rename the Mac's local hostname:

```bash
sudo scutil --set LocalHostName fermentai     # -> fermentai.local
```

You can also set it in System Settings › General › Sharing › Local hostname. iPadOS resolves
`.local` names natively.

## 3. Start the backend

```bash
cp .env.example .env        # then set a unique FERMENTAI_API_TOKEN
scripts/start_demo.sh                           # real devices
scripts/start_demo.sh --with-simulated-devices  # no hardware: labelled SIMULATED A/B devices
```

The script binds to `0.0.0.0:8000` (`FERMENTAI_HOST` / `FERMENTAI_PORT`) and prints every LAN IP,
the iPad URL and the ESP32 base URL.

### Auth model

- The ESP32s and every write from the dashboard (taste feedback, motor button, setpoint, apply
  experiment, cancel command) send `Authorization: Bearer <FERMENTAI_API_TOKEN>`.
- `GET /api/v1/chambers/{id}/desired-control` also needs the token, because issuing a command is
  a side effect.
- **Read-only GETs are unauthenticated**: health, chamber state, telemetry history, commands log,
  events and SSE stream, predictions, molecular summary, STL and findings. That lets any browser on
  the isolated demo LAN view the dashboard without a login. It's acceptable only because the
  network is a private AP with WPA2. Don't expose port 8000 to an untrusted network.
- The iPad URL carries `?token=…`. The dashboard should read it once, keep it in `localStorage`,
  and send it as the Bearer header on writes.

## 4. macOS firewall

If the macOS Application Firewall is on (System Settings › Network › Firewall), the first
`uvicorn` start triggers an "accept incoming connections" prompt for `python3.12`. Click
**Allow**. If you missed it:

```bash
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$(uv run python -c 'import sys;print(sys.executable)')"
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "$(uv run python -c 'import sys;print(sys.executable)')"
```

Test from another device with `curl http://<mac-ip>:8000/api/v1/health`. Also keep the Mac awake
(`caffeinate -dims` in a spare terminal) and plugged in.

## 5. No-internet guarantees

- The dashboard must load only vendored assets from `src/fermentai/dashboard/static/`: no CDN and
  no Google Fonts. FastAPI's built-in `/docs` page loads Swagger assets from a CDN, so it won't
  render offline. That's expected, and the demo doesn't use it. `/openapi.json` works offline.
- LLM: `FERMENTAI_LLM_MODE=offline` (the default) uses the deterministic fallback, labelled as
  such. If a cloud LLM mode is configured and the internet drops, the taste-feedback endpoint
  falls back to the offline policy, or to a clearly labelled placeholder. It never fails the
  request.
- Brev and GPU: not contacted at runtime. Cached ALCHEMI results are served from `data/alchemi/`.

## 6. Disconnected-mode test procedure (run before the demo)

1. Unplug the router's WAN cable, or disable its upstream/repeater. On the Mac, confirm
   `ping -c1 1.1.1.1` fails and `ping -c1 <router-ip>` succeeds.
2. Mac: `scripts/start_demo.sh` (add `--with-simulated-devices` if the ESP32s aren't there).
3. iPad (joined to the demo SSID only, cellular off): open the printed URL. Confirm:
   - [ ] the dashboard renders fully, with fonts, charts and icons (no CDN failures);
   - [ ] chambers A and B show live telemetry updating (SSE), each labelled with its provenance
     (`real` or `simulated`);
   - [ ] cached predictions, the molecular summary and STL previews load.
4. Submit taste feedback on the iPad, for example "Loaf A was mild and fluffy. Loaf B was more
   sour but a little dense." Confirm:
   - [ ] the interpretation, hypothesis, agent messages and next experiment appear, labelled
     "deterministic fallback" (offline);
   - [ ] a motor command is issued for A and B:
     `curl -s http://127.0.0.1:8000/api/v1/commands?command_type=motor`
     shows each one `accepted`, then `completed`, acked by the real ESP32 or the simulated device;
   - [ ] the chef-hat motors move for ≤ 10 s (hardware), or the simulated-device log shows
     `motor RUN` (software).
5. Motor cancel: press the motor button, then cancel it. Or with curl:
   `curl -X DELETE -H "Authorization: Bearer $FERMENTAI_API_TOKEN" http://127.0.0.1:8000/api/v1/commands/<id>`.
   The motor should stop, and the log should show `cancelled`.
6. Unplug one ESP32 for 2 minutes. Its state should go stale (`telemetry_stale: true`) and its
   unacked commands should be logged `expired_unacked`. Plug it back in: it reconnects and acks
   new commands, and the old motor commands are **not** replayed.
7. Write the result into `docs/validation_status.md` (date, devices, pass/fail per box).

Automated software-only equivalent: `uv run pytest tests/test_api_*.py`
(`test_api_simdevice.py` drives the simulated devices through telemetry, setpoint, motor run and
cancel).
