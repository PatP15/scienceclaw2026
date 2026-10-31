#include "control/SafetyManager.h"

#include "device_config.h"

namespace fa {

SafetyManager::Config SafetyManager::defaults() {
  Config c;
  c.chamber_high_c = limits::kChamberCutoffHighC;
  c.chamber_low_c = limits::kChamberCutoffLowC;
  c.heatsink_max_c = limits::kHeatsinkMaxC;
  c.heatsink_clear_c = limits::kHeatsinkClearC;
  c.sensor_stale_ms = limits::kSensorStaleMs;
  c.clear_hold_ms = limits::kFaultClearHoldMs;
  c.plausible_min_c = limits::kPlausibleMinC;
  c.plausible_max_c = limits::kPlausibleMaxC;
  return c;
}

void SafetyManager::begin(uint32_t now_ms) {
  faults_ = 0;
  last_valid_chamber_ms_ = now_ms;  // staleness counted from boot
  temp_in_range_ = false;
  hs_cool_ = false;
}

bool SafetyManager::plausible(const Reading& r) const {
  return r.valid && isfinite(r.value) && r.value >= cfg_.plausible_min_c &&
         r.value <= cfg_.plausible_max_c;
}

void SafetyManager::set(uint32_t bit) {
  if (!(faults_ & bit)) ++trips_;
  faults_ |= bit;
}

void SafetyManager::update(const SafetyInputs& in, uint32_t now_ms) {
  // ---- chamber sensor staleness
  if (plausible(in.chamber)) {
    last_valid_chamber_ms_ = now_ms;
    faults_ &= ~kFaultSensorStale;
  } else if ((uint32_t)(now_ms - last_valid_chamber_ms_) >= cfg_.sensor_stale_ms) {
    set(kFaultSensorStale);
  }

  // ---- over/under temperature (latched)
  if (plausible(in.chamber)) {
    const float t = in.chamber.value;
    if (t > cfg_.chamber_high_c) {
      set(kFaultOvertemp);
      temp_in_range_ = false;
    } else if (t < cfg_.chamber_low_c) {
      set(kFaultUndertemp);
      temp_in_range_ = false;
    } else {
      if (!temp_in_range_) {
        temp_in_range_ = true;
        temp_in_range_since_ms_ = now_ms;
      }
      if ((uint32_t)(now_ms - temp_in_range_since_ms_) >= cfg_.clear_hold_ms)
        faults_ &= ~(kFaultOvertemp | kFaultUndertemp);
    }
  } else {
    temp_in_range_ = false;  // cannot prove we are back in range
  }

  // ---- heatsinks
  const bool hs_i = plausible(in.int_heatsink);
  const bool hs_e = plausible(in.ext_heatsink);
  bool hot = false, cool_enough = true;
  if (hs_i) {
    hot |= in.int_heatsink.value > cfg_.heatsink_max_c;
    cool_enough &= in.int_heatsink.value < cfg_.heatsink_clear_c;
  }
  if (hs_e) {
    hot |= in.ext_heatsink.value > cfg_.heatsink_max_c;
    cool_enough &= in.ext_heatsink.value < cfg_.heatsink_clear_c;
  }
  if (!hs_i && !hs_e) cool_enough = false;
  if (hot) {
    set(kFaultHeatsinkOvertemp);
    hs_cool_ = false;
  } else if (faults_ & kFaultHeatsinkOvertemp) {
    if (cool_enough) {
      if (!hs_cool_) {
        hs_cool_ = true;
        hs_cool_since_ms_ = now_ms;
      }
      if ((uint32_t)(now_ms - hs_cool_since_ms_) >= cfg_.clear_hold_ms)
        faults_ &= ~kFaultHeatsinkOvertemp;
    } else {
      hs_cool_ = false;
    }
  }
  if (in.heatsink_probes_required && (!hs_i || !hs_e))
    set(kFaultHeatsinkSensor);
  else
    faults_ &= ~kFaultHeatsinkSensor;
}

}  // namespace fa
