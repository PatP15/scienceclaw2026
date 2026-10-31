#include "control/MotorController.h"

#include <string.h>

namespace fa {

MotorPattern MotorController::parsePattern(const char* s) {
  if (s && !strcmp(s, "wiggle")) return MotorPattern::Wiggle;
  if (s && !strcmp(s, "nod")) return MotorPattern::Nod;
  return MotorPattern::Continuous;  // "spin" and unknown
}

void MotorController::begin() {
  running_ = false;
  finished_ = false;
  if (timer_) timer_->cancel();
  if (gate_) gate_->write(false);
}

void MotorController::setArmed(bool a) {
  armed_ = a;
  if (!a && running_) finish(StopReason::Disarmed, start_ms_ + duration_ms_);
}

bool MotorController::startMotor(uint32_t duration_ms, uint32_t now_ms, MotorPattern pattern,
                                 uint32_t* granted_ms) {
  if (!armed_ || running_ || duration_ms == 0 || !gate_) return false;
  if (duration_ms > max_ms_) duration_ms = max_ms_;
  duration_ms_ = duration_ms;
  start_ms_ = now_ms;
  pattern_ = pattern;
  running_ = true;
  finished_ = false;
  // Independent cutoff slightly after the software deadline.
  if (timer_) timer_->start(duration_ms + 50);
  gate_->write(true);
  if (granted_ms) *granted_ms = duration_ms;
  return true;
}

void MotorController::finish(StopReason why, uint32_t now_ms) {
  if (gate_) gate_->write(false);
  if (timer_) timer_->cancel();
  if (!running_) return;
  running_ = false;
  finished_ = true;
  why_ = why;
  uint32_t ran = now_ms - start_ms_;
  ran_ms_ = ran > duration_ms_ ? duration_ms_ : ran;
}

void MotorController::stopMotor(uint32_t now_ms) {
  if (running_)
    finish(StopReason::Stopped, now_ms);
  else if (gate_)
    gate_->write(false);
}

void MotorController::update(uint32_t now_ms) {
  if (!running_) {
    if (gate_ && gate_->state()) gate_->write(false);  // never ON while not running
    return;
  }
  const uint32_t el = now_ms - start_ms_;
  if (el >= duration_ms_ || el >= max_ms_) {
    finish(el > max_ms_ ? StopReason::Timeout : StopReason::Completed, now_ms);
    return;
  }
  // Decorative patterns: gate pulsing (on/off periods in ms).
  bool on = true;
  switch (pattern_) {
    case MotorPattern::Wiggle: on = (el % 600) < 400; break;
    case MotorPattern::Nod: on = (el % 500) < 250; break;
    default: break;
  }
  if (gate_ && gate_->state() != on) gate_->write(on);
}

bool MotorController::takeFinished(StopReason& why, uint32_t& ran_ms) {
  if (!finished_) return false;
  finished_ = false;
  why = why_;
  ran_ms = ran_ms_;
  return true;
}

}  // namespace fa
