#include "control/PeltierDriver.h"

namespace fa {

void PeltierDriver::begin(uint32_t now_ms) {
  duty_ = 0.0f;
  want_dir_ = Direction::None;
  want_frac_ = 0.0f;
  energized_dir_ = Direction::None;
  last_dir_ = Direction::None;
  fault_ = false;
  mode_ = PeltierMode::Idle;
  off_since_ms_ = now_ms;
  ever_energized_ = false;
  last_update_ms_ = now_ms;
  apply();
}

void PeltierDriver::setArmed(bool armed) {
  armed_ = armed;
  apply();
}

void PeltierDriver::request(Direction dir, float fraction, uint32_t now_ms) {
  (void)now_ms;
  if (!(fraction > 0.0f)) dir = Direction::None;  // also catches NaN
  want_dir_ = dir;
  want_frac_ = dir == Direction::None ? 0.0f : clampf(fraction, 0.0f, cfg_.max_fraction);
}

bool PeltierDriver::inDeadTime(uint32_t now_ms) const {
  if (!ever_energized_) return false;
  return duty_ == 0.0f && (uint32_t)(now_ms - off_since_ms_) < cfg_.dead_time_ms;
}

void PeltierDriver::deenergize(uint32_t now_ms) {
  if (duty_ > 0.0f || energized_dir_ != Direction::None) off_since_ms_ = now_ms;
  duty_ = 0.0f;
  energized_dir_ = Direction::None;
  apply();
}

void PeltierDriver::forceFault(uint32_t now_ms) {
  deenergize(now_ms);
  fault_ = true;
  mode_ = PeltierMode::Fault;
}

void PeltierDriver::clearFault(uint32_t now_ms) {
  (void)now_ms;
  if (!fault_) return;
  fault_ = false;
  mode_ = PeltierMode::Idle;
}

void PeltierDriver::update(uint32_t now_ms) {
  const uint32_t dt = now_ms - last_update_ms_;
  last_update_ms_ = now_ms;
  if (fault_) {
    deenergize(now_ms);
    mode_ = PeltierMode::Fault;
    return;
  }
  if (want_dir_ == Direction::None) {
    deenergize(now_ms);
    mode_ = PeltierMode::Idle;
    return;
  }
  // Direction change requested while energised: drop to zero first, start dead time.
  if (energized_dir_ != Direction::None && energized_dir_ != want_dir_) {
    deenergize(now_ms);
    mode_ = PeltierMode::Switching;
    return;
  }
  // Zero output, and the last energised direction differs from the wanted one: wait dead time.
  if (energized_dir_ == Direction::None && ever_energized_ && last_dir_ != want_dir_ &&
      (uint32_t)(now_ms - off_since_ms_) < cfg_.dead_time_ms) {
    duty_ = 0.0f;
    mode_ = PeltierMode::Switching;
    apply();
    return;
  }
  // Energise / ramp in the wanted direction.
  energized_dir_ = want_dir_;
  last_dir_ = want_dir_;
  ever_energized_ = true;
  const float max_step = cfg_.ramp_per_s * (float)dt / 1000.0f;
  if (want_frac_ > duty_)
    duty_ = (want_frac_ - duty_ > max_step) ? duty_ + max_step : want_frac_;
  else
    duty_ = want_frac_;  // ramp-down immediate
  duty_ = clampf(duty_, 0.0f, cfg_.max_fraction);
  mode_ = want_dir_ == Direction::Heat ? PeltierMode::Heating : PeltierMode::Cooling;
  apply();
}

void PeltierDriver::apply() {
  const float out = armed_ ? duty_ : 0.0f;
  const bool chan_a = (energized_dir_ == Direction::Heat) == cfg_.heat_on_channel_a;
  // Always lower the inactive side BEFORE raising the active one.
  if (out <= 0.0f || energized_dir_ == Direction::None) {
    if (a_) a_->setFraction(0.0f);
    if (b_) b_->setFraction(0.0f);
    if (en_) en_->write(false);
    return;
  }
  if (chan_a) {
    if (b_) b_->setFraction(0.0f);
    if (en_) en_->write(true);
    if (a_) a_->setFraction(out);
  } else {
    if (a_) a_->setFraction(0.0f);
    if (en_) en_->write(true);
    if (b_) b_->setFraction(out);
  }
}

}  // namespace fa
