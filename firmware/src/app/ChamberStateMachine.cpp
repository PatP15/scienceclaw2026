#include "app/ChamberStateMachine.h"

#include <stdio.h>
#include <string.h>

namespace fa {

const char* runStateName(RunState s) {
  switch (s) {
    case RunState::WaitingForSensors: return "waiting_for_sensors";
    case RunState::WaitingForCommand: return "waiting_for_command";
    case RunState::Active: return "active";
    case RunState::OfflineHold: return "offline_idle";
  }
  return "unknown";
}

void ChamberStateMachine::begin(uint32_t now_ms) {
  peltier_.begin(now_ms);  // outputs off
  if (motor_) motor_->begin();  // gate low
  safety_.begin(now_ms);
  ctl_.reset();
  cmds_.begin();
  state_ = RunState::WaitingForSensors;
}

void ChamberStateMachine::safetyTick(uint32_t now_ms) {
  SafetyInputs in;
  const ChamberObservation o = sensors_.observe(now_ms);
  in.chamber = o.chamber_temp;
  in.int_heatsink = o.int_heatsink;
  in.ext_heatsink = o.ext_heatsink;
  in.heatsink_probes_required = cfg_.heatsink_probes_required;
  safety_.update(in, now_ms);
  if (safety_.tripped()) {
    peltier_.forceFault(now_ms);
    ctl_.reset();
  } else if (peltier_.mode() == PeltierMode::Fault) {
    peltier_.clearFault(now_ms);
  }
  // Door open: Peltier idle (handled in controlTick) and the motor must stop immediately.
  if (sensors_.doorOpen() && motor_ && motor_->isMotorRunning()) {
    motor_stop_cause_ = MotorStopCause::Door;
    motor_->stopMotor(now_ms);
  }
  if (sensors_.doorOpen() && !safety_.tripped()) {
    peltier_.request(Direction::None, 0.0f, now_ms);
  }
  peltier_.update(now_ms);
}

void ChamberStateMachine::controlTick(uint32_t now_ms) {
  const Reading t = sensors_.chamberTemp(now_ms);
  // ---- run-state transitions
  switch (state_) {
    case RunState::WaitingForSensors:
      if (t.valid) state_ = RunState::WaitingForCommand;
      break;
    case RunState::WaitingForCommand:
      if (cmds_.hasValidControl() && exp_.hasSetpoint() &&
          (uint32_t)(now_ms - cmds_.lastValidControlMs()) < cfg_.offline_hold_ms)
        state_ = RunState::Active;
      break;
    case RunState::Active:
      if ((uint32_t)(now_ms - cmds_.lastValidControlMs()) >= cfg_.offline_hold_ms)
        state_ = RunState::OfflineHold;
      break;
    case RunState::OfflineHold:
      if ((uint32_t)(now_ms - cmds_.lastValidControlMs()) < cfg_.offline_hold_ms)
        state_ = RunState::Active;
      break;
  }
  Direction dir = Direction::None;
  float frac = 0.0f;
  const bool may_control = state_ == RunState::Active && !safety_.tripped() && t.valid &&
                           !sensors_.doorOpen() && exp_.hasSetpoint() &&
                           exp_.mode() != ControlMode::Idle;
  if (may_control) {
    const ControlDemand d = ctl_.compute(t.value, exp_.setpoint());
    dir = d.dir;
    frac = d.fraction;
  } else {
    ctl_.reset();
  }
  peltier_.request(dir, frac, now_ms);
  peltier_.update(now_ms);
}

void ChamberStateMachine::ackMotor(AckStatus st, const char* msg, int64_t ran_ms, uint32_t now_ms) {
  if (!motor_cmd_id_[0] || !acks_) return;
  AckRecord a;
  strncpy(a.command_id, motor_cmd_id_, sizeof a.command_id - 1);
  a.status = st;
  a.action = CmdAction::AnimateMotor;
  a.sequence_number = motor_cmd_seq_;
  a.ran_ms = ran_ms;
  if (msg) strncpy(a.message, msg, sizeof a.message - 1);
  a.uptime_ms = now_ms;
  a.epoch_ms = clock_.nowEpochMs(now_ms);
  acks_->pushAck(a);
}

void ChamberStateMachine::motorTick(uint32_t now_ms) {
  if (!motor_) return;
  motor_->update(now_ms);
  MotorController::StopReason why;
  uint32_t ran = 0;
  if (motor_->takeFinished(why, ran)) {
    char msg[64];
    switch (why) {
      case MotorController::StopReason::Completed:
      case MotorController::StopReason::Timeout:
        snprintf(msg, sizeof msg, "ran %.1f s", ran / 1000.0);
        ackMotor(AckStatus::Completed, msg, ran, now_ms);
        break;
      case MotorController::StopReason::Stopped:
        if (motor_stop_cause_ == MotorStopCause::Door) {
          snprintf(msg, sizeof msg, "stopped after %lu ms: door opened", (unsigned long)ran);
          ackMotor(AckStatus::Failed, msg, ran, now_ms);
        }
        // MotorStopCause::Command: CommandProcessor already acked "cancelled".
        break;
      case MotorController::StopReason::Disarmed:
        ackMotor(AckStatus::Failed, "motor disarmed while running", ran, now_ms);
        break;
      default:
        break;
    }
    motor_cmd_id_[0] = 0;
    motor_cmd_seq_ = -1;
    motor_stop_cause_ = MotorStopCause::None;
  }
}

void ChamberStateMachine::applySetpoint(float clamped_c, ControlMode mode, const char* experiment_id,
                                        uint32_t now_ms) {
  exp_.apply(clamped_c, mode, experiment_id, now_ms);
}

bool ChamberStateMachine::startMotor(uint32_t duration_ms, const char* pattern, const char* command_id,
                                     int64_t seq, uint32_t now_ms, uint32_t* granted_ms, char* why,
                                     size_t why_n) {
  auto fail = [&](const char* m) {
    snprintf(why, why_n, "%s", m);
    return false;
  };
  if (!motor_) return fail("motor not enabled on this device");
  if (!motor_->armed()) return fail("motor not armed (commissioning)");
  if (sensors_.doorOpen()) return fail("door open");
  // Refuse during a genuine thermal hazard (operator attention needed). Sensor-health faults
  // that only inhibit the Peltier (e.g. heatsink probe unassigned) do not block the motor.
  if (safety_.faults() & (kFaultOvertemp | kFaultUndertemp | kFaultHeatsinkOvertemp))
    return fail("local thermal fault");
  if (motor_->isMotorRunning()) return fail("motor already running");
  if (!motor_->startMotor(duration_ms, now_ms, MotorController::parsePattern(pattern), granted_ms))
    return fail("motor refused start");
  strncpy(motor_cmd_id_, command_id, sizeof motor_cmd_id_ - 1);
  motor_cmd_id_[sizeof motor_cmd_id_ - 1] = 0;
  motor_cmd_seq_ = seq;
  motor_stop_cause_ = MotorStopCause::None;
  return true;
}

bool ChamberStateMachine::stopMotor(uint32_t now_ms, char* stopped_id, size_t n) {
  if (!motor_) return false;
  const bool was = motor_->isMotorRunning();
  if (was) {
    if (stopped_id && n) snprintf(stopped_id, n, "%s", motor_cmd_id_);
    motor_stop_cause_ = MotorStopCause::Command;
  }
  motor_->stopMotor(now_ms);
  return was;
}

bool ChamberStateMachine::requestImage(const char* command_id, int64_t seq, char* why, size_t why_n) {
  if (!cfg_.camera_available) {
    snprintf(why, why_n, "camera not available");
    return false;
  }
  image_requested_ = true;
  snprintf(image_cmd_id_, sizeof image_cmd_id_, "%s", command_id);
  image_cmd_seq_ = seq;
  return true;
}

bool ChamberStateMachine::takeImageRequest(char* command_id, size_t n, int64_t& seq) {
  if (!image_requested_) return false;
  image_requested_ = false;
  snprintf(command_id, n, "%s", image_cmd_id_);
  seq = image_cmd_seq_;
  return true;
}

PeltierMode ChamberStateMachine::reportedMode() const {
  const PeltierMode m = peltier_.mode();
  if (m == PeltierMode::Fault || m == PeltierMode::Switching) return m;
  // Report what is physically happening: a disarmed driver energises nothing.
  if (!peltier_.armed()) return PeltierMode::Idle;
  return m;
}

void ChamberStateMachine::fillTelemetry(TelemetryRecord& r, uint32_t now_ms) const {
  r.obs = sensors_.observe(now_ms);
  r.chamber_id = cfg_.chamber_id;
  r.experiment_id = exp_.experimentId();
  r.peltier_mode = reportedMode();
  r.peltier_output_fraction = peltier_.appliedFraction();
  r.setpoint = exp_.setpointReading();
  r.motor_running = motor_ && motor_->isMotorRunning();
  r.fault_bits = safety_.faults();
  r.last_command_id = cmds_.lastCommandId();
  r.last_sequence_number = cmds_.lastSequence();
  if (!peltier_.armed()) r.quality_flags.add("peltier_output_disarmed");
  if (motor_ && !motor_->armed()) r.quality_flags.add("motor_disarmed");
  if (safety_.tripped()) r.quality_flags.add("local_safety_fault");
  switch (state_) {
    case RunState::WaitingForSensors: r.quality_flags.add("control_waiting_for_sensors"); break;
    case RunState::WaitingForCommand: r.quality_flags.add("control_waiting_for_command"); break;
    case RunState::OfflineHold: r.quality_flags.add("offline_hold_elapsed"); break;
    default: break;
  }
  if (peltier_.mode() == PeltierMode::Switching) r.quality_flags.add("peltier_dead_time");
  if (exp_.hasSetpoint() && exp_.mode() == ControlMode::Idle) r.quality_flags.add("control_mode_idle");
}

}  // namespace fa
