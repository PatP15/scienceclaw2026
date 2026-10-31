// ENS160AHT21Sensor — combined "ENS160 + AHT21" breakout on the sensor I2C bus (ESP32 only).
//
//  * AHT21 (0x38): chamber air temperature + relative humidity (Adafruit_AHTX0; AHT20-compatible).
//  * ENS160 (0x52 or 0x53): metal-oxide gas sensor. Minimal register driver below (no library).
//    It reports TVOC (ppb), eCO2 (ppm equivalent) and a 1-5 AQI index. Those are INDICES derived
//    by the chip's firmware, not a CO2 measurement, and they are compensated with the AHT21 T/RH.
//    Pressure and gas resistance are not available: EnvRaw.has_pressure/has_gas stay false.
//  * Warm-up: ENS160 needs ~3 min after power-up (first-ever use ~1 h) before the indices settle;
//    its DATA_STATUS "warm-up"/"initial start-up" bits are exposed via warmingUp().
#pragma once

#include "hal_interfaces.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO
#include <Adafruit_AHTX0.h>
#include <Wire.h>

namespace fa {

class ENS160AHT21Sensor : public IEnvSensor {
 public:
  explicit ENS160AHT21Sensor(TwoWire* wire) : wire_(wire) {}
  bool begin() override;
  bool present() const override { return aht_present_; }  // T/RH is the mandatory part
  const char* name() const override { return "ens160_aht21"; }
  uint32_t startMeasurement() override { return aht_present_ ? 1 : 0; }
  bool fetch(EnvRaw& out) override;

  // ENS160 extras (valid only when ensPresent() && ensDataValid()).
  bool ensPresent() const { return ens_present_; }
  bool ensDataValid() const { return ens_valid_; }
  bool warmingUp() const { return ens_warmup_; }
  uint16_t tvocPpb() const { return tvoc_ppb_; }
  uint16_t eco2Ppm() const { return eco2_ppm_; }
  uint8_t aqi() const { return aqi_; }
  uint8_t ensAddress() const { return ens_addr_; }

 private:
  bool ensWrite(uint8_t reg, const uint8_t* data, size_t n);
  bool ensRead(uint8_t reg, uint8_t* data, size_t n);
  bool ensBegin(uint8_t addr);
  void ensCompensate(float t_c, float rh_pct);
  bool ensPoll();

  TwoWire* wire_;
  Adafruit_AHTX0 aht_;
  bool aht_present_ = false;
  bool ens_present_ = false;
  bool ens_valid_ = false;
  bool ens_warmup_ = false;
  uint8_t ens_addr_ = 0;
  uint16_t tvoc_ppb_ = 0, eco2_ppm_ = 0;
  uint8_t aqi_ = 0;
  uint8_t fails_ = 0;
};

}  // namespace fa
#endif
