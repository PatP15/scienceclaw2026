#include "sensors/SHT31Sensor.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO
#include <math.h>

namespace fa {

bool SHT31Sensor::begin() {
  present_ = sht_.begin(0x44) || sht_.begin(0x45);
  if (present_) sht_.heater(false);
  return present_;
}

bool SHT31Sensor::fetch(EnvRaw& out) {
  if (!present_) return false;
  float t = NAN, h = NAN;
  // Single-shot measurement, ~15 ms clock-stretch-free; CRC checked by the library (NaN on error).
  if (!sht_.readBoth(&t, &h)) return false;
  if (isnan(t) || isnan(h)) return false;
  out.temperature_c = t;
  out.humidity_pct = h;
  out.has_pressure = false;
  out.has_gas = false;
  return true;
}

}  // namespace fa
#endif
