// SHT31Sensor — optional interchangeable air T/RH sensor (Adafruit_SHT31), ESP32 only.
// Address 0x44 (ADDR low) or 0x45 (ADDR high). It can share the I2C bus with a BME680
// (0x76/0x77): no address conflict. If both are fitted, ENV_SENSOR selects which one provides
// chamber_temperature_c / RH; the other can be used for cross-checking during commissioning.
#pragma once

#include "hal_interfaces.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO
#include <Adafruit_SHT31.h>
#include <Wire.h>

namespace fa {

class SHT31Sensor : public IEnvSensor {
 public:
  explicit SHT31Sensor(TwoWire* wire) : sht_(wire) {}
  bool begin() override;
  bool present() const override { return present_; }
  const char* name() const override { return "sht31"; }
  uint32_t startMeasurement() override { return present_ ? 1 : 0; }
  bool fetch(EnvRaw& out) override;

 private:
  Adafruit_SHT31 sht_;
  bool present_ = false;
};

}  // namespace fa
#endif
