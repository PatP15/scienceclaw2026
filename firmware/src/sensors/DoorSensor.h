// DoorSensor — debounced reed switch with configurable polarity, open count, time since change.
// A change is accepted only after the raw level has been stable for debounce_ms.
#pragma once

#include <stdint.h>

#include "hal_interfaces.h"

namespace fa {

class DoorSensor {
 public:
  DoorSensor(IDigitalIn* in, bool open_level_high, uint32_t debounce_ms)
      : in_(in), open_high_(open_level_high), debounce_ms_(debounce_ms) {}

  void begin(uint32_t now_ms);
  void update(uint32_t now_ms);

  bool known() const { return known_; }
  bool isOpen() const { return open_; }
  uint32_t openCount() const { return open_count_; }
  uint32_t msSinceChange(uint32_t now_ms) const { return now_ms - changed_ms_; }
  // True once after each debounced transition (for event logging).
  bool takeEdge(bool& now_open);

 private:
  bool levelToOpen(bool level) const { return level == open_high_; }
  IDigitalIn* in_;
  bool open_high_;
  uint32_t debounce_ms_;
  bool known_ = false;
  bool open_ = false;
  bool candidate_ = false;
  uint32_t candidate_since_ms_ = 0;
  uint32_t changed_ms_ = 0;
  uint32_t open_count_ = 0;
  bool edge_ = false;
};

}  // namespace fa
