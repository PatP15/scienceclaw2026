#include "control/TemperatureController.h"

namespace fa {

ControlDemand TemperatureController::compute(float temp_c, float setpoint_c) {
  ControlDemand d;
  if (!isfinite(temp_c) || !isfinite(setpoint_c)) {
    state_ = Direction::None;
    return d;
  }
  const float err = setpoint_c - temp_c;  // > 0: too cold
  const float h = cfg_.hysteresis_c;
  switch (state_) {
    case Direction::None:
      if (err > h)
        state_ = Direction::Heat;
      else if (err < -h)
        state_ = Direction::Cool;
      break;
    case Direction::Heat:
      if (err <= 0.0f) state_ = (err < -h) ? Direction::Cool : Direction::None;
      break;
    case Direction::Cool:
      if (err >= 0.0f) state_ = (err > h) ? Direction::Heat : Direction::None;
      break;
  }
  d.dir = state_;
  if (state_ == Direction::None) return d;
  float f;
  if (cfg_.mode == Mode::Hysteresis) {
    f = cfg_.fixed_fraction;
  } else {
    f = cfg_.kp_per_c * fabsf(err);
    if (f < cfg_.min_fraction) f = cfg_.min_fraction;
  }
  d.fraction = clampf(f, 0.0f, cfg_.max_fraction);
  return d;
}

}  // namespace fa
