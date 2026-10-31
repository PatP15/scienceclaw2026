// MotorController — decorative N20 chef-hat motor behind a logic-level MOSFET.
//
//  startMotor(duration_ms) clamps to max_ms (10 s) and refuses 0 / disarmed / already running.
//  Two independent cut-offs: update() (called every 20 ms) and a one-shot hardware timer
//  (esp_timer on device) armed for the same duration + margin, which drives the gate LOW even if
//  the main loop stalls. There is no API that turns the motor on without a duration.
//  The motor shares no state or pins with the Peltier.
#pragma once

#include <stdint.h>

#include "hal_interfaces.h"

namespace fa {

enum class MotorPattern : uint8_t { Continuous, Wiggle, Nod };

class MotorController {
 public:
  enum class StopReason : uint8_t { None, Completed, Stopped, Timeout, Disarmed };

  MotorController(IDigitalOut* gate, IOneShotTimer* hw_cutoff, uint32_t max_ms)
      : gate_(gate), timer_(hw_cutoff), max_ms_(max_ms) {}

  void begin();  // gate LOW (safe off on boot)
  void setArmed(bool a);
  bool armed() const { return armed_; }

  // Returns false (and does nothing) if refused. Clamped duration in *granted_ms.
  bool startMotor(uint32_t duration_ms, uint32_t now_ms, MotorPattern pattern = MotorPattern::Continuous,
                  uint32_t* granted_ms = nullptr);
  void stopMotor(uint32_t now_ms);
  bool isMotorRunning() const { return running_; }
  void update(uint32_t now_ms);

  // One-shot "the last run ended" event, consumed by the app to send the completion ack.
  bool takeFinished(StopReason& why, uint32_t& ran_ms);
  uint32_t maxMs() const { return max_ms_; }
  bool gateState() const { return gate_ && gate_->state(); }

  static MotorPattern parsePattern(const char* s);

 private:
  void finish(StopReason why, uint32_t now_ms);
  IDigitalOut* gate_;
  IOneShotTimer* timer_;
  uint32_t max_ms_;
  bool armed_ = false;
  bool running_ = false;
  uint32_t start_ms_ = 0;
  uint32_t duration_ms_ = 0;
  MotorPattern pattern_ = MotorPattern::Continuous;
  bool finished_ = false;
  StopReason why_ = StopReason::None;
  uint32_t ran_ms_ = 0;
};

}  // namespace fa
