// PidController — discrete PID in output-fraction units. Pure logic, no I/O, no Arduino.
//
//   e  = setpoint - measurement                       (> 0: too cold -> heat)
//   P  = kp * e                                       kp: fraction / degC
//   I += ki * e * dt   (clamped to +-i_max)           ki: fraction / (degC * s)
//   D  = alpha*D_prev + (1-alpha) * (-kd * dm/dt)     kd: fraction * s / degC, on MEASUREMENT
//   u  = clamp(P + I + D, -out_max, +out_max)         + = heat demand, - = cool demand
//
// Anti-windup (conditional integration): a trial integral step is discarded when it would leave
// P+I+D saturated in the direction of the error. Derivative on measurement means a setpoint step
// produces no derivative kick. The controller only PROPOSES a signed demand; limits, heat<->cool
// dead time, heatsink cut-offs and the PWM cap stay in SafetyManager / PeltierDriver / PidMode.
#pragma once

#include <math.h>
#include <stdint.h>

namespace fa {

struct PidGains {
  float kp = 0.25f;    // fraction per degC (0.25 = existing proportional behaviour)
  float ki = 0.0f;     // fraction per (degC * s)
  float kd = 0.0f;     // fraction * s per degC
  float alpha = 0.8f;  // derivative filter memory, 0 = unfiltered, <1
};

class PidController {
 public:
  explicit PidController(float out_max = 0.8f, float i_max = -1.0f) { setLimits(out_max, i_max); }

  void setGains(const PidGains& g) { gains_ = g; }
  const PidGains& gains() const { return gains_; }
  void setLimits(float out_max, float i_max = -1.0f) {
    out_max_ = out_max > 0.0f ? out_max : 0.0f;
    i_max_ = i_max >= 0.0f ? i_max : out_max_;
  }
  float outMax() const { return out_max_; }

  // Clear integral and derivative memory (FAULT / door open / idle / invalid sensor).
  void reset() {
    i_ = 0.0f;
    d_ = 0.0f;
    u_ = p_ = 0.0f;
    have_prev_ = false;
    pending_bumpless_ = false;
  }
  // Bumpless transfer: on the next update() the integral is initialised so that u equals
  // `current_output` (signed fraction) with no step. No effect when ki == 0 (P-only has no memory).
  void engage(float current_output) {
    pending_out_ = current_output;
    pending_bumpless_ = true;
  }
  // Drop only the derivative memory (e.g. after a long gap between samples).
  void restartDerivative() {
    d_ = 0.0f;
    have_prev_ = false;
  }

  // dt_s <= 0: proportional term only, integral/derivative untouched.
  // freeze_integral: hold I (used while the output is held at 0 for heat<->cool dead time).
  float update(float setpoint, float meas, float dt_s, bool freeze_integral = false) {
    const float e = setpoint - meas;
    p_ = gains_.kp * e;
    if (pending_bumpless_) {
      pending_bumpless_ = false;
      if (gains_.ki > 0.0f) i_ = clampf_(pending_out_ - p_, -i_max_, i_max_);
    }
    if (dt_s > 0.0f && have_prev_) {
      const float raw = -gains_.kd * (meas - prev_meas_) / dt_s;
      const float a = clampf_(gains_.alpha, 0.0f, 0.999f);
      d_ = a * d_ + (1.0f - a) * raw;
    } else {
      d_ = 0.0f;
    }
    prev_meas_ = meas;
    have_prev_ = true;
    if (dt_s > 0.0f && gains_.ki > 0.0f && !freeze_integral) {
      const float i_try = clampf_(i_ + gains_.ki * e * dt_s, -i_max_, i_max_);
      const float u_try = p_ + i_try + d_;
      const bool sat_with_error = (u_try > out_max_ && e > 0.0f) || (u_try < -out_max_ && e < 0.0f);
      if (!sat_with_error) i_ = i_try;
    }
    u_ = clampf_(p_ + i_ + d_, -out_max_, out_max_);
    return u_;
  }

  float pTerm() const { return p_; }
  float iTerm() const { return i_; }
  float dTerm() const { return d_; }
  float output() const { return u_; }

 private:
  static float clampf_(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
  PidGains gains_;
  float out_max_ = 0.8f;
  float i_max_ = 0.8f;
  float i_ = 0.0f, d_ = 0.0f, p_ = 0.0f, u_ = 0.0f;
  float prev_meas_ = 0.0f;
  bool have_prev_ = false;
  bool pending_bumpless_ = false;
  float pending_out_ = 0.0f;
};

}  // namespace fa
