// MockActuators — in-memory actuators and storage for native tests and the esp32s3cam_noio build.
#pragma once

#include <map>
#include <string>

#include "hal_interfaces.h"

namespace fa {

class MockDigitalOut : public IDigitalOut {
 public:
  void write(bool high) override {
    if (high && !state_) ++rising_edges;
    state_ = high;
    ++writes;
  }
  bool state() const override { return state_; }
  // Used by the mock one-shot timer to emulate the esp_timer gate cut-off.
  void forceLow() { state_ = false; }
  uint32_t writes = 0;
  uint32_t rising_edges = 0;

 private:
  bool state_ = false;
};

class MockPwmOut : public IPwmOut {
 public:
  void setFraction(float f) override {
    f_ = f < 0 ? 0 : (f > 1 ? 1 : f);
    if (f_ > max_seen) max_seen = f_;
  }
  float fraction() const override { return f_; }
  float max_seen = 0.0f;

 private:
  float f_ = 0.0f;
};

class MockOneShotTimer : public IOneShotTimer {
 public:
  explicit MockOneShotTimer(MockDigitalOut* gate = nullptr) : gate_(gate) {}
  bool start(uint32_t ms) override {
    armed = true;
    period_ms = ms;
    ++starts;
    return true;
  }
  void cancel() override { armed = false; }
  // Test hook: emulate the timer firing (e.g. when the main loop is "stalled").
  void fire() {
    if (armed && gate_) gate_->forceLow();
    armed = false;
  }
  bool armed = false;
  uint32_t period_ms = 0;
  uint32_t starts = 0;

 private:
  MockDigitalOut* gate_;
};

class MockKeyValueStore : public IKeyValueStore {
 public:
  bool getString(const char* key, char* out, size_t n) override;
  bool putString(const char* key, const char* value) override;
  bool getFloat(const char* key, float& out) override;
  bool putFloat(const char* key, float value) override;
  bool getI64(const char* key, int64_t& out) override;
  bool putI64(const char* key, int64_t value) override;
  bool remove(const char* key) override;
  size_t size() const { return s_.size() + f_.size() + i_.size(); }

 private:
  std::map<std::string, std::string> s_;
  std::map<std::string, float> f_;
  std::map<std::string, int64_t> i_;
};

}  // namespace fa
