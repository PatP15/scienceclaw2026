#include "sensors/DS18B20Manager.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "device_config.h"

#if defined(__has_include)
#if __has_include("secrets.h")
#include "secrets.h"  // optional DS18B20_ROM_* build-time defaults
#endif
#endif

namespace fa {

const char* DS18B20Manager::kvKey(ProbeRole r) {
  switch (r) {
    case ProbeRole::Starter: return "ds_starter";
    case ProbeRole::InternalHeatsink: return "ds_int_hs";
    case ProbeRole::ExternalHeatsink: return "ds_ext_hs";
  }
  return "ds_unknown";
}

bool DS18B20Manager::parseRom(const char* hex, RomAddr& out) {
  if (!hex) return false;
  int nib = 0;
  uint8_t cur = 0;
  int bytes = 0;
  for (const char* p = hex; *p; ++p) {
    if (*p == ':' || *p == '-' || *p == ' ') continue;
    if (!isxdigit((unsigned char)*p)) return false;
    const char c = (char)tolower((unsigned char)*p);
    const uint8_t v = (uint8_t)(c <= '9' ? c - '0' : c - 'a' + 10);
    cur = (uint8_t)((cur << 4) | v);
    if (++nib == 2) {
      if (bytes >= 8) return false;
      out.b[bytes++] = cur;
      nib = 0;
      cur = 0;
    }
  }
  return bytes == 8 && nib == 0 && !out.isZero();
}

void DS18B20Manager::formatRom(const RomAddr& rom, char* out, size_t n) {
  if (n < 17) {
    if (n) out[0] = 0;
    return;
  }
  for (int i = 0; i < 8; ++i) snprintf(out + 2 * i, 3, "%02x", rom.b[i]);
}

bool DS18B20Manager::parseRole(const char* s, ProbeRole& out) {
  if (!s) return false;
  if (!strcmp(s, "starter") || !strcmp(s, "starter_temperature")) {
    out = ProbeRole::Starter;
    return true;
  }
  if (!strcmp(s, "internal_heatsink") || !strcmp(s, "internal_heatsink_temperature") ||
      !strcmp(s, "int_hs")) {
    out = ProbeRole::InternalHeatsink;
    return true;
  }
  if (!strcmp(s, "external_heatsink") || !strcmp(s, "external_heatsink_temperature") ||
      !strcmp(s, "ext_hs")) {
    out = ProbeRole::ExternalHeatsink;
    return true;
  }
  return false;
}

void DS18B20Manager::begin(uint32_t now_ms) {
  for (size_t i = 0; i < kNumProbeRoles; ++i) {
    roles_[i] = RoleState();
    char hex[24];
    if (kv_ && kv_->getString(kvKey((ProbeRole)i), hex, sizeof hex)) {
      RomAddr r;
      if (parseRom(hex, r)) {
        roles_[i].mapped = true;
        roles_[i].rom = r;
      }
    }
  }
  // Optional compile-time defaults (only if NVS has no assignment for that role).
#ifdef DS18B20_ROM_STARTER
  if (!roles_[0].mapped) roles_[0].mapped = parseRom(DS18B20_ROM_STARTER, roles_[0].rom);
#endif
#ifdef DS18B20_ROM_INTERNAL_HEATSINK
  if (!roles_[1].mapped) roles_[1].mapped = parseRom(DS18B20_ROM_INTERNAL_HEATSINK, roles_[1].rom);
#endif
#ifdef DS18B20_ROM_EXTERNAL_HEATSINK
  if (!roles_[2].mapped) roles_[2].mapped = parseRom(DS18B20_ROM_EXTERNAL_HEATSINK, roles_[2].rom);
#endif
  rediscover(now_ms);
  converting_ = false;
  started_ = false;
  last_cycle_ms_ = now_ms;
}

void DS18B20Manager::rediscover(uint32_t now_ms) {
  n_found_ = bus_ ? bus_->discover(found_, kMaxProbes) : 0;
  last_discover_ms_ = now_ms;
  for (auto& r : roles_) r.present = r.mapped && onBus(r.rom);
}

bool DS18B20Manager::onBus(const RomAddr& rom) const {
  for (size_t i = 0; i < n_found_; ++i)
    if (found_[i] == rom) return true;
  return false;
}

bool DS18B20Manager::isAssigned(const RomAddr& rom) const {
  for (const auto& r : roles_)
    if (r.mapped && r.rom == rom) return true;
  return false;
}

size_t DS18B20Manager::unassignedCount() const {
  size_t n = 0;
  for (size_t i = 0; i < n_found_; ++i)
    if (!isAssigned(found_[i])) ++n;
  return n;
}

bool DS18B20Manager::assignRole(const RomAddr& rom, ProbeRole role) {
  if (rom.isZero()) return false;
  // One ROM serves exactly one role: un-map it from any other role first.
  for (size_t i = 0; i < kNumProbeRoles; ++i) {
    if ((ProbeRole)i != role && roles_[i].mapped && roles_[i].rom == rom) clearRole((ProbeRole)i);
  }
  RoleState& s = roles_[(size_t)role];
  s = RoleState();
  s.mapped = true;
  s.rom = rom;
  s.present = onBus(rom);
  char hex[17];
  formatRom(rom, hex, sizeof hex);
  return kv_ ? kv_->putString(kvKey(role), hex) : true;
}

bool DS18B20Manager::clearRole(ProbeRole role) {
  roles_[(size_t)role] = RoleState();
  return kv_ ? kv_->remove(kvKey(role)) : true;
}

bool DS18B20Manager::roleRom(ProbeRole role, RomAddr& out) const {
  const RoleState& s = roles_[(size_t)role];
  if (!s.mapped) return false;
  out = s.rom;
  return true;
}

void DS18B20Manager::update(uint32_t now_ms) {
  if (!bus_) return;
  if ((uint32_t)(now_ms - last_discover_ms_) >= rediscover_ms_ && !converting_) rediscover(now_ms);
  if (!converting_) {
    if (started_ && (uint32_t)(now_ms - last_cycle_ms_) < period_ms_) return;
    started_ = true;
    last_cycle_ms_ = now_ms;
    if (bus_->requestConversion()) {
      converting_ = true;
      conv_start_ms_ = now_ms;
    } else {
      for (auto& r : roles_) r.last_read_failed = true;
    }
    return;
  }
  if ((uint32_t)(now_ms - conv_start_ms_) < bus_->conversionTimeMs()) return;
  converting_ = false;
  readAll(now_ms);
}

void DS18B20Manager::readAll(uint32_t now_ms) {
  for (size_t i = 0; i < kNumProbeRoles; ++i) {
    RoleState& s = roles_[i];
    if (!s.mapped) continue;
    float t;
    if (!bus_->readTempC(s.rom, t)) {
      s.last_read_failed = true;
      s.present = false;  // no presence / CRC -> treat as disconnected until next good read
      ++crc_errors_;
      continue;
    }
    s.present = true;
    s.last_read_failed = false;
    // -127 = DEVICE_DISCONNECTED_C; 85.0 = power-on-reset scratchpad (no conversion happened)
    bool bad = !isfinite(t) || t <= -127.0f || t < -55.0f || t > 125.0f;
    if (t == 85.0f && !(s.have_valid && s.last > 75.0f)) bad = true;
    if (t < limits::kPlausibleMinC || t > 100.0f) bad = true;
    s.last_invalid = bad;
    if (bad) continue;
    s.last = t;
    s.have_valid = true;
    s.last_valid_ms = now_ms;
  }
}

SensorHealth DS18B20Manager::health(ProbeRole role, uint32_t now_ms) const {
  const RoleState& s = roles_[(size_t)role];
  if (!s.mapped || !s.present) {
    // A mapped probe that failed reads recently but had a fresh value is still within stale_ms.
    if (s.mapped && s.have_valid && (uint32_t)(now_ms - s.last_valid_ms) < stale_ms_)
      return SensorHealth::Ok;
    return SensorHealth::Missing;
  }
  if (!s.have_valid) return s.last_invalid ? SensorHealth::Invalid : SensorHealth::Missing;
  if ((uint32_t)(now_ms - s.last_valid_ms) >= stale_ms_)
    return s.last_invalid ? SensorHealth::Invalid : SensorHealth::Stale;
  return SensorHealth::Ok;
}

Reading DS18B20Manager::reading(ProbeRole role, uint32_t now_ms) const {
  const RoleState& s = roles_[(size_t)role];
  const SensorHealth h = health(role, now_ms);
  if (h != SensorHealth::Ok) return Reading::bad(h, s.last_valid_ms);
  return Reading::ok(s.last, s.last_valid_ms, prov_);
}

}  // namespace fa
