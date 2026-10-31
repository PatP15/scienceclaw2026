#include "sensors/PHSensor.h"

#include <math.h>
#include <string.h>

namespace fa {

bool PhCalibration::valid(float min_span_mv) const {
  return has_ph4 && has_ph7 && isfinite(mv_ph4) && isfinite(mv_ph7) &&
         fabsf(mv_ph7 - mv_ph4) >= min_span_mv;
}

void PHSensor::begin() {
  cal_ = PhCalibration();
  if (!kv_) return;
  float v;
  if (kv_->getFloat("ph_mv4", v)) {
    cal_.mv_ph4 = v;
    cal_.has_ph4 = true;
  }
  if (kv_->getFloat("ph_mv7", v)) {
    cal_.mv_ph7 = v;
    cal_.has_ph7 = true;
  }
  kv_->getString("ph_cal_date", cal_.date, sizeof cal_.date);
}

void PHSensor::save() {
  if (!kv_) return;
  if (cal_.has_ph4) kv_->putFloat("ph_mv4", cal_.mv_ph4); else kv_->remove("ph_mv4");
  if (cal_.has_ph7) kv_->putFloat("ph_mv7", cal_.mv_ph7); else kv_->remove("ph_mv7");
  if (cal_.date[0]) kv_->putString("ph_cal_date", cal_.date); else kv_->remove("ph_cal_date");
}

void PHSensor::update(uint32_t now_ms) {
  if (!cfg_.enabled || !adc_) return;
  int64_t raw_sum = 0;
  double mv_sum = 0;
  uint16_t ok = 0;
  for (uint16_t i = 0; i < cfg_.samples; ++i) {
    int32_t r;
    float mv;
    if (adc_->sample(r, mv) && isfinite(mv)) {
      raw_sum += r;
      mv_sum += mv;
      ++ok;
    }
  }
  if (ok < (cfg_.samples + 1) / 2) {
    have_ = false;
    return;
  }
  raw_ = (int32_t)(raw_sum / ok);
  mv_ = (float)(mv_sum / ok);
  have_ = true;
  t_ms_ = now_ms;
}

bool PHSensor::voltageInWindow() const {
  return have_ && isfinite(mv_) && mv_ >= cfg_.adc_min_mv && mv_ <= cfg_.adc_max_mv;
}

bool PHSensor::calibratePoint(int buffer_ph, const char* date_text) {
  if (!cfg_.enabled || !voltageInWindow()) return false;
  if (buffer_ph == 4) {
    cal_.mv_ph4 = mv_;
    cal_.has_ph4 = true;
  } else if (buffer_ph == 7) {
    cal_.mv_ph7 = mv_;
    cal_.has_ph7 = true;
  } else {
    return false;
  }
  strncpy(cal_.date, date_text ? date_text : "", sizeof cal_.date - 1);
  cal_.date[sizeof cal_.date - 1] = 0;
  save();
  return true;
}

void PHSensor::clearCalibration() {
  cal_ = PhCalibration();
  save();
}

SensorHealth PHSensor::health(uint32_t now_ms) const {
  if (!cfg_.enabled) return SensorHealth::Disabled;
  if (!have_) return SensorHealth::Missing;
  if ((uint32_t)(now_ms - t_ms_) >= cfg_.stale_ms) return SensorHealth::Stale;
  if (!voltageInWindow()) return SensorHealth::Invalid;
  if (!calibrated()) return SensorHealth::Uncalibrated;
  const float ph = cal_.toPh(mv_);
  if (!isfinite(ph) || ph < 0.0f || ph > 14.0f) return SensorHealth::Invalid;
  return SensorHealth::Ok;
}

Reading PHSensor::voltageMv(uint32_t now_ms) const {
  if (!cfg_.enabled || !have_ || (uint32_t)(now_ms - t_ms_) >= cfg_.stale_ms)
    return Reading::bad(cfg_.enabled ? SensorHealth::Missing : SensorHealth::Disabled);
  return Reading::ok(mv_, t_ms_, prov_);
}

Reading PHSensor::ph(uint32_t now_ms) const {
  const SensorHealth h = health(now_ms);
  if (h != SensorHealth::Ok) return Reading::bad(h, t_ms_);
  return Reading::ok(cal_.toPh(mv_), t_ms_, prov_);
}

}  // namespace fa
