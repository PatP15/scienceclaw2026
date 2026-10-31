#include "sensors/SensorManager.h"

#include <string.h>

namespace fa {

namespace {
const char* envFlag(const IEnvSensor* e, const char* bme, const char* sht, const char* other) {
  if (!e) return other;
  if (!strcmp(e->name(), "bme680")) return bme;
  if (!strcmp(e->name(), "sht31")) return sht;
  return other;
}
}  // namespace

void SensorManager::begin(uint32_t now_ms) {
  env_boot_ms_ = now_ms;
  env_state_ = EnvState::Idle;
  env_started_ = false;
  env_have_ = false;
  env_last_ok_ms_ = now_ms;
  env_began_ = env_ && env_->begin();
  env_last_reinit_ms_ = now_ms;
  if (ds_) ds_->begin(now_ms);
  if (ph_) ph_->begin();
  if (door_) door_->begin(now_ms);
  stuck_since_ms_ = now_ms;
}

void SensorManager::updateEnv(uint32_t now_ms) {
  if (!env_) return;
  if (!env_->present()) {
    // Re-probe a missing/failed sensor every 10 s (hot-plug / brown-out recovery).
    if ((uint32_t)(now_ms - env_last_reinit_ms_) >= 10000) {
      env_last_reinit_ms_ = now_ms;
      env_began_ = env_->begin();
      env_state_ = EnvState::Idle;
    }
    return;
  }
  if (env_state_ == EnvState::Idle) {
    if (env_started_ && (uint32_t)(now_ms - env_last_start_ms_) < cfg_.env_period_ms) return;
    env_started_ = true;
    env_last_start_ms_ = now_ms;
    const uint32_t wait = env_->startMeasurement();
    if (wait == 0) {
      env_last_failed_ = true;
      ++env_failures_;
      return;
    }
    env_ready_ms_ = now_ms + wait;
    env_state_ = EnvState::Waiting;
    return;
  }
  if ((int32_t)(now_ms - env_ready_ms_) < 0) return;
  env_state_ = EnvState::Idle;
  EnvRaw raw;
  if (!env_->fetch(raw)) {
    env_last_failed_ = true;
    ++env_failures_;
    return;
  }
  env_last_failed_ = false;
  EnvValidated v = validateEnv(raw, now_ms, cfg_.prov, cfg_.env_temp_offset_c);
  env_last_invalid_ = v.any_invalid;
  env_last_ = v;
  env_have_ = true;
  if (v.temperature.valid) {
    env_last_ok_ms_ = now_ms;
    if (v.temperature.value != stuck_value_) {
      stuck_value_ = v.temperature.value;
      stuck_since_ms_ = now_ms;
    }
  }
}

Reading SensorManager::chamberTemp(uint32_t now_ms) const {
  if (!env_have_ || !env_last_.temperature.valid)
    return Reading::bad(env_have_ ? SensorHealth::Invalid : SensorHealth::Missing);
  if ((uint32_t)(now_ms - env_last_.temperature.t_ms) >= cfg_.env_stale_ms)
    return Reading::bad(SensorHealth::Stale, env_last_.temperature.t_ms);
  if ((uint32_t)(now_ms - stuck_since_ms_) >= cfg_.stuck_ms)
    return Reading::bad(SensorHealth::Stale, env_last_.temperature.t_ms);
  return env_last_.temperature;
}

ChamberObservation SensorManager::observe(uint32_t now_ms) const {
  ChamberObservation o;
  // ---------------- environmental sensor
  if (env_) {
    const char* nm = env_->name();
    const bool fresh = env_have_ && (uint32_t)(now_ms - env_last_ok_ms_) < cfg_.env_stale_ms;
    o.chamber_temp = chamberTemp(now_ms);
    auto freshOr = [&](const Reading& r) {
      if (!fresh || !r.valid) return Reading::bad(r.health == SensorHealth::Ok ? SensorHealth::Stale : r.health);
      return r;
    };
    o.rh = freshOr(env_last_.humidity);
    o.pressure_pa = freshOr(env_last_.pressure);
    o.gas_ohm = freshOr(env_last_.gas);
    SensorHealth h;
    if (!env_->present())
      h = SensorHealth::Missing;
    else if (!env_have_)
      h = SensorHealth::Missing;
    else if (!fresh)
      h = SensorHealth::Stale;
    else if (env_last_invalid_)
      h = SensorHealth::Invalid;
    else
      h = o.chamber_temp.valid ? SensorHealth::Ok : o.chamber_temp.health;
    o.setHealth(nm, h);
    if (!env_->present()) o.flags.add(envFlag(env_, "bme680_missing", "sht31_missing", "env_sensor_missing"));
    if (env_last_failed_ || !fresh)
      o.flags.add(envFlag(env_, "bme680_read_failed", "sht31_read_failed", "env_sensor_read_failed"));
    if (env_have_ && env_last_invalid_)
      o.flags.add(envFlag(env_, "bme680_invalid_reading", "sht31_invalid_reading", "env_sensor_invalid_reading"));
    if (o.chamber_temp.health == SensorHealth::Stale && fresh) o.flags.add("chamber_temperature_stuck");
    if (!strcmp(nm, "bme680") || !strcmp(nm, "mock_env")) {
      if ((uint32_t)(now_ms - env_boot_ms_) < cfg_.env_warmup_ms && o.gas_ohm.valid)
        o.flags.add("sensor_warming_up");
    }
  } else {
    o.flags.add("env_sensor_not_configured");
  }

  // ---------------- DS18B20 roles
  if (ds_) {
    static const char* const kNames[kNumProbeRoles] = {"ds18b20_starter", "ds18b20_heatsink_internal",
                                                       "ds18b20_heatsink_external"};
    Reading* dst[kNumProbeRoles] = {&o.starter_temp, &o.int_heatsink, &o.ext_heatsink};
    for (size_t i = 0; i < kNumProbeRoles; ++i) {
      const ProbeRole role = (ProbeRole)i;
      *dst[i] = ds_->reading(role, now_ms);
      RomAddr rom;
      const bool mapped = ds_->roleRom(role, rom);
      o.setHealth(kNames[i], dst[i]->health);
      if (!mapped)
        o.flags.add("ds18b20_role_unassigned");
      else if (dst[i]->health == SensorHealth::Missing)
        o.flags.add("ds18b20_disconnected");
      else if (dst[i]->health == SensorHealth::Invalid)
        o.flags.add("ds18b20_invalid_reading");
      else if (dst[i]->health == SensorHealth::Stale)
        o.flags.add("ds18b20_stale");
    }
    if (ds_->unassignedCount() > 0) o.flags.add("ds18b20_unassigned_probe");
  }

  // ---------------- pH (optional; null unless calibrated and valid)
  if (ph_ && ph_->enabled()) {
    o.ph = ph_->ph(now_ms);
    o.ph_mv = ph_->voltageMv(now_ms);
    o.ph_raw_adc = ph_->rawAdc();
    o.ph_calibrated = ph_->calibrated();
    const SensorHealth h = ph_->health(now_ms);
    o.setHealth("ph", h);
    if (h == SensorHealth::Uncalibrated) o.flags.add("ph_uncalibrated");
    if (h == SensorHealth::Invalid) o.flags.add("ph_out_of_range");
    if (h == SensorHealth::Missing || h == SensorHealth::Stale) o.flags.add("ph_read_failed");
  }

  // ---------------- door
  if (door_) {
    o.door_known = door_->known();
    o.door_open = door_->isOpen();
    o.door_open_count = door_->openCount();
    o.door_ms_since_change = door_->msSinceChange(now_ms);
    o.setHealth("door", o.door_known ? SensorHealth::Ok : SensorHealth::Missing);
    if (o.door_known && o.door_open) o.flags.add("door_open_during_reading");
  }
  return o;
}

}  // namespace fa
