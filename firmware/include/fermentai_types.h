// fermentai_types.h — portable value types shared by drivers, control, app and serializers.
// No Arduino dependency: compiled for both the ESP32 and the native unit-test host.
#pragma once

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace fa {

enum class SensorHealth : uint8_t { Ok, Missing, Stale, Invalid, Uncalibrated, Disabled };

inline const char* healthName(SensorHealth h) {
  switch (h) {
    case SensorHealth::Ok: return "ok";
    case SensorHealth::Missing: return "missing";
    case SensorHealth::Stale: return "stale";
    case SensorHealth::Invalid: return "invalid";
    case SensorHealth::Uncalibrated: return "uncalibrated";
    case SensorHealth::Disabled: return "missing";  // never serialized; filtered by caller
  }
  return "invalid";
}

// Per-field provenance (contract v1.1 field_provenance).
enum class Provenance : uint8_t { None, Measured, Simulated, Manual };

inline const char* provenanceName(Provenance p) {
  switch (p) {
    case Provenance::Measured: return "measured";
    case Provenance::Simulated: return "simulated";
    case Provenance::Manual: return "manual";
    default: return nullptr;
  }
}

// One scalar observation with validity metadata. value is NaN whenever valid == false.
struct Reading {
  float value = NAN;
  bool valid = false;
  uint32_t t_ms = 0;  // device uptime when collected
  SensorHealth health = SensorHealth::Missing;
  Provenance prov = Provenance::None;

  static Reading ok(float v, uint32_t t, Provenance p) {
    Reading r;
    r.value = v;
    r.valid = isfinite(v);
    r.t_ms = t;
    r.health = r.valid ? SensorHealth::Ok : SensorHealth::Invalid;
    r.prov = p;
    if (!r.valid) r.value = NAN;
    return r;
  }
  static Reading bad(SensorHealth h, uint32_t t = 0) {
    Reading r;
    r.health = h;
    r.t_ms = t;
    return r;
  }
};

// Internal Peltier state. Serialized names depend on the contract profile (telemetry_schema.h).
enum class PeltierMode : uint8_t { Idle, Heating, Cooling, Switching, Fault, Off };
enum class Direction : uint8_t { None, Heat, Cool };

enum class ProbeRole : uint8_t { Starter = 0, InternalHeatsink = 1, ExternalHeatsink = 2 };
constexpr size_t kNumProbeRoles = 3;

inline const char* probeRoleName(ProbeRole r) {
  switch (r) {
    case ProbeRole::Starter: return "starter_temperature";
    case ProbeRole::InternalHeatsink: return "internal_heatsink_temperature";
    case ProbeRole::ExternalHeatsink: return "external_heatsink_temperature";
  }
  return "unknown";
}

enum class TimestampQuality : uint8_t { Unsynced, Synced, Estimated };

inline const char* timestampQualityName(TimestampQuality q) {
  switch (q) {
    case TimestampQuality::Synced: return "synced";
    case TimestampQuality::Estimated: return "estimated";
    default: return "unsynced";
  }
}

// Local safety faults (bit set). Names match contract fault_codes.
enum FaultBits : uint32_t {
  kFaultNone = 0,
  kFaultOvertemp = 1u << 0,
  kFaultUndertemp = 1u << 1,
  kFaultSensorStale = 1u << 2,
  kFaultHeatsinkOvertemp = 1u << 3,
  kFaultHeatsinkSensor = 1u << 4,
  kFaultFan = 1u << 5,
};

struct FaultName {
  uint32_t bit;
  const char* name;
};
constexpr FaultName kFaultNames[] = {
    {kFaultOvertemp, "overtemp"},
    {kFaultUndertemp, "undertemp"},
    {kFaultSensorStale, "sensor_stale"},
    {kFaultHeatsinkOvertemp, "heatsink_overtemp"},
    {kFaultHeatsinkSensor, "heatsink_sensor_fault"},
    {kFaultFan, "fan_fail"},
};

// Fixed-capacity list of quality flags. Entries must be string literals / static storage.
struct FlagList {
  static constexpr size_t kMax = 24;
  const char* items[kMax];
  size_t n = 0;

  void add(const char* f) {
    if (!f) return;
    for (size_t i = 0; i < n; ++i)
      if (strcmp(items[i], f) == 0) return;
    if (n < kMax) items[n++] = f;
  }
  bool has(const char* f) const {
    for (size_t i = 0; i < n; ++i)
      if (strcmp(items[i], f) == 0) return true;
    return false;
  }
  void merge(const FlagList& o) {
    for (size_t i = 0; i < o.n; ++i) add(o.items[i]);
  }
  void clear() { n = 0; }
};

struct HealthEntry {
  const char* name;
  SensorHealth health;
};

// Everything the acquisition service knows about one chamber at one instant (PLAN_MASTER §5.1).
struct ChamberObservation {
  Reading chamber_temp;       // env sensor (BME680/SHT31) air temperature, degC
  Reading rh;                 // %
  Reading pressure_pa;        // Pa (BME680 only)
  Reading gas_ohm;            // BME680 raw gas resistance, ohm. NOT CO2.
  Reading starter_temp;       // DS18B20 role starter_temperature
  Reading int_heatsink;       // DS18B20 role internal_heatsink_temperature
  Reading ext_heatsink;       // DS18B20 role external_heatsink_temperature
  Reading ph;                 // calibrated pH (null unless calibrated + valid)
  Reading ph_mv;              // conditioned interface output voltage, mV
  int32_t ph_raw_adc = -1;    // averaged raw ADC counts, -1 if not read
  bool ph_calibrated = false;
  bool door_known = false;
  bool door_open = false;
  uint32_t door_open_count = 0;
  uint32_t door_ms_since_change = 0;
  HealthEntry health[10];
  size_t n_health = 0;
  FlagList flags;

  void setHealth(const char* name, SensorHealth h) {
    if (h == SensorHealth::Disabled) return;
    for (size_t i = 0; i < n_health; ++i)
      if (strcmp(health[i].name, name) == 0) {
        health[i].health = h;
        return;
      }
    if (n_health < sizeof(health) / sizeof(health[0])) health[n_health++] = {name, h};
  }
};

inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

}  // namespace fa
