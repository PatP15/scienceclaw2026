// SerialTransport — the ESP32 <-> Mac link over USB CDC / UART instead of Wi-Fi, carrying the
// SAME payloads (telemetry_v1, acknowledgment_v1, command_v1). Protocol: contracts/serial_v1.md
// (interim definition in firmware/docs/SERIAL_TRANSPORT.md):
//
//   newline-delimited JSON, 115200 baud, one object per line, <= 8192 bytes incl. '\n',
//   every line {"type": ..., "v": "1.0", ...}
//   device -> host : hello {device_id, chamber_id, firmware_version, boot_id, capabilities[]}
//                     (at begin() and every 30 s until hello_ack)
//                    telemetry {payload: telemetry_v1}   ack {payload: acknowledgment_v1}
//                    log {level, msg}                    pong {uptime_ms[, id]}
//   host -> device : hello_ack {server_time_utc, chamber_id, poll_interval_s}
//                    command {payload: command_v1[, server_time_utc]}
//                    time {server_time_utc}              ping {[id]}
//
// It is a drop-in IHttpTransport, so TelemetryClient / CommandClient / CommandProcessor are used
// unchanged:
//   POST /api/v1/telemetry   -> emits a `telemetry` line, returns 200 {"ok":true}
//   POST /api/v1/control-ack -> emits an `ack` line, returns 200 {"ok":true}
//   GET  /api/v1/chambers/{id}/desired-control
//                            -> returns a synthesized desired-control envelope
//                               {"chamber_id", "server_time_utc"?, "commands": [...]} holding the
//                               `command` payloads received since the last poll (<= 8 per poll).
//                               Each carries "ttl_s" = remaining validity measured from RECEIPT of
//                               the serial line (expires_at - server time at receipt), so queueing
//                               between receipt and poll can only shorten validity, never extend it.
//   anything else (e.g. images) -> 501 (permanent; images need Wi-Fi/HTTP)
// Before hello_ack every request returns -1 (transient): telemetry/acks stay queued, nothing lost.
//
// Threading: poll() and request() run in the network task. log() may be called from ANY task and
// never blocks (bounded queue, flushed by poll()). Nothing here may be called from the safety loop
// except log().
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <deque>
#include <functional>
#include <mutex>
#include <string>

#include "network/HttpTransport.h"
#include "network/SerialLink.h"
#include "util/TimeUtil.h"

namespace fa {

struct SerialTransportConfig {
  const char* device_id = "";
  const char* chamber_id = "A";
  const char* firmware_version = "";
  const char* boot_id = "";
  uint32_t hello_interval_ms = 30000;
  size_t max_line_bytes = 8192;     // including the trailing '\n'
  size_t rx_budget_bytes = 4096;    // max bytes consumed per poll() (keeps poll() short)
  size_t max_pending_commands = 32; // queued between polls; 8 handed out per poll
  size_t max_log_lines = 16;
  // 0 = once hello_ack'ed stay connected (serial_v1). >0 = if the host has been silent this long,
  // fall back to hello every hello_interval_ms (host restart recovery).
  uint32_t host_timeout_ms = 0;
};

class SerialTransport : public IHttpTransport {
 public:
  static constexpr const char* kVersion = "1.0";
  static constexpr size_t kCommandsPerPoll = 8;  // == DesiredControlParse::kMaxCommands

  SerialTransport(ISerialPort* port, const SerialTransportConfig& cfg) : port_(port), cfg_(cfg) {}

  void begin(uint32_t now_ms);  // sends the first hello
  void poll(uint32_t now_ms);   // non-blocking RX + message handling + hello/log/pong TX
  bool connected() const { return acked_; }

  int request(const char* method, const char* path_and_query, const char* content_type,
              const uint8_t* body, size_t len, std::string* response, uint32_t timeout_ms) override;

  // Thread-safe, non-blocking: queued and written by the next poll(). level: debug|info|warn|error.
  void log(const char* level, const char* msg);

  const ClockSync& clock() const { return clock_; }
  uint32_t pollIntervalS() const { return poll_interval_s_; }
  // Optional: called on every hello_ack/time/command server_time_utc (epoch ms, receipt millis).
  std::function<void(int64_t, uint32_t)> onServerTime;

  struct Stats {
    uint32_t lines_rx = 0, lines_tx = 0, oversize_dropped = 0, non_json = 0, bad_json = 0,
             unknown_type = 0, bad_version = 0, commands_rx = 0, commands_dropped = 0,
             hellos_sent = 0, pongs = 0, logs_dropped = 0, tx_too_long = 0;
  };
  const Stats& stats() const { return stats_; }
  size_t pendingCommands() const { return cmds_.size(); }

 private:
  struct PendingCommand {
    std::string json;  // command_v1 object as received
    uint32_t received_ms;
    bool has_ttl;
    int64_t ttl_ms;
    int64_t sequence_number;
  };
  void handleLine(const char* line, size_t len, uint32_t now_ms);
  void syncClock(const char* iso, uint32_t now_ms);
  void sendHello(uint32_t now_ms);
  bool writeLine(const std::string& line);  // appends '\n', enforces max_line_bytes
  void flushLogs();

  ISerialPort* port_;
  SerialTransportConfig cfg_;
  std::string rx_;
  bool discarding_ = false;  // inside an oversize line: drop bytes until '\n'
  bool acked_ = false;
  bool began_ = false;
  uint32_t last_hello_ms_ = 0;
  uint32_t last_rx_ms_ = 0;
  uint32_t poll_interval_s_ = 0;
  ClockSync clock_;
  std::deque<PendingCommand> cmds_;
  std::mutex log_m_;
  std::deque<std::string> logs_;
  std::mutex tx_m_;
  Stats stats_;
};

}  // namespace fa
