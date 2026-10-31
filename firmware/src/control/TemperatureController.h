// TemperatureController — decides the wanted Peltier direction and output fraction from the
// controlled temperature and the (already locally clamped) setpoint. Pure logic, no I/O.
//
// Hysteresis gate (both modes): heating starts below sp - h and stops at sp; cooling starts
// above sp + h and stops at sp. In Proportional mode the fraction is kp * |error| clamped to
// [min_fraction, max_fraction]; in Hysteresis (bang-bang) mode it is a fixed fraction.
// The PeltierDriver adds dead time, ramp and the PWM limit on top of this.
#pragma once

#include <math.h>
#include <stdint.h>

#include "fermentai_types.h"

namespace fa {

struct ControlDemand {
  Direction dir = Direction::None;
  float fraction = 0.0f;
};

class TemperatureController {
 public:
  enum class Mode : uint8_t { Hysteresis, Proportional };

  struct Config {
    Mode mode = Mode::Proportional;
    float hysteresis_c = 0.5f;
    float fixed_fraction = 0.6f;
    float kp_per_c = 0.25f;
    float min_fraction = 0.15f;
    float max_fraction = 0.8f;
  };

  explicit TemperatureController(const Config& cfg) : cfg_(cfg) {
    if (cfg_.hysteresis_c < 0.3f) cfg_.hysteresis_c = 0.3f;  // contract minimum
  }

  // temp must be valid/finite; caller handles invalid sensors (SafetyManager -> FAULT).
  ControlDemand compute(float temp_c, float setpoint_c);
  void reset() { state_ = Direction::None; }
  Direction state() const { return state_; }
  const Config& config() const { return cfg_; }

 private:
  Config cfg_;
  Direction state_ = Direction::None;
};

}  // namespace fa
