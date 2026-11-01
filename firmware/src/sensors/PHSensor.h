// PHSensor — reads the CONDITIONED output of a pH interface board (never the raw glass electrode)
// on an ADC1 pin. Two-point calibration (pH 4 and pH 7 buffers) stored in NVS together with a
// calibration date (wall-clock if synced, otherwise "unsynced+<uptime>ms" — left unset).
//
// Published: raw ADC counts, calibrated millivolts, pH estimate, calibration validity, health.
// pH is reported (non-null) ONLY when the sensor is enabled, the calibration is valid and the
// voltage is inside the safe ADC window. Otherwise ph = null with a quality flag.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "fermentai_types.h"
#include "hal_interfaces.h"

namespace fa {

struct PhCalibration {
  bool has_ph4 = false;
  bool has_ph7 = false;
  float mv_ph4 = NAN;
  float mv_ph7 = NAN;
  char date[40] = {0};

  bool valid(float min_span_mv) const;
  float slopeMvPerPh() const { return (mv_ph7 - mv_ph4) / 3.0f; }  // mV per pH unit
  float toPh(float mv) const { return 7.0f + (mv - mv_ph7) / slopeMvPerPh(); }
};

class PHSensor {
 public:
  struct Config {
    bool enabled = false;
    uint16_t samples = 16;
    float adc_min_mv = 50.0f;
    float adc_max_mv = 3000.0f;
    float min_span_mv = 100.0f;
    uint32_t stale_ms = 20000;
  };

  PHSensor(IAnalogIn* adc, IKeyValueStore* kv, const Config& cfg, Provenance prov)
      : adc_(adc), kv_(kv), cfg_(cfg), prov_(prov) {}

  void begin();
  void update(uint32_t now_ms);  // takes cfg.samples samples and averages them

  // Record the CURRENT averaged voltage as the pH 4 or pH 7 point. date_text from the caller.
  bool calibratePoint(int buffer_ph, const char* date_text);
  void clearCalibration();
  const PhCalibration& calibration() const { return cal_; }

  bool enabled() const { return cfg_.enabled; }
  Reading ph(uint32_t now_ms) const;
  Reading voltageMv(uint32_t now_ms) const;
  int32_t rawAdc() const { return raw_; }
  SensorHealth health(uint32_t now_ms) const;
  bool calibrated() const { return cal_.valid(cfg_.min_span_mv); }
  bool voltageInWindow() const;

 private:
  void save();
  IAnalogIn* adc_;
  IKeyValueStore* kv_;
  Config cfg_;
  Provenance prov_;
  PhCalibration cal_;
  int32_t raw_ = -1;
  float mv_ = NAN;
  bool have_ = false;
  uint32_t t_ms_ = 0;
};

}  // namespace fa
