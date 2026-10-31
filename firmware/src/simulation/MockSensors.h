// MockSensors — scriptable sensor doubles + a first-order thermal TEST plant.
//
// The plant exists only to exercise the controller in native tests and the noio firmware. It is
// NOT a validated physical model of the chamber; every value it produces is labelled
// source="simulated" / field_provenance "simulated".
#pragma once

#include <math.h>

#include "hal_interfaces.h"
#include "simulation/MockActuators.h"

namespace fa {

class MockDigitalIn : public IDigitalIn {
 public:
  bool read() override { return level; }
  bool level = false;
};

class MockAnalogIn : public IAnalogIn {
 public:
  bool sample(int32_t& raw, float& mv) override {
    if (fail) return false;
    mv = millivolts;
    raw = (int32_t)(millivolts / 3100.0f * 4095.0f);
    return true;
  }
  float millivolts = 1500.0f;
  bool fail = false;
};

class MockEnvSensor : public IEnvSensor {
 public:
  explicit MockEnvSensor(const char* name = "bme680") : name_(name) {}
  bool begin() override { return present_ = !absent; }
  bool present() const override { return present_; }
  const char* name() const override { return name_; }
  uint32_t startMeasurement() override { return (present_ && !fail_start) ? 160 : 0; }
  bool fetch(EnvRaw& out) override {
    if (!present_ || fail_fetch) return false;
    out = raw;
    return true;
  }
  EnvRaw raw;
  bool absent = false;
  bool fail_start = false;
  bool fail_fetch = false;
  void unplug() { present_ = false; absent = true; }

 private:
  const char* name_;
  bool present_ = false;
};

class MockOneWireBus : public IOneWireTempBus {
 public:
  struct Probe {
    RomAddr rom;
    float temp_c;
    bool connected;
    bool crc_error;
  };
  static constexpr size_t kMax = 8;

  size_t addProbe(const RomAddr& rom, float t) {
    if (n_ >= kMax) return n_;
    probes_[n_] = Probe{rom, t, true, false};
    return n_++;
  }
  Probe& probe(size_t i) { return probes_[i]; }
  size_t count() const { return n_; }

  size_t discover(RomAddr* out, size_t max) override {
    size_t k = 0;
    for (size_t i = 0; i < n_ && k < max; ++i)
      if (probes_[i].connected) out[k++] = probes_[i].rom;
    return k;
  }
  bool requestConversion() override {
    ++conversions;
    return !bus_fault;
  }
  uint32_t conversionTimeMs() const override { return 750; }
  bool readTempC(const RomAddr& rom, float& out) override {
    for (size_t i = 0; i < n_; ++i)
      if (probes_[i].rom == rom) {
        if (!probes_[i].connected || probes_[i].crc_error || bus_fault) return false;
        out = probes_[i].temp_c;
        return true;
      }
    return false;
  }
  bool bus_fault = false;
  uint32_t conversions = 0;

 private:
  Probe probes_[kMax];
  size_t n_ = 0;
};

// First-order thermal test plant driven by the two mock PWM channels.
class ThermalTestPlant {
 public:
  ThermalTestPlant(MockPwmOut* heat, MockPwmOut* cool, MockEnvSensor* env, MockOneWireBus* bus)
      : heat_(heat), cool_(cool), env_(env), bus_(bus) {}
  void step(float dt_s) {
    const float p = (heat_ ? heat_->fraction() : 0.f) - (cool_ ? cool_->fraction() : 0.f);
    // dT/dt = (ambient - T)/tau + gain * p     (tau and gain are arbitrary test values)
    chamber_c += dt_s * ((ambient_c - chamber_c) / tau_s + gain_c_per_s * p);
    starter_c += dt_s * (chamber_c - starter_c) / (tau_s * 2.0f);
    const float hot = fabsf(p) * 25.0f;
    int_hs_c = chamber_c + (p > 0 ? hot : -hot * 0.4f);
    ext_hs_c = ambient_c + (p < 0 ? hot : -hot * 0.4f);
    if (env_) {
      env_->raw.temperature_c = chamber_c;
      env_->raw.humidity_pct = 70.0f - 0.3f * (chamber_c - ambient_c);
      env_->raw.pressure_pa = 101325.0f;
      env_->raw.gas_ohm = 100000.0f;
      env_->raw.has_pressure = true;
      env_->raw.has_gas = true;
    }
    if (bus_ && bus_->count() >= 3) {
      bus_->probe(0).temp_c = starter_c;
      bus_->probe(1).temp_c = int_hs_c;
      bus_->probe(2).temp_c = ext_hs_c;
    }
  }
  float ambient_c = 21.0f;
  float chamber_c = 21.0f;
  float starter_c = 21.0f;
  float int_hs_c = 21.0f;
  float ext_hs_c = 21.0f;
  float tau_s = 900.0f;
  float gain_c_per_s = 0.02f;

 private:
  MockPwmOut* heat_;
  MockPwmOut* cool_;
  MockEnvSensor* env_;
  MockOneWireBus* bus_;
};

}  // namespace fa
