// command_schema.h — parser for GET /api/v1/chambers/{id}/desired-control and serializer for
// POST /api/v1/control-ack.
//
// The response may carry BOTH:
//   * v1.0 flat fields: command_id/sequence_number/ttl_s/temperature_setpoint_c (+ control_mode)
//     and the `motor` directive {command_id, action run|stop|none, duration_s, ttl_s, ...};
//   * v1.1 `commands: [{command_id, chamber_id, experiment_id, action, parameters,
//     issued_at_utc, expires_at_utc, sequence_number}]` with action set_setpoint | animate_motor |
//     stop_motor | capture_image | noop.
// Both are normalised into DeviceCommand. Expiry is always converted to a deadline on the
// device's monotonic clock measured from RECEIPT of the response:
//   ttl_s present                         -> ttl_s
//   expires_at_utc + server_time_utc      -> expires_at - server_time (server-relative TTL)
//   expires_at_utc only                   -> absolute; usable only with a synced device clock
//   nothing                               -> no freshness proof -> rejected by CommandProcessor
#pragma once

#include <ArduinoJson.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "fermentai_types.h"
#include "telemetry_schema.h"
#include "util/TimeUtil.h"

namespace fa {

enum class CmdAction : uint8_t { SetSetpoint, AnimateMotor, StopMotor, CaptureImage, Noop, Unknown };
// Sequence-number channel. v1.0: control and motor counted separately. v1.1 array: one counter
// per chamber across all actions.
enum class CmdChannel : uint8_t { FlatControl = 0, FlatMotor = 1, Array = 2 };
constexpr size_t kNumChannels = 3;
enum class ControlMode : uint8_t { Auto, Hold, Idle };

inline const char* actionName(CmdAction a) {
  switch (a) {
    case CmdAction::SetSetpoint: return "set_setpoint";
    case CmdAction::AnimateMotor: return "animate_motor";
    case CmdAction::StopMotor: return "stop_motor";
    case CmdAction::CaptureImage: return "capture_image";
    case CmdAction::Noop: return "noop";
    default: return "unknown";
  }
}

inline CmdAction parseAction(const char* s) {
  if (!s) return CmdAction::Unknown;
  if (!strcmp(s, "set_setpoint")) return CmdAction::SetSetpoint;
  if (!strcmp(s, "animate_motor")) return CmdAction::AnimateMotor;
  if (!strcmp(s, "stop_motor")) return CmdAction::StopMotor;
  if (!strcmp(s, "capture_image")) return CmdAction::CaptureImage;
  if (!strcmp(s, "noop")) return CmdAction::Noop;
  return CmdAction::Unknown;
}

struct DeviceCommand {
  char command_id[65] = {0};
  char chamber_id[17] = {0};
  char experiment_id[65] = {0};
  char target_command_id[65] = {0};  // stop_motor: the animate_motor being cancelled
  char pattern[12] = {0};
  CmdAction action = CmdAction::Unknown;
  CmdChannel channel = CmdChannel::Array;
  ControlMode control_mode = ControlMode::Auto;
  int64_t sequence_number = -1;
  bool has_setpoint = false;
  float setpoint_c = NAN;
  uint32_t duration_ms = 0;
  // expiry
  uint32_t received_ms = 0;
  bool has_ttl = false;
  int64_t ttl_ms = 0;               // may be <= 0 (already stale)
  int64_t expires_epoch_ms = -1;    // absolute expiry, used only if !has_ttl and clock synced
  bool flat = false;                // came from v1.0 flat fields
  bool flat_stop = false;           // v1.0 motor.action=stop: command_id is the cancelled run
};

struct DesiredControlParse {
  static constexpr size_t kMaxCommands = 8;
  bool ok = false;
  char error[64] = {0};
  char server_epoch[33] = {0};
  int64_t server_time_epoch_ms = -1;
  uint32_t received_ms = 0;
  uint32_t poll_interval_s = 0;
  DeviceCommand cmds[kMaxCommands];
  size_t n = 0;
  size_t dropped = 0;  // commands beyond kMaxCommands or unparseable
};

namespace detail {
inline void copyStr(char* dst, size_t n, const char* src) {
  if (!src) {
    dst[0] = 0;
    return;
  }
  strncpy(dst, src, n - 1);
  dst[n - 1] = 0;
}
inline ControlMode parseControlMode(const char* s) {
  if (s && !strcmp(s, "idle")) return ControlMode::Idle;
  if (s && !strcmp(s, "hold")) return ControlMode::Hold;
  return ControlMode::Auto;
}
inline void setExpiry(DeviceCommand& c, JsonVariantConst ttl, const char* expires_at,
                      int64_t server_now_ms) {
  if (!ttl.isNull() && (ttl.is<long>() || ttl.is<double>())) {
    c.has_ttl = true;
    c.ttl_ms = (int64_t)(ttl.as<double>() * 1000.0);
    return;
  }
  int64_t exp_ms;
  if (expires_at && parseIso8601Utc(expires_at, exp_ms)) {
    if (server_now_ms >= 0) {
      c.has_ttl = true;
      c.ttl_ms = exp_ms - server_now_ms;
    } else {
      c.expires_epoch_ms = exp_ms;
    }
  }
}
inline bool push(DesiredControlParse& out, const DeviceCommand& c) {
  if (out.n >= DesiredControlParse::kMaxCommands) {
    ++out.dropped;
    return false;
  }
  out.cmds[out.n++] = c;
  return true;
}
}  // namespace detail

// Parse a desired-control response body received at monotonic time received_ms.
inline bool parseDesiredControl(const char* json, size_t len, uint32_t received_ms,
                                DesiredControlParse& out) {
  using namespace detail;
  out = DesiredControlParse();
  out.received_ms = received_ms;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json, len);
  if (err) {
    snprintf(out.error, sizeof out.error, "json: %s", err.c_str());
    return false;
  }
  JsonObjectConst root = doc.as<JsonObjectConst>();
  if (root.isNull()) {
    copyStr(out.error, sizeof out.error, "not an object");
    return false;
  }
  copyStr(out.server_epoch, sizeof out.server_epoch, root["server_epoch"] | "");
  int64_t srv = -1;
  if (const char* st = root["server_time_utc"] | (const char*)nullptr) parseIso8601Utc(st, srv);
  out.server_time_epoch_ms = srv;
  out.poll_interval_s = root["poll_interval_s"] | 0;
  const char* chamber = root["chamber_id"] | "";
  const char* exp = root["experiment_id"] | (const char*)nullptr;

  // ---- v1.1 commands array first (ascending sequence as served)
  JsonArrayConst arr = root["commands"].as<JsonArrayConst>();
  for (JsonObjectConst jc : arr) {
    DeviceCommand c;
    const char* id = jc["command_id"] | (const char*)nullptr;
    if (!id || !id[0]) {
      ++out.dropped;
      continue;
    }
    copyStr(c.command_id, sizeof c.command_id, id);
    copyStr(c.chamber_id, sizeof c.chamber_id, jc["chamber_id"] | chamber);
    copyStr(c.experiment_id, sizeof c.experiment_id, jc["experiment_id"] | "");
    c.action = parseAction(jc["action"] | (const char*)nullptr);
    c.channel = CmdChannel::Array;
    c.sequence_number = jc["sequence_number"].isNull() ? -1 : jc["sequence_number"].as<int64_t>();
    c.received_ms = received_ms;
    JsonObjectConst prm = jc["parameters"].as<JsonObjectConst>();
    switch (c.action) {
      case CmdAction::SetSetpoint: {
        JsonVariantConst sp = prm["setpoint_c"];
        if (sp.isNull()) sp = prm["temperature_setpoint_c"];
        if (!sp.isNull() && (sp.is<double>() || sp.is<long>())) {
          c.has_setpoint = true;
          c.setpoint_c = sp.as<float>();
        }
        c.control_mode = parseControlMode(prm["control_mode"] | "auto");
        break;
      }
      case CmdAction::AnimateMotor: {
        if (!prm["duration_ms"].isNull())
          c.duration_ms = (uint32_t)fmax(0.0, prm["duration_ms"].as<double>());
        else if (!prm["duration_s"].isNull())
          c.duration_ms = (uint32_t)fmax(0.0, prm["duration_s"].as<double>() * 1000.0);
        copyStr(c.pattern, sizeof c.pattern, prm["pattern"] | "wiggle");
        break;
      }
      case CmdAction::StopMotor:
        copyStr(c.target_command_id, sizeof c.target_command_id,
                prm["target_command_id"] | "");
        break;
      default:
        break;
    }
    setExpiry(c, jc["ttl_s"], jc["expires_at_utc"] | (const char*)nullptr, srv);
    push(out, c);
  }

  // ---- v1.0 flat control command
  const char* cid = root["command_id"] | (const char*)nullptr;
  if (cid && cid[0]) {
    DeviceCommand c;
    copyStr(c.command_id, sizeof c.command_id, cid);
    copyStr(c.chamber_id, sizeof c.chamber_id, chamber);
    copyStr(c.experiment_id, sizeof c.experiment_id, exp ? exp : "");
    c.action = CmdAction::SetSetpoint;
    c.channel = CmdChannel::FlatControl;
    c.flat = true;
    c.sequence_number = root["sequence_number"].isNull() ? -1 : root["sequence_number"].as<int64_t>();
    JsonVariantConst sp = root["temperature_setpoint_c"];
    if (!sp.isNull() && (sp.is<double>() || sp.is<long>())) {
      c.has_setpoint = true;
      c.setpoint_c = sp.as<float>();
    }
    c.control_mode = parseControlMode(root["control_mode"] | "auto");
    c.received_ms = received_ms;
    setExpiry(c, root["ttl_s"], root["expires_at_utc"] | (const char*)nullptr, srv);
    push(out, c);
  }

  // ---- v1.0 flat motor directive
  JsonObjectConst m = root["motor"].as<JsonObjectConst>();
  if (!m.isNull()) {
    const char* act = m["action"] | "none";
    const char* mid = m["command_id"] | (const char*)nullptr;
    if (strcmp(act, "none") != 0 && mid && mid[0]) {
      DeviceCommand c;
      copyStr(c.command_id, sizeof c.command_id, mid);
      copyStr(c.chamber_id, sizeof c.chamber_id, chamber);
      copyStr(c.experiment_id, sizeof c.experiment_id, exp ? exp : "");
      c.channel = CmdChannel::FlatMotor;
      c.flat = true;
      c.sequence_number = m["sequence_number"].isNull() ? -1 : m["sequence_number"].as<int64_t>();
      c.received_ms = received_ms;
      if (!strcmp(act, "run")) {
        c.action = CmdAction::AnimateMotor;
        c.duration_ms = (uint32_t)fmax(0.0, (m["duration_s"] | 0.0) * 1000.0);
        copyStr(c.pattern, sizeof c.pattern, m["pattern"] | "wiggle");
      } else if (!strcmp(act, "stop")) {
        c.action = CmdAction::StopMotor;
        c.flat_stop = true;
        copyStr(c.target_command_id, sizeof c.target_command_id, mid);
      } else {
        c.action = CmdAction::Unknown;
      }
      setExpiry(c, m["ttl_s"], m["expires_at_utc"] | (const char*)nullptr, srv);
      // A stop directive must always be honoured: stopping is the safe direction.
      if (c.action == CmdAction::StopMotor && !c.has_ttl && c.expires_epoch_ms < 0) {
        c.has_ttl = true;
        c.ttl_ms = 30000;
      }
      push(out, c);
    }
  }
  out.ok = true;
  return true;
}

// ------------------------------------------------------------------ acknowledgments
enum class AckStatus : uint8_t { Accepted, Rejected, Completed, Failed, Cancelled, Expired };

inline const char* ackStatusName(AckStatus s) {
  switch (s) {
    case AckStatus::Accepted: return "accepted";
    case AckStatus::Rejected: return "rejected";
    case AckStatus::Completed: return "completed";
    case AckStatus::Failed: return "failed";
    case AckStatus::Cancelled: return "cancelled";
    case AckStatus::Expired: return "expired";
  }
  return "failed";
}

struct AckRecord {
  char command_id[65] = {0};
  AckStatus status = AckStatus::Accepted;
  CmdAction action = CmdAction::Unknown;
  int64_t sequence_number = -1;
  bool has_applied_setpoint = false;
  float applied_setpoint_c = NAN;
  int64_t ran_ms = -1;           // motor: actual run time
  char message[120] = {0};
  uint32_t uptime_ms = 0;
  int64_t epoch_ms = -1;         // -1 = no synced clock -> timestamp_utc null
  uint8_t attempts = 0;
};

// v1.0: schema_version, command_id, chamber_id, status, sequence_number, timestamp_utc,
//       applied_setpoint_c, message   (exactly; v1.0 server forbids extra keys)
// v1.1: the v1.0 keys + device_id, device_uptime_ms, detail, result
inline size_t serializeAck(const AckRecord& a, const char* chamber_id, const char* device_id,
                           ContractProfile p, char* buf, size_t n) {
  JsonDocument doc;
  JsonObject o = doc.to<JsonObject>();
  o["schema_version"] = kWireSchemaVersion;
  o["command_id"] = a.command_id;
  o["chamber_id"] = chamber_id;
  o["status"] = ackStatusName(a.status);
  if (a.sequence_number >= 0)
    o["sequence_number"] = a.sequence_number;
  else
    o["sequence_number"] = nullptr;
  if (a.epoch_ms >= 0) {
    char ts[24];
    formatIso8601Utc(a.epoch_ms, ts, sizeof ts);
    o["timestamp_utc"] = ts;
  } else {
    o["timestamp_utc"] = nullptr;
  }
  if (a.has_applied_setpoint && isfinite(a.applied_setpoint_c))
    o["applied_setpoint_c"] = detail::round2(a.applied_setpoint_c);
  else
    o["applied_setpoint_c"] = nullptr;
  if (a.message[0])
    o["message"] = a.message;
  else
    o["message"] = nullptr;
  if (p == ContractProfile::V1_1) {
    o["device_id"] = device_id;
    o["device_uptime_ms"] = a.uptime_ms;
    if (a.message[0])
      o["detail"] = a.message;
    else
      o["detail"] = nullptr;
    if (a.has_applied_setpoint || a.ran_ms >= 0) {
      JsonObject r = o["result"].to<JsonObject>();
      if (a.has_applied_setpoint && isfinite(a.applied_setpoint_c))
        r["applied_setpoint_c"] = detail::round2(a.applied_setpoint_c);
      if (a.ran_ms >= 0) r["ran_ms"] = a.ran_ms;
    }
  }
  if (measureJson(doc) + 1 > n) return 0;
  return serializeJson(doc, buf, n);
}

}  // namespace fa
