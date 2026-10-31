// PeltierDriver — BTS7960 output stage state machine: IDLE / HEATING / COOLING / SWITCHING / FAULT.
//
// Invariants (checked by test_control):
//  * Both PWM channels are never non-zero at the same time.
//  * Polarity is never reversed directly: output goes to 0, mode SWITCHING, and the new direction
//    is energised only after kHeatCoolDeadTimeMs with zero output.
//  * Duty is clamped to [0, max_fraction] and ramps up at ramp_per_s (soft start); ramp-down is
//    immediate (removing power is always the safe direction).
//  * When not armed (compile flag or runtime commissioning flag), every output is held at 0/LOW
//    while the state machine still runs so the logic can be observed in telemetry.
//  * forceFault() de-energises immediately and stays FAULT until clearFault().
#pragma once

#include <stdint.h>

#include "fermentai_types.h"
#include "hal_interfaces.h"

namespace fa {

struct PeltierConfig {
  float max_fraction = 0.8f;
  float ramp_per_s = 0.1f;
  uint32_t dead_time_ms = 60000;
  bool heat_on_channel_a = true;
};

class PeltierDriver {
 public:
  PeltierDriver(IPwmOut* a, IPwmOut* b, IDigitalOut* enable, const PeltierConfig& cfg)
      : a_(a), b_(b), en_(enable), cfg_(cfg) {}

  void begin(uint32_t now_ms);                         // all outputs off; no dead time pending
  void request(Direction dir, float fraction, uint32_t now_ms);  // desired; applied in update()
  void update(uint32_t now_ms);
  void forceFault(uint32_t now_ms);
  void clearFault(uint32_t now_ms);
  void setArmed(bool armed);

  PeltierMode mode() const { return mode_; }
  Direction energizedDirection() const { return energized_dir_; }
  float commandedFraction() const { return duty_; }   // what the logic wants (even if disarmed)
  float appliedFraction() const { return armed_ ? duty_ : 0.0f; }
  bool energized() const { return armed_ && duty_ > 0.0f; }
  bool armed() const { return armed_; }
  bool inDeadTime(uint32_t now_ms) const;
  uint32_t lastDeenergizedMs() const { return off_since_ms_; }

 private:
  void apply();
  void deenergize(uint32_t now_ms);

  IPwmOut* a_;
  IPwmOut* b_;
  IDigitalOut* en_;
  PeltierConfig cfg_;
  bool armed_ = false;
  bool fault_ = false;
  PeltierMode mode_ = PeltierMode::Idle;
  Direction want_dir_ = Direction::None;
  float want_frac_ = 0.0f;
  Direction energized_dir_ = Direction::None;  // direction of the current/last non-zero output
  Direction last_dir_ = Direction::None;       // last direction that was energised
  float duty_ = 0.0f;
  uint32_t off_since_ms_ = 0;
  bool ever_energized_ = false;
  uint32_t last_update_ms_ = 0;
};

}  // namespace fa
