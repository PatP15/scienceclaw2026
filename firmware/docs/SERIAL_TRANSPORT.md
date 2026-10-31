# USB-serial transport (serial_v1)

The ESP32 can talk to the Mac over the USB cable instead of Wi-Fi. The payloads are the **same**
contract objects: `telemetry_v1`, `acknowledgment_v1` and `command_v1`. Only the framing changes.
The canonical protocol document is `contracts/serial_v1.md`, owned by the backend. Until it lands,
§1 below is the definition the firmware implements.

Code (all new files):

| File | Role |
|---|---|
| `src/network/SerialLink.h` | `ISerialPort`; `ArduinoStreamPort` wraps any Arduino `Stream` (`Serial`, `Serial1`, `USBSerial`) |
| `src/network/SerialTransport.h/.cpp` | the protocol, implemented as an `IHttpTransport` (drop-in for `NetworkManager`) |
| `test/test_serial/test_main.cpp` | 19 native tests through the unchanged TelemetryClient / CommandClient / CommandProcessor |
| `platformio_serial.ini` | envs `esp32s3cam_a_serial`, `esp32s3cam_b_serial` (`-DFERMENTAI_TRANSPORT_SERIAL=1`) |

## 1. Protocol (serial_v1, interim definition)

- 115200 baud, 8N1. UTF-8 newline-delimited JSON: **one object per line**, at most **8192 bytes
  including `\n`**. `\r\n` is accepted.
- Every line is `{"type": "...", "v": "1.0", ...}`. A `v` whose major version isn't 1 is ignored.
- Lines that don't start with `{`, can't be parsed, or have an unknown `type` are ignored and
  counted. Both sides must tolerate that, because boot banners and ROM output share the port.
- A line longer than 8192 bytes is dropped up to its `\n`, and the device emits a `log` warn.

### Device → host

| type | fields | when |
|---|---|---|
| `hello` | `device_id, chamber_id, firmware_version, boot_id, capabilities[], uptime_ms` | at boot, then every 30 s until `hello_ack` |
| `telemetry` | `payload`: telemetry_v1 object (byte-identical to the HTTP body, same `sequence_number`/`boot_id` dedup) | every 10 s (queued before `hello_ack`, ≤ 50) |
| `ack` | `payload`: acknowledgment_v1 object | per command, as over HTTP |
| `log` | `level` (`debug\|info\|warn\|error`), `msg` (≤ 240 chars) | firmware diagnostics |
| `pong` | `uptime_ms`, `id` (echoed if the ping had one) | reply to `ping` |

`capabilities`: `telemetry, ack, command, time, ping, log, set_setpoint, animate_motor,
stop_motor, noop`. `capture_image` is **not** offered: images are not carried over serial_v1,
and serial builds compile the camera out.

### Host → device

| type | fields | effect |
|---|---|---|
| `hello_ack` | `server_time_utc`, `chamber_id`, `poll_interval_s` | link up; clock set (`timestamp_quality: "synced"`). A `chamber_id` mismatch is ignored and a `log` error is emitted. |
| `command` | `payload`: command_v1 object; optional `server_time_utc` | queued, then executed by the existing CommandProcessor exactly like a `commands[]` entry of desired-control (local clamp, replay ring, sequence ≤ highest ignored, expiry) |
| `time` | `server_time_utc` | sets the clock |
| `ping` | optional `id` | the device answers `pong` |

**Command expiry.** Remaining validity is fixed **at receipt of the line**:
`expires_at_utc − server_time_utc`. The server time comes from the `command` message if present,
otherwise from the clock set by `hello_ack`/`time`. If neither exists, the command keeps only its
absolute expiry and is rejected unless the device clock is synced (same rule as HTTP). Time
spent queued between receipt and execution is subtracted. The host should send `hello_ack` with
`server_time_utc`, and may add `server_time_utc` to each `command`.

**Host duties:**
- Answer every `hello` with `hello_ack`, and send one whenever a device line arrives from a
  `boot_id` it hasn't acked yet.
- Deduplicate telemetry on (device_id, boot_id, sequence_number), as over HTTP.
- Treat a command as executed only after its `ack` arrives.
- Optionally re-send unacked commands. The device does not execute a `command_id` twice; the
  ack is re-sent at most every 15 s.

## 2. How it plugs in (no changes to existing logic)

`SerialTransport` implements `IHttpTransport`, the interface TelemetryClient and CommandClient
already use:

| HTTP call made by the existing clients | serial behaviour |
|---|---|
| `POST /api/v1/telemetry` | writes `{"type":"telemetry","v":"1.0","payload":<body>}`, returns 200 `{"ok":true}` |
| `POST /api/v1/control-ack` | writes an `ack` line, returns 200 |
| `GET /api/v1/chambers/{id}/desired-control` | returns a synthesized envelope `{"chamber_id","server_time_utc"?,"commands":[...]}` with up to 8 received commands (ascending `sequence_number`, each with its remaining `ttl_s`) |
| anything else (images) | 501 |
| any call before `hello_ack` | −1 (transient): telemetry and acks stay queued; nothing is lost |

CommandClient's `onServerTime` receives the envelope's `server_time_utc`, so the firmware clock
(`g.clock`) is synced without extra code.

## 3. Never blocking the safety loop

- `poll()` and every `request()` run in **netTask** (core 0). The loop task (safety, control,
  door, motor) never touches the serial transport.
- RX is non-blocking: `poll()` consumes at most 4096 bytes per call (rx budget) from a bounded
  8 KB line buffer. It never waits for bytes.
- TX writes each line with **one** `write()` call. At 115200 baud a 1.5 KB telemetry line takes
  about 130 ms, and a full UART TX buffer blocks only netTask.
- `SerialTransport::log(level, msg)` is safe from **any** task. It only appends to a bounded
  queue (16 lines, oldest dropped and counted), and netTask's `poll()` writes it out.

## 4. Keeping debug output from corrupting JSON lines

The port carries both the protocol and anything else printed to `Serial`. Rules:

1. Serial builds set `CORE_DEBUG_LEVEL=0` (`platformio_serial.ini`), which compiles out every
   `log_e/w/i` in the firmware and the Arduino core.
2. The hook (§6) disables the `console` and `statuslog` tasks in serial mode. The console would
   otherwise **read** protocol bytes off the same port, and the status log would print text
   every 30 s.
3. The remaining `Serial.printf` calls are the boot banner and two lines in `setup()`. Each is a
   whole-line write, and Arduino-ESP32 serialises each `write()` under the UART lock, so they
   land **between** JSON lines, never inside one. The host ignores them (§1).
4. New firmware diagnostics in serial mode should use `serialLink->log("warn", "...")`, not
   `Serial.print`.
5. A fully separate channel is possible: construct `ArduinoStreamPort serialPort(Serial1)` with
   `Serial1.begin(115200, SERIAL_8N1, RX, TX)` on two spare pins and a USB-UART adapter. Debug
   then stays on `Serial`. This needs two free GPIOs (see GPIO_MAP).

Port choice: with the current build (`ARDUINO_USB_CDC_ON_BOOT=0`), `Serial` is UART0 through the
board's USB-UART bridge (the "COM"/CH343 USB-C port). That is the port to plug into the Mac. With
`ARDUINO_USB_CDC_ON_BOOT=1`, `Serial` becomes native USB CDC on the other USB-C port. ROM boot
messages then stay on UART0 and off the protocol port, but that changes `platformio.ini` and the
upload/monitor port. It is not done here.

## 5. Build and test

```bash
cd firmware
pio test -e native -f test_serial                                # 19 tests
pio run -c platformio_serial.ini -e esp32s3cam_a_serial          # chamber A, serial transport
pio run -c platformio_serial.ini -e esp32s3cam_b_serial
pio run -c platformio_serial.ini -e esp32s3cam_a_serial -t upload   # only once the hook is applied
```

`platformio_serial.ini` pulls in `platformio.ini` via `extra_configs`, so the original file is
untouched. It uses `build_unflags` to replace `CORE_DEBUG_LEVEL=2` → 0 and `CAMERA_ENABLED=1` → 0.
`-U` does not work here: PlatformIO moves `-U` after all `-D`s and leaves the macro undefined.
**Until the hook is applied, these envs build the normal Wi-Fi firmware.** `SerialTransport` is
compiled but not linked.

## 6. The main.cpp hook (NOT applied; apply when the bring-up session releases main.cpp)

This patch was generated against `firmware/src/main.cpp` as of commit `26602a5` (the file with
the ENS160AHT21 include). It was verified by applying it to a scratch copy: `esp32s3cam_a_serial`
built (RAM 58,032 B, Flash 851,149 B, 50 `fa::SerialTransport::` symbols linked), and the plain
`esp32s3cam_a` Wi-Fi build of the same patched file also succeeded. All changes are behind
`#if FERMENTAI_TRANSPORT_SERIAL`, so Wi-Fi builds are unchanged.

```diff
--- a/firmware/src/main.cpp
+++ b/firmware/src/main.cpp
@@ -33,6 +33,9 @@
 #include "network/CommandClient.h"
 #include "network/NetworkManager.h"
 #include "network/TelemetryClient.h"
+#if FERMENTAI_TRANSPORT_SERIAL
+#include "network/SerialTransport.h"  // USB-serial transport (docs/SERIAL_TRANSPORT.md)
+#endif
 #include "sensors/BME680Sensor.h"
 #include "sensors/ENS160AHT21Sensor.h"
 #include "sensors/SHT31Sensor.h"
@@ -190,6 +193,9 @@
 esp_reset_reason_t resetReason = ESP_RST_UNKNOWN;
 NetConfig netCfg;
 NetworkManager* net = nullptr;
+#if FERMENTAI_TRANSPORT_SERIAL
+SerialTransport* serialLink = nullptr;
+#endif
 TelemetryClient* telemetry = nullptr;
 CommandClient* commands = nullptr;
 Scheduler sched;
@@ -656,8 +662,13 @@
   uint32_t last_poll = 0, last_tel = 0, last_cam = 0;
   for (;;) {
     const uint32_t now = millis();
+#if FERMENTAI_TRANSPORT_SERIAL
+    serialLink->poll(now);  // non-blocking RX, hello/pong/log TX
+    const bool up = serialLink->connected();
+#else
     net->maintain(now);
     const bool up = net->connected();
+#endif
 
     if (up && (uint32_t)(now - last_poll) >= cfg::kCommandPollPeriodMs) {
       last_poll = now;
@@ -804,8 +815,17 @@
 
   loadNetConfig();
   static NetworkManager netMgr(netCfg);
-  static TelemetryClient telClient(&netMgr, psramFound() ? 50 : 10, cfg::kHttpTimeoutMs);
-  static CommandClient cmdClient(&netMgr, CHAMBER_ID, DEVICE_ID, kProfile, cfg::kHttpTimeoutMs,
+#if FERMENTAI_TRANSPORT_SERIAL
+  static ArduinoStreamPort serialPort(Serial);
+  static SerialTransport serialTr(&serialPort,
+                                  SerialTransportConfig{DEVICE_ID, CHAMBER_ID, FERMENTAI_FIRMWARE_VERSION, bootId});
+  serialLink = &serialTr;
+  IHttpTransport* transport = &serialTr;
+#else
+  IHttpTransport* transport = &netMgr;
+#endif
+  static TelemetryClient telClient(transport, psramFound() ? 50 : 10, cfg::kHttpTimeoutMs);
+  static CommandClient cmdClient(transport, CHAMBER_ID, DEVICE_ID, kProfile, cfg::kHttpTimeoutMs,
                                  cfg::kAckMaxAttempts);
   net = &netMgr;
   telemetry = &telClient;
@@ -837,8 +857,10 @@
   sched.add("control", cfg::kControlPeriodMs, tControl, nullptr, 500);
   sched.add("commands", 50, tCommands, nullptr);
   sched.add("snapshot", cfg::kSnapshotPeriodMs, tSnapshot, nullptr);
+#if !FERMENTAI_TRANSPORT_SERIAL  // serial mode: the port belongs to the JSON protocol
   sched.add("console", 50, tConsole, nullptr);
   sched.add("statuslog", 30000, tStatusLog, nullptr, 5000);
+#endif
   ds.setPeriod(cfg::kDs18b20PeriodMs);
 
   enableLoopWDT();  // a hung loop resets the chip -> boots with outputs off
```

If `main.cpp` has moved on, apply it by hand. The essential changes are:

1. Use `transport` instead of `&netMgr` for the two clients.
2. Call `serialLink->poll(now)` and set `up = serialLink->connected()` in netTask.
3. Skip the console and statuslog tasks.

Wi-Fi is left as is. If NVS still holds Wi-Fi credentials the radio will associate, but nothing
uses it in serial mode.

## 7. What the hardware session must do

1. Apply the §6 hook, then `pio run -c platformio_serial.ini -e esp32s3cam_a_serial -t upload`
   through the **UART (CH343) USB-C port**.
2. In serial builds the interactive console is off. Do commissioning (`assign`, `phcal`,
   `arm ...`) with the normal Wi-Fi build first; the NVS settings persist across the reflash.
3. Close any `pio device monitor` before starting the Mac-side serial bridge. Only one process
   can own the port.
4. Expect a few non-JSON lines (ROM boot log, banner) after each reset. The bridge must ignore
   them.
5. Check:
   - A `hello` appears within 1 s of boot.
   - After `hello_ack`, `telemetry` arrives every 10 s with `timestamp_quality: "synced"`.
   - A `command` set_setpoint returns an `ack` with `applied_setpoint_c`.
   - `animate_motor` returns `accepted` then `completed` (`ran_ms`).
   - `ping` → `pong`.
6. Images: none over serial (camera compiled out). Use the Wi-Fi build for images.
