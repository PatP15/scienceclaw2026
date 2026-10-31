#include "network/SerialTransport.h"

#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>

namespace fa {

namespace {
bool startsWith(const char* s, const char* p) { return strncmp(s, p, strlen(p)) == 0; }
}  // namespace

void SerialTransport::begin(uint32_t now_ms) {
  began_ = true;
  last_rx_ms_ = now_ms;
  rx_.reserve(cfg_.max_line_bytes);
  sendHello(now_ms);
}

bool SerialTransport::writeLine(const std::string& line) {
  if (!port_) return false;
  if (line.size() + 1 > cfg_.max_line_bytes) {
    ++stats_.tx_too_long;
    return false;
  }
  std::string out = line;
  out.push_back('\n');
  std::lock_guard<std::mutex> g(tx_m_);
  const size_t n = port_->write(reinterpret_cast<const uint8_t*>(out.data()), out.size());
  if (n != out.size()) return false;
  ++stats_.lines_tx;
  return true;
}

void SerialTransport::sendHello(uint32_t now_ms) {
  JsonDocument d;
  d["type"] = "hello";
  d["v"] = kVersion;
  d["device_id"] = cfg_.device_id;
  d["chamber_id"] = cfg_.chamber_id;
  d["firmware_version"] = cfg_.firmware_version;
  d["boot_id"] = cfg_.boot_id;
  d["uptime_ms"] = now_ms;
  JsonArray caps = d["capabilities"].to<JsonArray>();
  for (const char* c : {"telemetry", "ack", "command", "time", "ping", "log", "set_setpoint",
                        "animate_motor", "stop_motor", "noop"})
    caps.add(c);
  std::string s;
  serializeJson(d, s);
  if (writeLine(s)) ++stats_.hellos_sent;
  last_hello_ms_ = now_ms;
}

void SerialTransport::log(const char* level, const char* msg) {
  JsonDocument d;
  d["type"] = "log";
  d["v"] = kVersion;
  d["level"] = level ? level : "info";
  std::string m = msg ? msg : "";
  if (m.size() > 240) m.resize(240);
  d["msg"] = m;
  std::string s;
  serializeJson(d, s);
  std::lock_guard<std::mutex> g(log_m_);
  if (logs_.size() >= cfg_.max_log_lines) {
    logs_.pop_front();
    ++stats_.logs_dropped;
  }
  logs_.push_back(std::move(s));
}

void SerialTransport::flushLogs() {
  std::deque<std::string> out;
  {
    std::lock_guard<std::mutex> g(log_m_);
    out.swap(logs_);
  }
  for (const auto& s : out) writeLine(s);
}

void SerialTransport::syncClock(const char* iso, uint32_t now_ms) {
  int64_t ms;
  if (!iso || !parseIso8601Utc(iso, ms)) return;
  clock_.syncFrom(ms, now_ms);
  if (onServerTime) onServerTime(ms, now_ms);
}

void SerialTransport::poll(uint32_t now_ms) {
  if (!began_) begin(now_ms);
  // ---- RX: bounded work per call; never waits for bytes
  size_t budget = cfg_.rx_budget_bytes;
  while (budget-- > 0 && port_ && port_->available() > 0) {
    const int c = port_->read();
    if (c < 0) break;
    if (c == '\n') {
      if (!discarding_) {
        if (!rx_.empty() && rx_.back() == '\r') rx_.pop_back();
        if (!rx_.empty()) handleLine(rx_.c_str(), rx_.size(), now_ms);
      }
      discarding_ = false;
      rx_.clear();
      continue;
    }
    if (discarding_) continue;
    if (rx_.size() + 1 >= cfg_.max_line_bytes) {  // +1 for the '\n' that would end it
      ++stats_.oversize_dropped;
      discarding_ = true;
      rx_.clear();
      log("warn", "serial: dropped oversize line (> max_line_bytes)");
      continue;
    }
    rx_.push_back((char)c);
  }
  // ---- host silence (optional)
  if (acked_ && cfg_.host_timeout_ms && (uint32_t)(now_ms - last_rx_ms_) >= cfg_.host_timeout_ms) {
    acked_ = false;
    last_hello_ms_ = now_ms - cfg_.hello_interval_ms;  // hello immediately
  }
  // ---- hello until acknowledged
  if (!acked_ && (uint32_t)(now_ms - last_hello_ms_) >= cfg_.hello_interval_ms) sendHello(now_ms);
  flushLogs();
}

void SerialTransport::handleLine(const char* line, size_t len, uint32_t now_ms) {
  ++stats_.lines_rx;
  if (line[0] != '{') {  // console echo, boot noise, etc.
    ++stats_.non_json;
    return;
  }
  JsonDocument d;
  if (deserializeJson(d, line, len) != DeserializationError::Ok || !d.is<JsonObject>()) {
    ++stats_.bad_json;
    log("warn", "serial: unparseable JSON line ignored");
    return;
  }
  const char* type = d["type"] | (const char*)nullptr;
  if (!type) {
    ++stats_.bad_json;
    return;
  }
  const char* v = d["v"] | (const char*)nullptr;
  if (v && !startsWith(v, "1.")) {
    ++stats_.bad_version;
    log("warn", "serial: unsupported protocol version");
    return;
  }
  last_rx_ms_ = now_ms;

  if (!strcmp(type, "hello_ack")) {
    const char* ch = d["chamber_id"] | (const char*)nullptr;
    if (ch && strcmp(ch, cfg_.chamber_id) != 0) {
      log("error", "serial: hello_ack for another chamber ignored");
      return;
    }
    syncClock(d["server_time_utc"] | (const char*)nullptr, now_ms);
    poll_interval_s_ = d["poll_interval_s"] | 0;
    acked_ = true;
    return;
  }
  if (!strcmp(type, "time")) {
    syncClock(d["server_time_utc"] | (const char*)nullptr, now_ms);
    return;
  }
  if (!strcmp(type, "ping")) {
    JsonDocument p;
    p["type"] = "pong";
    p["v"] = kVersion;
    p["uptime_ms"] = now_ms;
    if (!d["id"].isNull()) p["id"] = d["id"];
    std::string s;
    serializeJson(p, s);
    if (writeLine(s)) ++stats_.pongs;
    return;
  }
  if (!strcmp(type, "command")) {
    JsonObject cmd = d["payload"].as<JsonObject>();
    const char* id = cmd["command_id"] | (const char*)nullptr;
    if (cmd.isNull() || !id || !id[0]) {
      ++stats_.bad_json;
      log("warn", "serial: command without payload.command_id ignored");
      return;
    }
    // Remaining validity at RECEIPT: expires_at - (message server_time, else synced clock).
    const char* st = d["server_time_utc"] | (const char*)nullptr;
    if (st) syncClock(st, now_ms);
    PendingCommand pc{std::string(), now_ms, false, 0, cmd["sequence_number"] | (int64_t)-1};
    if (!cmd["ttl_s"].isNull()) {
      pc.has_ttl = true;
      pc.ttl_ms = (int64_t)(cmd["ttl_s"].as<double>() * 1000.0);
    } else {
      int64_t exp_ms;
      const char* exp = cmd["expires_at_utc"] | (const char*)nullptr;
      if (exp && parseIso8601Utc(exp, exp_ms) && clock_.hasSync()) {
        pc.has_ttl = true;
        pc.ttl_ms = exp_ms - clock_.nowEpochMs(now_ms);
      }
    }
    serializeJson(cmd, pc.json);
    if (cmds_.size() >= cfg_.max_pending_commands) {
      cmds_.pop_front();
      ++stats_.commands_dropped;
    }
    cmds_.push_back(std::move(pc));
    ++stats_.commands_rx;
    return;
  }
  ++stats_.unknown_type;
}

int SerialTransport::request(const char* method, const char* path, const char* content_type,
                             const uint8_t* body, size_t len, std::string* response, uint32_t timeout_ms) {
  (void)content_type;
  (void)timeout_ms;
  if (!acked_) return -1;  // transient: callers keep their queues
  const bool post = !strcmp(method, "POST");
  if (post && (!strcmp(path, "/api/v1/telemetry") || !strcmp(path, "/api/v1/control-ack"))) {
    if (!body || len == 0 || body[0] != '{') return 400;
    const char* type = !strcmp(path, "/api/v1/telemetry") ? "telemetry" : "ack";
    std::string line;
    line.reserve(len + 48);
    line += "{\"type\":\"";
    line += type;
    line += "\",\"v\":\"";
    line += kVersion;
    line += "\",\"payload\":";
    line.append(reinterpret_cast<const char*>(body), len);
    line += "}";
    if (line.size() + 1 > cfg_.max_line_bytes) {
      ++stats_.tx_too_long;
      return 413;  // permanent: TelemetryClient drops it rather than retrying forever
    }
    if (!writeLine(line)) return -1;
    if (response) *response = "{\"ok\":true}";
    return 200;
  }
  if (!strcmp(method, "GET") && startsWith(path, "/api/v1/chambers/") && strstr(path, "/desired-control")) {
    JsonDocument env;
    env["schema_version"] = "1.0";
    env["chamber_id"] = cfg_.chamber_id;
    const uint32_t now_ms = last_rx_ms_ > 0 ? 0 : 0;  // placeholder (overwritten below)
    (void)now_ms;
    JsonArray arr = env["commands"].to<JsonArray>();
    // Ascending sequence_number (serial_v1 / command_v1 ordering); stable for equal numbers.
    std::stable_sort(cmds_.begin(), cmds_.end(), [](const PendingCommand& a, const PendingCommand& b) {
      return a.sequence_number < b.sequence_number;
    });
    size_t n = 0;
    while (!cmds_.empty() && n < kCommandsPerPoll) {
      PendingCommand pc = std::move(cmds_.front());
      cmds_.pop_front();
      JsonDocument c;
      if (deserializeJson(c, pc.json) != DeserializationError::Ok) continue;
      if (pc.has_ttl) {
        const int64_t left_ms = pc.ttl_ms - (int64_t)(uint32_t)(pollNow_ - pc.received_ms);
        c["ttl_s"] = (double)left_ms / 1000.0;
      }
      arr.add(c.as<JsonObject>());
      ++n;
    }
    if (clock_.hasSync()) {
      char ts[24];
      formatIso8601Utc(clock_.nowEpochMs(pollNow_), ts, sizeof ts);
      env["server_time_utc"] = ts;
    }
    if (poll_interval_s_) env["poll_interval_s"] = poll_interval_s_;
    if (response) {
      response->clear();
      serializeJson(env, *response);
    }
    return 200;
  }
  return 501;  // not available over serial (e.g. POST /api/v1/images)
}

}  // namespace fa
