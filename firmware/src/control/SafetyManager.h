// SafetyManager — local interlocks that no command can override (contract §5).
//
//  overtemp / undertemp : chamber > 38 C or < 2 C -> FAULT, latched until back in range for 60 s
//  sensor_stale         : no valid chamber temperature for 30 s (also from boot) -> FAULT
//  heatsink_overtemp    : any heatsink probe > 60 C -> FAULT, latched until < 50 C for 60 s
//  heatsink_sensor_fault: heatsink probes required but missing/invalid -> FAULT (not latched)
// FAULT means: Peltier de-energised immediately (PeltierDriver::forceFault).
// Door-open and offline-hold are NOT faults; ChamberStateMachine puts the Peltier in IDLE for them.
#pragma once

#include <stdint.h>

#include "fermentai_types.h"

namespace fa {

struct SafetyInputs {
  Reading chamber;
  Reading int_heatsink;
  Reading ext_heatsink;
  bool heatsink_probes_required = true;
};

class SafetyManager {
 public:
  struct Config {
    float chamber_high_c;
    float chamber_low_c;
    float heatsink_max_c;
    float heatsink_clear_c;
    uint32_t sensor_stale_ms;
    uint32_t clear_hold_ms;
    float plausible_min_c;
    float plausible_max_c;
  };
  static Config defaults();

  explicit SafetyManager(const Config& cfg) : cfg_(cfg) {}
  void begin(uint32_t now_ms);
  void update(const SafetyInputs& in, uint32_t now_ms);

  uint32_t faults() const { return faults_; }
  bool tripped() const { return faults_ != 0; }
  // Monotonic count of new fault events (for logging).
  uint32_t tripCount() const { return trips_; }
  uint32_t lastValidChamberMs() const { return last_valid_chamber_ms_; }

 private:
  bool plausible(const Reading& r) const;
  void set(uint32_t bit);
  Config cfg_;
  uint32_t faults_ = 0;
  uint32_t trips_ = 0;
  uint32_t last_valid_chamber_ms_ = 0;
  uint32_t temp_in_range_since_ms_ = 0;
  bool temp_in_range_ = false;
  uint32_t hs_cool_since_ms_ = 0;
  bool hs_cool_ = false;
};

}  // namespace fa
