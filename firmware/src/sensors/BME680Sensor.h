// BME680Sensor — Adafruit_BME680 driver behind IEnvSensor (ESP32 only).
// Address detection: tries 0x77 (Adafruit default, SDO high) then 0x76 (SDO low).
// Non-blocking: startMeasurement() -> beginReading() (returns when the heater cycle completes),
// fetch() -> endReading() only after that time has passed.
#pragma once

#include "hal_interfaces.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO
#include <Adafruit_BME680.h>
#include <Wire.h>

namespace fa {

class BME680Sensor : public IEnvSensor {
 public:
  explicit BME680Sensor(TwoWire* wire) : bme_(wire) {}
  bool begin() override;
  bool present() const override { return present_; }
  const char* name() const override { return "bme680"; }
  uint32_t startMeasurement() override;
  bool fetch(EnvRaw& out) override;
  uint8_t address() const { return addr_; }

 private:
  Adafruit_BME680 bme_;
  bool present_ = false;
  bool pending_ = false;
  uint8_t addr_ = 0;
  uint8_t consecutive_failures_ = 0;
};

}  // namespace fa
#endif
