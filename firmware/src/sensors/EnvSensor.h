// EnvSensor — validation of BME680 / SHT31 readings (portable) shared by the real drivers in
// BME680Sensor.cpp / SHT31Sensor.cpp and the mock.
//
// BME680 notes (see docs/SENSOR_WIRING.md):
//  * Gas resistance is a broad VOC/humidity/temperature-sensitive resistance. It is NOT CO2 and
//    is never converted to ppm. It drifts strongly during warm-up: first-ever use needs ~48 h
//    burn-in; after each power-up allow >= 5 min (flag "sensor_warming_up").
//  * The gas heater (320 C for 150 ms per cycle) and the die self-heat the temperature reading
//    by roughly 0.5-2 C; kBme680TempOffsetC corrects it after comparison with a DS18B20.
#pragma once

#include <stdint.h>

#include "fermentai_types.h"
#include "hal_interfaces.h"

namespace fa {

struct EnvValidated {
  Reading temperature;
  Reading humidity;
  Reading pressure;
  Reading gas;
  bool any_invalid = false;
};

// Range checks: T -20..80 C, RH 0..100 %, P 30-110 kPa, gas 1 ohm..100 Mohm.
EnvValidated validateEnv(const EnvRaw& raw, uint32_t now_ms, Provenance prov, float temp_offset_c);

}  // namespace fa
