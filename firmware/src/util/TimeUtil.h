// TimeUtil — ISO-8601 parsing/formatting and wall-clock tracking that never invents time.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "fermentai_types.h"

namespace fa {

// Parse "YYYY-MM-DDTHH:MM:SS[.fff][Z|+00:00]" (UTC only) to Unix epoch milliseconds.
bool parseIso8601Utc(const char* s, int64_t& epoch_ms);
// Format epoch ms as "YYYY-MM-DDTHH:MM:SSZ". buf must hold >= 21 bytes.
void formatIso8601Utc(int64_t epoch_ms, char* buf, size_t n);

// Wall-clock estimate = (server/SNTP time at sync) + (monotonic ms elapsed since sync).
// Before the first sync the device has NO wall-clock time and reports timestamp_utc = null.
class ClockSync {
 public:
  void syncFrom(int64_t epoch_ms, uint32_t now_ms) {
    base_epoch_ms_ = epoch_ms;
    base_ms_ = now_ms;
    synced_ = true;
  }
  bool hasSync() const { return synced_; }
  // Returns -1 when unsynced.
  int64_t nowEpochMs(uint32_t now_ms) const {
    if (!synced_) return -1;
    return base_epoch_ms_ + (int64_t)(uint32_t)(now_ms - base_ms_);
  }
  TimestampQuality quality(uint32_t now_ms, uint32_t estimated_after_ms) const {
    if (!synced_) return TimestampQuality::Unsynced;
    return (uint32_t)(now_ms - base_ms_) > estimated_after_ms ? TimestampQuality::Estimated
                                                              : TimestampQuality::Synced;
  }

 private:
  bool synced_ = false;
  int64_t base_epoch_ms_ = 0;
  uint32_t base_ms_ = 0;
};

// Exponential backoff 1 s, 2 s, 4 s ... capped (contract §3).
class Backoff {
 public:
  explicit Backoff(uint32_t base_ms = 1000, uint32_t max_ms = 30000) : base_(base_ms), max_(max_ms) {}
  void success() { failures_ = 0; }
  void failure(uint32_t now_ms) {
    if (failures_ < 16) ++failures_;
    next_ms_ = now_ms + delayMs();
  }
  uint32_t delayMs() const {
    if (failures_ == 0) return 0;
    uint64_t d = (uint64_t)base_ << (failures_ - 1);
    return d > max_ ? max_ : (uint32_t)d;
  }
  bool ready(uint32_t now_ms) const { return failures_ == 0 || (int32_t)(now_ms - next_ms_) >= 0; }
  uint8_t failures() const { return failures_; }

 private:
  uint32_t base_, max_;
  uint8_t failures_ = 0;
  uint32_t next_ms_ = 0;
};

}  // namespace fa
