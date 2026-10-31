// SensorManager — the single acquisition service per chamber (PLAN_MASTER §5.1). It paces each
// driver, validates readings, tracks staleness / stuck values / warm-up, and publishes one
// consistent ChamberObservation with per-sensor health and quality flags.
#pragma once

#include <stdint.h>

#include "fermentai_types.h"
#include "hal_interfaces.h"
#include "sensors/DS18B20Manager.h"
#include "sensors/DoorSensor.h"
#include "sensors/EnvSensor.h"
#include "sensors/PHSensor.h"

namespace fa {

class SensorManager {
 public:
  struct Config {
    uint32_t env_period_ms = 5000;
    uint32_t env_stale_ms = 20000;
    uint32_t env_warmup_ms = 300000;
    uint32_t stuck_ms = 900000;
    float env_temp_offset_c = 0.0f;
    Provenance prov = Provenance::Measured;
  };

  SensorManager(IEnvSensor* env, DS18B20Manager* ds, PHSensor* ph, DoorSensor* door,
                const Config& cfg)
      : env_(env), ds_(ds), ph_(ph), door_(door), cfg_(cfg) {}

  void begin(uint32_t now_ms);
  void updateEnv(uint32_t now_ms);   // call every ~50-250 ms; paces itself to env_period_ms
  void updateDs(uint32_t now_ms) { if (ds_) ds_->update(now_ms); }
  void updatePh(uint32_t now_ms) { if (ph_) ph_->update(now_ms); }
  void updateDoor(uint32_t now_ms) { if (door_) door_->update(now_ms); }

  ChamberObservation observe(uint32_t now_ms) const;

  // Fast accessors for the safety loop.
  Reading chamberTemp(uint32_t now_ms) const;
  bool doorOpen() const { return door_ && door_->known() && door_->isOpen(); }

  DS18B20Manager* ds() { return ds_; }
  PHSensor* ph() { return ph_; }
  DoorSensor* door() { return door_; }
  IEnvSensor* env() { return env_; }
  uint32_t envFailures() const { return env_failures_; }

 private:
  IEnvSensor* env_;
  DS18B20Manager* ds_;
  PHSensor* ph_;
  DoorSensor* door_;
  Config cfg_;

  enum class EnvState : uint8_t { Idle, Waiting };
  EnvState env_state_ = EnvState::Idle;
  uint32_t env_ready_ms_ = 0;
  uint32_t env_last_start_ms_ = 0;
  bool env_started_ = false;
  bool env_began_ = false;
  uint32_t env_boot_ms_ = 0;
  uint32_t env_last_reinit_ms_ = 0;
  EnvValidated env_last_;
  bool env_have_ = false;
  uint32_t env_last_ok_ms_ = 0;
  bool env_last_failed_ = false;
  bool env_last_invalid_ = false;
  uint32_t env_failures_ = 0;
  // stuck-value detection (identical chamber temperature for a long time)
  float stuck_value_ = NAN;
  uint32_t stuck_since_ms_ = 0;
};

}  // namespace fa
