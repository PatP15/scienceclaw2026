#include "sensors/BME680Sensor.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO

namespace fa {

bool BME680Sensor::begin() {
  present_ = false;
  pending_ = false;
  for (uint8_t a : {(uint8_t)0x77, (uint8_t)0x76}) {
    if (bme_.begin(a, true)) {
      addr_ = a;
      present_ = true;
      break;
    }
  }
  if (!present_) return false;
  bme_.setTemperatureOversampling(BME680_OS_8X);
  bme_.setHumidityOversampling(BME680_OS_2X);
  bme_.setPressureOversampling(BME680_OS_4X);
  bme_.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme_.setGasHeater(320, 150);  // 320 C for 150 ms (Bosch/Adafruit example values)
  return true;
}

uint32_t BME680Sensor::startMeasurement() {
  if (!present_) return 0;
  const unsigned long end = bme_.beginReading();
  if (end == 0) {
    pending_ = false;
    if (++consecutive_failures_ >= 3) present_ = false;  // re-probe on next begin()
    return 0;
  }
  pending_ = true;
  const unsigned long now = millis();
  return end > now ? (uint32_t)(end - now) + 5 : 5;
}

bool BME680Sensor::fetch(EnvRaw& out) {
  if (!present_ || !pending_) return false;
  pending_ = false;
  if (!bme_.endReading()) {
    if (++consecutive_failures_ >= 3) present_ = false;
    return false;
  }
  consecutive_failures_ = 0;
  out.temperature_c = bme_.temperature;
  out.humidity_pct = bme_.humidity;
  out.pressure_pa = (float)bme_.pressure;  // library reports Pa
  out.gas_ohm = (float)bme_.gas_resistance;
  out.has_pressure = true;
  out.has_gas = true;
  return true;
}

}  // namespace fa
#endif
