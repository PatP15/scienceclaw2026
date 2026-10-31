// ExperimentManager — the setpoint/experiment assignment currently in force on this chamber.
// Nothing here is restored from flash at boot: after a reset the chamber stays passive until a
// FRESH control command arrives (contract §5.9).
#pragma once

#include <stdint.h>
#include <string.h>

#include "command_schema.h"
#include "fermentai_types.h"

namespace fa {

class ExperimentManager {
 public:
  void apply(float setpoint_c, ControlMode mode, const char* experiment_id, uint32_t now_ms) {
    setpoint_ = Reading::ok(setpoint_c, now_ms, Provenance::None);
    mode_ = mode;
    if (experiment_id && experiment_id[0]) {
      strncpy(experiment_id_, experiment_id, sizeof experiment_id_ - 1);
      experiment_id_[sizeof experiment_id_ - 1] = 0;
    }
    ++changes_;
  }
  bool hasSetpoint() const { return setpoint_.valid; }
  float setpoint() const { return setpoint_.value; }
  const Reading& setpointReading() const { return setpoint_; }
  ControlMode mode() const { return mode_; }
  const char* experimentId() const { return experiment_id_[0] ? experiment_id_ : nullptr; }
  uint32_t changes() const { return changes_; }

 private:
  Reading setpoint_;
  ControlMode mode_ = ControlMode::Auto;
  char experiment_id_[65] = {0};
  uint32_t changes_ = 0;
};

}  // namespace fa
