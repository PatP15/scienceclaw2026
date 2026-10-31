// Scheduler — tiny non-blocking cooperative scheduler. Each task runs when its period has
// elapsed; tasks must return quickly (no delay()). Overrun-safe with uint32 millis() wrap.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace fa {

class Scheduler {
 public:
  using Fn = void (*)(uint32_t now_ms, void* ctx);
  static constexpr size_t kMaxTasks = 16;

  bool add(const char* name, uint32_t period_ms, Fn fn, void* ctx, uint32_t first_delay_ms = 0) {
    if (n_ >= kMaxTasks || !fn || period_ms == 0) return false;
    tasks_[n_++] = Task{name, period_ms, fn, ctx, first_delay_ms, 0, 0, false};
    return true;
  }

  // Run every due task once. Returns the number of tasks run.
  size_t tick(uint32_t now_ms) {
    size_t ran = 0;
    for (size_t i = 0; i < n_; ++i) {
      Task& t = tasks_[i];
      if (!t.started) {
        t.next_ms = now_ms + t.first_delay_ms;
        t.started = true;
      }
      if ((int32_t)(now_ms - t.next_ms) >= 0) {
        t.fn(now_ms, t.ctx);
        // Skip missed periods instead of bursting to catch up.
        t.next_ms += t.period_ms;
        if ((int32_t)(now_ms - t.next_ms) >= 0) t.next_ms = now_ms + t.period_ms;
        ++t.runs;
        ++ran;
      }
    }
    return ran;
  }

  size_t size() const { return n_; }
  uint32_t runs(size_t i) const { return i < n_ ? tasks_[i].runs : 0; }
  const char* name(size_t i) const { return i < n_ ? tasks_[i].name : nullptr; }

 private:
  struct Task {
    const char* name;
    uint32_t period_ms;
    Fn fn;
    void* ctx;
    uint32_t first_delay_ms;
    uint32_t next_ms;
    uint32_t runs;
    bool started;
  };
  Task tasks_[kMaxTasks];
  size_t n_ = 0;
};

}  // namespace fa
