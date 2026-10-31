// PidMode — adapter between PidController and TemperatureController / PeltierDriver.
//
// TemperatureController (patched, see docs/PID_PATCH.md) calls PidMode::compute() when the selected
// mode is Pid and gets back the same ControlDemand{dir, fraction} the other modes return, so
// everything downstream (PeltierDriver request -> dead time, ramp, PWM cap, FAULT, door idle,
// disarm) is unchanged. PidMode adds:
//   * deadband with hysteresis: idle -> active when |u| >= min_fraction, active -> idle when
//     |u| < min_fraction * stop_ratio (0.5); active fraction is clamped to [min, max];
//   * heat<->cool dead time: after a demand in one direction, the opposite direction is refused
//     (demand None, integral frozen) until dead_time_ms has passed since the last non-zero demand.
//     This is a belt-and-braces copy of the PeltierDriver rule; the driver still enforces it.
//   * gains + selected mode persisted in NVS key "pid" as "kp ki kd alpha mode".
#pragma once

#include <stdint.h>

#include "control/PidController.h"
#include "control/TemperatureController.h"  // ControlDemand
#include "fermentai_types.h"
#include "hal_interfaces.h"

namespace fa {

enum class ControlSel : uint8_t { Hysteresis = 0, Proportional = 1, Pid = 2 };
const char* controlSelName(ControlSel s);       // "hyst" | "prop" | "pid"
bool parseControlSel(const char* s, ControlSel& out);

struct PidModeConfig {
  float min_fraction = 0.15f;
  float max_fraction = 0.8f;
  float stop_ratio = 0.5f;            // deadband hysteresis
  uint32_t dead_time_ms = 60000;      // = limits::kHeatCoolDeadTimeMs
  uint32_t max_dt_ms = 10000;         // longer gap between samples -> derivative restarted
};

// Allowed ranges for console / NVS values (units in PidGains).
struct PidLimits {
  static constexpr float kKpMax = 2.0f;
  static constexpr float kKiMax = 0.05f;
  static constexpr float kKdMax = 500.0f;
  static constexpr float kAlphaMax = 0.99f;
};
bool pidGainsValid(const PidGains& g);

class PidMode {
 public:
  explicit PidMode(const PidModeConfig& cfg = PidModeConfig());

  ControlSel selected() const { return sel_; }
  void select(ControlSel s) { sel_ = s; }
  const PidGains& gains() const { return pid_.gains(); }
  // Returns false (and keeps the old gains) when out of range / non-finite.
  bool setGains(const PidGains& g);
  const PidController& pid() const { return pid_; }
  const PidModeConfig& config() const { return cfg_; }
  bool active() const { return active_; }
  bool inDeadTimeHold() const { return hold_; }

  // temp/setpoint must be finite (else the state is reset and None returned).
  ControlDemand compute(float temp_c, float setpoint_c, uint32_t now_ms);
  // FAULT / door open / idle / invalid sensor: clears I and D. Dead-time memory is KEPT because the
  // output stage was still energised in that direction.
  void reset();
  // Mode switched to Pid while running: initialise I so u continues from the current signed output.
  void engage(float current_signed_output) { pid_.engage(current_signed_output); }

  // NVS persistence (key "pid"). load() falls back to defaults on a missing/corrupt entry.
  bool load(IKeyValueStore* kv);
  bool save(IKeyValueStore* kv) const;

  static constexpr const char* kNvsKey = "pid";

 private:
  PidModeConfig cfg_;
  PidController pid_;
  ControlSel sel_ = ControlSel::Proportional;  // default = existing behaviour
  bool active_ = false;
  bool hold_ = false;
  Direction last_dir_ = Direction::None;       // last direction demanded (kept across reset())
  uint32_t last_on_ms_ = 0;
  uint32_t last_ms_ = 0;
  bool have_ms_ = false;
};

}  // namespace fa
