#include "sensors/DoorSensor.h"

namespace fa {

void DoorSensor::begin(uint32_t now_ms) {
  known_ = false;
  open_count_ = 0;
  edge_ = false;
  if (!in_) return;
  candidate_ = levelToOpen(in_->read());
  candidate_since_ms_ = now_ms;
  changed_ms_ = now_ms;
}

void DoorSensor::update(uint32_t now_ms) {
  if (!in_) return;
  const bool raw_open = levelToOpen(in_->read());
  if (raw_open != candidate_) {
    candidate_ = raw_open;
    candidate_since_ms_ = now_ms;
    return;
  }
  if ((uint32_t)(now_ms - candidate_since_ms_) < debounce_ms_) return;
  if (!known_) {
    known_ = true;
    open_ = candidate_;
    changed_ms_ = now_ms;
    return;  // initial state is not an "opening"
  }
  if (candidate_ != open_) {
    open_ = candidate_;
    changed_ms_ = now_ms;
    edge_ = true;
    if (open_) ++open_count_;
  }
}

bool DoorSensor::takeEdge(bool& now_open) {
  if (!edge_) return false;
  edge_ = false;
  now_open = open_;
  return true;
}

}  // namespace fa
