#include "control/PidMode.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace fa {

const char* controlSelName(ControlSel s) {
  switch (s) {
    case ControlSel::Hysteresis: return "hyst";
    case ControlSel::Proportional: return "prop";
    case ControlSel::Pid: return "pid";
  }
  return "prop";
}

bool parseControlSel(const char* s, ControlSel& out) {
  if (!s) return false;
  if (!strcmp(s, "pid")) out = ControlSel::Pid;
  else if (!strcmp(s, "prop")) out = ControlSel::Proportional;
  else if (!strcmp(s, "hyst")) out = ControlSel::Hysteresis;
  else return false;
  return true;
}

bool pidGainsValid(const PidGains& g) {
  return isfinite(g.kp) && isfinite(g.ki) && isfinite(g.kd) && isfinite(g.alpha) && g.kp >= 0.0f &&
         g.kp <= PidLimits::kKpMax && g.ki >= 0.0f && g.ki <= PidLimits::kKiMax && g.kd >= 0.0f &&
         g.kd <= PidLimits::kKdMax && g.alpha >= 0.0f && g.alpha <= PidLimits::kAlphaMax;
}

PidMode::PidMode(const PidModeConfig& cfg) : cfg_(cfg), pid_(cfg.max_fraction) {}

bool PidMode::setGains(const PidGains& g) {
  if (!pidGainsValid(g)) return false;
  pid_.setGains(g);
  return true;
}

void PidMode::reset() {
  pid_.reset();
  active_ = false;
  hold_ = false;
  have_ms_ = false;
}

ControlDemand PidMode::compute(float temp_c, float setpoint_c, uint32_t now_ms) {
  ControlDemand d;
  if (!isfinite(temp_c) || !isfinite(setpoint_c)) {
    reset();
    return d;
  }
  float dt = 0.0f;
  if (have_ms_) {
    const uint32_t gap = now_ms - last_ms_;
    if (gap > cfg_.max_dt_ms) pid_.restartDerivative();
    else dt = (float)gap / 1000.0f;
  }
  last_ms_ = now_ms;
  have_ms_ = true;

  const float u = pid_.update(setpoint_c, temp_c, dt, hold_);
  const float mag = fabsf(u);

  // Deadband with hysteresis.
  if (!active_) {
    if (mag >= cfg_.min_fraction) active_ = true;
  } else if (mag < cfg_.min_fraction * cfg_.stop_ratio) {
    active_ = false;
  }
  hold_ = false;
  if (!active_) return d;

  const Direction want = u > 0.0f ? Direction::Heat : Direction::Cool;
  if (last_dir_ != Direction::None && want != last_dir_ &&
      (uint32_t)(now_ms - last_on_ms_) < cfg_.dead_time_ms) {
    hold_ = true;  // output 0 while the stage waits out the heat<->cool dead time
    return d;
  }
  last_dir_ = want;
  last_on_ms_ = now_ms;
  d.dir = want;
  d.fraction = clampf(mag, cfg_.min_fraction, cfg_.max_fraction);
  return d;
}

bool PidMode::save(IKeyValueStore* kv) const {
  if (!kv) return false;
  const PidGains& g = pid_.gains();
  char buf[96];
  snprintf(buf, sizeof buf, "%.6g %.6g %.6g %.6g %d", (double)g.kp, (double)g.ki, (double)g.kd,
           (double)g.alpha, (int)sel_);
  return kv->putString(kNvsKey, buf);
}

bool PidMode::load(IKeyValueStore* kv) {
  if (!kv) return false;
  char buf[96] = {0};
  if (!kv->getString(kNvsKey, buf, sizeof buf)) return false;
  float v[5];
  char* p = buf;
  for (int i = 0; i < 5; ++i) {
    char* end = nullptr;
    v[i] = strtof(p, &end);
    if (end == p) return false;
    p = end;
  }
  PidGains g;
  g.kp = v[0];
  g.ki = v[1];
  g.kd = v[2];
  g.alpha = v[3];
  const int m = (int)v[4];
  if (!pidGainsValid(g) || m < 0 || m > 2) return false;  // corrupt entry: keep defaults
  pid_.setGains(g);
  sel_ = (ControlSel)m;
  return true;
}

}  // namespace fa
