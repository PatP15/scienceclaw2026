// AckQueue — bounded, thread-safe FIFO of acknowledgments. The control loop pushes (via
// IAckSink), the network task pops and posts. If full, the OLDEST ack is dropped and counted
// (fresh acks matter more: the server retires unacked commands as expired_unacked anyway).
#pragma once

#include <deque>
#include <mutex>

#include "app/CommandProcessor.h"

namespace fa {

class AckQueue : public IAckSink {
 public:
  explicit AckQueue(size_t cap = 24) : cap_(cap) {}
  bool pushAck(const AckRecord& a) override {
    std::lock_guard<std::mutex> g(m_);
    if (q_.size() >= cap_) {
      q_.pop_front();
      ++dropped_;
    }
    q_.push_back(a);
    return true;
  }
  bool pop(AckRecord& out) {
    std::lock_guard<std::mutex> g(m_);
    if (q_.empty()) return false;
    out = q_.front();
    q_.pop_front();
    return true;
  }
  void pushFront(const AckRecord& a) {
    std::lock_guard<std::mutex> g(m_);
    if (q_.size() >= cap_) {
      ++dropped_;
      return;
    }
    q_.push_front(a);
  }
  size_t size() {
    std::lock_guard<std::mutex> g(m_);
    return q_.size();
  }
  uint32_t dropped() const { return dropped_; }

 private:
  size_t cap_;
  std::deque<AckRecord> q_;
  std::mutex m_;
  uint32_t dropped_ = 0;
};

}  // namespace fa
