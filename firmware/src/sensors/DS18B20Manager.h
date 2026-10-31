// DS18B20Manager — shared OneWire bus, ROM-address -> logical-role mapping persisted in NVS.
//
// Roles are NEVER inferred from bus order. A probe gets a role only through an explicit
// assignment (serial console `assign <rom> <role>`, or a build-flag default ROM). Unassigned
// probes are listed and flagged ("ds18b20_unassigned_probe") but never published as a role.
//
// Non-blocking cycle: requestConversion() -> wait conversionTimeMs() -> read each mapped ROM.
// Per role: Missing (no ROM mapped or ROM absent on bus), Invalid (CRC error / -127 / 85 C
// power-on value / implausible), Stale (no valid read for stale_ms), Ok.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "fermentai_types.h"
#include "hal_interfaces.h"

namespace fa {

class DS18B20Manager {
 public:
  static constexpr size_t kMaxProbes = 8;

  DS18B20Manager(IOneWireTempBus* bus, IKeyValueStore* kv, uint32_t stale_ms,
                 uint32_t rediscover_ms, Provenance prov)
      : bus_(bus), kv_(kv), stale_ms_(stale_ms), rediscover_ms_(rediscover_ms), prov_(prov) {}

  void begin(uint32_t now_ms);
  void update(uint32_t now_ms);  // call often (>= every 250 ms); does its own pacing
  void setPeriod(uint32_t period_ms) { period_ms_ = period_ms; }

  bool assignRole(const RomAddr& rom, ProbeRole role);  // persists
  bool clearRole(ProbeRole role);                        // persists
  bool roleRom(ProbeRole role, RomAddr& out) const;
  Reading reading(ProbeRole role, uint32_t now_ms) const;
  SensorHealth health(ProbeRole role, uint32_t now_ms) const;

  size_t discoveredCount() const { return n_found_; }
  const RomAddr& discovered(size_t i) const { return found_[i]; }
  bool isAssigned(const RomAddr& rom) const;
  size_t unassignedCount() const;
  uint32_t crcErrors() const { return crc_errors_; }
  void rediscover(uint32_t now_ms);

  static bool parseRom(const char* hex, RomAddr& out);   // 16 hex chars, optional ':' / '-'
  static void formatRom(const RomAddr& rom, char* out, size_t n);  // 16 hex chars
  static bool parseRole(const char* s, ProbeRole& out);

 private:
  struct RoleState {
    bool mapped = false;
    RomAddr rom{};
    bool present = false;
    float last = NAN;
    bool have_valid = false;
    uint32_t last_valid_ms = 0;
    bool last_read_failed = false;
    bool last_invalid = false;
  };
  bool onBus(const RomAddr& rom) const;
  static const char* kvKey(ProbeRole r);
  void readAll(uint32_t now_ms);

  IOneWireTempBus* bus_;
  IKeyValueStore* kv_;
  uint32_t stale_ms_;
  uint32_t rediscover_ms_;
  Provenance prov_;
  uint32_t period_ms_ = 2000;
  RoleState roles_[kNumProbeRoles];
  RomAddr found_[kMaxProbes];
  size_t n_found_ = 0;
  bool converting_ = false;
  uint32_t conv_start_ms_ = 0;
  uint32_t last_cycle_ms_ = 0;
  uint32_t last_discover_ms_ = 0;
  bool started_ = false;
  uint32_t crc_errors_ = 0;
};

}  // namespace fa
