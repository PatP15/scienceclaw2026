// telemetry_schema.h — field-for-field serializer for POST /api/v1/telemetry.
//
// Contract sources:
//   v1.0 (frozen): firmware_contract/README_FOR_ESP32_AGENT.md §3.1, telemetry_example.json
//   v1.1 (additive): contracts/telemetry_v1.schema.json, src/fermentai/api/schemas.py TelemetryIn
//
// Two wire profiles:
//   ContractProfile::V1_0  exactly the v1.0 field set (the v1.0 server rejects unknown keys)
//                          peltier_mode heat|cool|switching|idle|fault, source real|simulated
//   ContractProfile::V1_1  v1.0 fields + v1.1 fields; schema_version stays "1.0" on the wire;
//                          peltier_mode heating|cooling|switching|idle|fault, source measured|simulated
// Rules (both): missing/failed values are JSON null, never NaN/Infinity.
// timestamp_utc is null until the clock has been synced from SNTP or a server response.
#pragma once

#include <ArduinoJson.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "fermentai_types.h"
#include "util/TimeUtil.h"

namespace fa {

enum class ContractProfile : uint8_t { V1_0, V1_1 };

constexpr const char* kWireSchemaVersion = "1.0";

constexpr ContractProfile defaultContractProfile() {
#if FERMENTAI_CONTRACT_V11
  return ContractProfile::V1_1;
#else
  return ContractProfile::V1_0;
#endif
}

// All v1.0 telemetry keys, in fixture order (test_telemetry checks this against the fixture).
constexpr const char* kTelemetryV10Keys[] = {
    "schema_version", "chamber_id", "experiment_id", "timestamp_utc", "chamber_temperature_c",
    "starter_temperature_c", "relative_humidity_percent", "gas_resistance_ohms", "ph", "ph_method",
    "starter_height_mm", "door_open", "peltier_mode", "temperature_setpoint_c", "motor_running",
    "source", "quality_flags", "device_id", "firmware_version", "uptime_s", "wifi_rssi_dbm",
    "heatsink_temperature_c", "fan_running", "peltier_duty_percent", "last_command_id",
    "last_sequence_number", "fault_codes"};
// Keys added by v1.1 (PLAN_MASTER §4).
constexpr const char* kTelemetryV11Keys[] = {
    "boot_id", "sequence_number", "timestamp_quality", "device_uptime_ms",
    "internal_heatsink_temperature_c", "external_heatsink_temperature_c", "pressure_pa",
    "peltier_output_fraction", "diagnostics", "field_provenance"};

inline const char* peltierModeName(PeltierMode m, ContractProfile p) {
  const bool v11 = p == ContractProfile::V1_1;
  switch (m) {
    case PeltierMode::Heating: return v11 ? "heating" : "heat";
    case PeltierMode::Cooling: return v11 ? "cooling" : "cool";
    case PeltierMode::Switching: return "switching";
    case PeltierMode::Fault: return "fault";
    case PeltierMode::Off: return "off";
    default: return "idle";
  }
}

inline const char* sourceName(bool simulated, ContractProfile p) {
  if (simulated) return "simulated";
  return p == ContractProfile::V1_1 ? "measured" : "real";
}

// Everything the serializer needs. Built by the app from a ChamberObservation + control state.
struct TelemetryRecord {
  const char* chamber_id = "A";
  const char* device_id = "unknown";
  const char* experiment_id = nullptr;  // null if none assigned
  const char* boot_id = "";
  const char* firmware_version = "0.0.0";
  uint32_t sequence_number = 0;
  int64_t timestamp_epoch_ms = -1;  // -1 = no valid wall clock -> null
  TimestampQuality timestamp_quality = TimestampQuality::Unsynced;
  uint64_t uptime_ms = 0;

  ChamberObservation obs;
  Reading starter_height_mm;  // never measured on-device (camera analysis is on the Mac)

  PeltierMode peltier_mode = PeltierMode::Idle;
  float peltier_output_fraction = 0.0f;  // actually applied (0 when disarmed)
  Reading setpoint;                      // setpoint in force after local clamp (invalid = none yet)
  bool motor_running = false;
  int8_t fan_state = -1;                 // -1 unknown/not controllable, 0 off, 1 on
  bool simulated = false;                // mock drivers active -> source "simulated"
  const char* ph_method = nullptr;

  bool wifi_rssi_valid = false;
  int32_t wifi_rssi_dbm = 0;
  uint32_t free_heap_bytes = 0;
  const char* last_command_id = nullptr;
  int64_t last_sequence_number = -1;
  uint32_t fault_bits = 0;
  FlagList quality_flags;  // merged with obs.flags at serialization
};

namespace detail {
inline double round2(float v) { return round((double)v * 100.0) / 100.0; }
// Output fraction sanitised to [0, 1]; NaN/Inf -> 0 (never serialized as NaN).
inline float safeFraction(float f) { return isfinite(f) ? clampf(f, 0.0f, 1.0f) : 0.0f; }

inline void putReading(JsonObject o, const char* key, const Reading& r, int decimals = 2) {
  if (r.valid && isfinite(r.value)) {
    if (decimals == 0)
      o[key] = round((double)r.value);
    else
      o[key] = round2(r.value);
  } else {
    o[key] = nullptr;
  }
}

inline void putProv(JsonObject fp, const char* key, const Reading& r) {
  const char* n = provenanceName(r.prov);
  if (r.valid && n) fp[key] = n;
}

// Hot side for the v1.0 single heatsink field: max of the valid heatsink probes.
inline Reading hotSide(const ChamberObservation& o) {
  if (o.int_heatsink.valid && o.ext_heatsink.valid)
    return o.int_heatsink.value >= o.ext_heatsink.value ? o.int_heatsink : o.ext_heatsink;
  if (o.ext_heatsink.valid) return o.ext_heatsink;
  if (o.int_heatsink.valid) return o.int_heatsink;
  return Reading::bad(SensorHealth::Missing);
}
}  // namespace detail

// Fill `doc` with the telemetry object. Returns false only if the document overflowed.
inline bool buildTelemetryJson(const TelemetryRecord& t, JsonDocument& doc, ContractProfile p) {
  using namespace detail;
  doc.clear();
  JsonObject o = doc.to<JsonObject>();
  const ChamberObservation& ob = t.obs;
  const bool v11 = p == ContractProfile::V1_1;

  o["schema_version"] = kWireSchemaVersion;
  o["chamber_id"] = t.chamber_id;
  if (t.experiment_id && t.experiment_id[0])
    o["experiment_id"] = t.experiment_id;
  else
    o["experiment_id"] = nullptr;
  if (t.timestamp_epoch_ms >= 0 && t.timestamp_quality != TimestampQuality::Unsynced) {
    char ts[24];
    formatIso8601Utc(t.timestamp_epoch_ms, ts, sizeof ts);
    o["timestamp_utc"] = ts;  // ArduinoJson copies (char* is duplicated)
  } else {
    o["timestamp_utc"] = nullptr;
  }
  putReading(o, "chamber_temperature_c", ob.chamber_temp);
  putReading(o, "starter_temperature_c", ob.starter_temp);
  putReading(o, "relative_humidity_percent", ob.rh);
  putReading(o, "gas_resistance_ohms", ob.gas_ohm, 0);
  putReading(o, "ph", ob.ph);
  if (ob.ph.valid && t.ph_method)
    o["ph_method"] = t.ph_method;
  else
    o["ph_method"] = nullptr;
  putReading(o, "starter_height_mm", t.starter_height_mm);
  if (ob.door_known)
    o["door_open"] = ob.door_open;
  else
    o["door_open"] = nullptr;
  o["peltier_mode"] = peltierModeName(t.peltier_mode, p);
  putReading(o, "temperature_setpoint_c", t.setpoint);
  o["motor_running"] = t.motor_running;
  o["source"] = sourceName(t.simulated, p);

  JsonArray qf = o["quality_flags"].to<JsonArray>();
  FlagList flags = ob.flags;
  flags.merge(t.quality_flags);
  for (size_t i = 0; i < flags.n; ++i) qf.add(flags.items[i]);

  // ---- v1.0 optional diagnostics
  o["device_id"] = t.device_id;
  o["firmware_version"] = t.firmware_version;
  o["uptime_s"] = (double)(t.uptime_ms / 100) / 10.0;
  if (t.wifi_rssi_valid)
    o["wifi_rssi_dbm"] = t.wifi_rssi_dbm;
  else
    o["wifi_rssi_dbm"] = nullptr;
  putReading(o, "heatsink_temperature_c", hotSide(ob));
  if (t.fan_state < 0)
    o["fan_running"] = nullptr;
  else
    o["fan_running"] = t.fan_state > 0;
  o["peltier_duty_percent"] = round2(safeFraction(t.peltier_output_fraction) * 100.0f);
  if (t.last_command_id && t.last_command_id[0])
    o["last_command_id"] = t.last_command_id;
  else
    o["last_command_id"] = nullptr;
  if (t.last_sequence_number >= 0)
    o["last_sequence_number"] = t.last_sequence_number;
  else
    o["last_sequence_number"] = nullptr;
  JsonArray fc = o["fault_codes"].to<JsonArray>();
  for (const auto& fn : kFaultNames)
    if (t.fault_bits & fn.bit) fc.add(fn.name);

  if (v11) {
    o["boot_id"] = t.boot_id;
    o["sequence_number"] = t.sequence_number;
    o["timestamp_quality"] = timestampQualityName(t.timestamp_epoch_ms >= 0
                                                      ? t.timestamp_quality
                                                      : TimestampQuality::Unsynced);
    o["device_uptime_ms"] = t.uptime_ms;
    putReading(o, "internal_heatsink_temperature_c", ob.int_heatsink);
    putReading(o, "external_heatsink_temperature_c", ob.ext_heatsink);
    putReading(o, "pressure_pa", ob.pressure_pa, 0);
    o["peltier_output_fraction"] = round(safeFraction(t.peltier_output_fraction) * 1000.0) / 1000.0;

    JsonObject d = o["diagnostics"].to<JsonObject>();
    if (t.wifi_rssi_valid)
      d["wifi_rssi_dbm"] = t.wifi_rssi_dbm;
    else
      d["wifi_rssi_dbm"] = nullptr;
    d["free_heap_bytes"] = t.free_heap_bytes;
    d["firmware_version"] = t.firmware_version;
    if (t.last_command_id && t.last_command_id[0])
      d["last_command_id"] = t.last_command_id;
    else
      d["last_command_id"] = nullptr;
    JsonObject sh = d["sensor_health"].to<JsonObject>();
    for (size_t i = 0; i < ob.n_health; ++i) sh[ob.health[i].name] = healthName(ob.health[i].health);

    JsonObject fp = o["field_provenance"].to<JsonObject>();
    putProv(fp, "chamber_temperature_c", ob.chamber_temp);
    putProv(fp, "starter_temperature_c", ob.starter_temp);
    putProv(fp, "relative_humidity_percent", ob.rh);
    putProv(fp, "gas_resistance_ohms", ob.gas_ohm);
    putProv(fp, "pressure_pa", ob.pressure_pa);
    putProv(fp, "ph", ob.ph);
    putProv(fp, "internal_heatsink_temperature_c", ob.int_heatsink);
    putProv(fp, "external_heatsink_temperature_c", ob.ext_heatsink);
    putProv(fp, "heatsink_temperature_c", hotSide(ob));
    putProv(fp, "starter_height_mm", t.starter_height_mm);
    if (ob.door_known) fp["door_open"] = t.simulated ? "simulated" : "measured";
  }
  return !doc.overflowed();
}

// Serialize into buf. Returns bytes written (0 on failure / truncation).
inline size_t serializeTelemetry(const TelemetryRecord& t, char* buf, size_t n, ContractProfile p) {
  JsonDocument doc;
  if (!buildTelemetryJson(t, doc, p)) return 0;
  const size_t need = measureJson(doc);
  if (need + 1 > n) return 0;
  return serializeJson(doc, buf, n);
}

}  // namespace fa
