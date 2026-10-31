// ChamberStateMachine — one chamber's local brain. Owns the run state, applies commands,
// combines SafetyManager + TemperatureController + PeltierDriver + MotorController, and builds
// the telemetry record. Pure logic: unit-tested natively with mock drivers.
//
// Run states:
//   WaitingForSensors  boot: Peltier off until the controlled temperature is valid
//   WaitingForCommand  Peltier off until a FRESH (unexpired) control command has been applied
//   Active             closed-loop control toward the clamped setpoint
//   OfflineHold        no valid control command for kOfflineHoldMs -> Peltier idle (passive)
// Overlays (not states): FAULT from SafetyManager (Peltier forced off), door open (Peltier idle,
// motor refused/stopped), control_mode idle (Peltier idle).
#pragma once

#include <stdint.h>

#include "app/CommandProcessor.h"
#include "app/ExperimentManager.h"
#include "control/MotorController.h"
#include "control/PeltierDriver.h"
#include "control/SafetyManager.h"
#include "control/TemperatureController.h"
#include "sensors/SensorManager.h"
#include "telemetry_schema.h"
#include "util/TimeUtil.h"

namespace fa {

enum class RunState : uint8_t { WaitingForSensors, WaitingForCommand, Active, OfflineHold };
const char* runStateName(RunState s);

class ChamberStateMachine : public ICommandTarget {
 public:
  struct Config {
    const char* chamber_id = "A";
    bool heatsink_probes_required = true;
    bool camera_available = false;
    uint32_t offline_hold_ms = 1800000;
  };

  ChamberStateMachine(const Config& cfg, SensorManager& sensors, SafetyManager& safety,
                      TemperatureController& ctl, PeltierDriver& peltier, MotorController* motor,
                      IKeyValueStore* kv, IAckSink* acks, ClockSync& clock)
      : cfg_(cfg), sensors_(sensors), safety_(safety), ctl_(ctl), peltier_(peltier), motor_(motor),
        acks_(acks), clock_(clock), cmds_(cfg.chamber_id, kv, this, acks) {}

  void begin(uint32_t now_ms);
  void safetyTick(uint32_t now_ms);   // 100-250 ms
  void controlTick(uint32_t now_ms);  // 1 s
  void motorTick(uint32_t now_ms);    // 20-50 ms
  void handleDesiredControl(const DesiredControlParse& p, uint32_t now_ms) {
    cmds_.handle(p, now_ms, clock_);
  }

  // Fill the chamber-level part of a telemetry record (caller adds boot/seq/network diagnostics).
  void fillTelemetry(TelemetryRecord& r, uint32_t now_ms) const;

  // ICommandTarget
  void applySetpoint(float clamped_c, ControlMode mode, const char* experiment_id, uint32_t now_ms) override;
  bool startMotor(uint32_t duration_ms, const char* pattern, const char* command_id, int64_t seq,
                  uint32_t now_ms, uint32_t* granted_ms, char* why, size_t why_n) override;
  bool stopMotor(uint32_t now_ms, char* stopped_id, size_t n) override;
  bool requestImage(const char* command_id, int64_t seq, char* why, size_t why_n) override;

  // Camera request handshake (network task takes it).
  bool takeImageRequest(char* command_id, size_t n, int64_t& seq);
  void setCameraAvailable(bool a) { cfg_.camera_available = a; }

  RunState runState() const { return state_; }
  PeltierMode reportedMode() const;
  const ExperimentManager& experiment() const { return exp_; }
  const CommandProcessor& commands() const { return cmds_; }
  CommandProcessor& commands() { return cmds_; }
  uint32_t faults() const { return safety_.faults(); }
  bool doorOpen() const { return sensors_.doorOpen(); }

 private:
  void ackMotor(AckStatus st, const char* msg, int64_t ran_ms, uint32_t now_ms);

  Config cfg_;
  SensorManager& sensors_;
  SafetyManager& safety_;
  TemperatureController& ctl_;
  PeltierDriver& peltier_;
  MotorController* motor_;
  IAckSink* acks_;
  ClockSync& clock_;
  CommandProcessor cmds_;
  ExperimentManager exp_;
  RunState state_ = RunState::WaitingForSensors;

  char motor_cmd_id_[65] = {0};
  int64_t motor_cmd_seq_ = -1;
  enum class MotorStopCause : uint8_t { None, Door, Command } motor_stop_cause_ = MotorStopCause::None;

  bool image_requested_ = false;
  char image_cmd_id_[65] = {0};
  int64_t image_cmd_seq_ = -1;
};

}  // namespace fa
