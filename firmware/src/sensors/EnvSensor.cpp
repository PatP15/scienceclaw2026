#include "sensors/EnvSensor.h"

#include <math.h>

#include "device_config.h"

namespace fa {

EnvValidated validateEnv(const EnvRaw& raw, uint32_t now_ms, Provenance prov, float temp_offset_c) {
  EnvValidated v;
  const float t = raw.temperature_c + temp_offset_c;
  if (isfinite(t) && t >= limits::kPlausibleMinC && t <= limits::kPlausibleMaxC)
    v.temperature = Reading::ok(t, now_ms, prov);
  else {
    v.temperature = Reading::bad(SensorHealth::Invalid, now_ms);
    v.any_invalid = true;
  }
  // RH exactly 0 or 100 from a BME680 almost always means a failed/saturated read.
  if (isfinite(raw.humidity_pct) && raw.humidity_pct > 0.0f && raw.humidity_pct < 100.0f)
    v.humidity = Reading::ok(raw.humidity_pct, now_ms, prov);
  else {
    v.humidity = Reading::bad(SensorHealth::Invalid, now_ms);
    v.any_invalid = true;
  }
  if (raw.has_pressure) {
    if (isfinite(raw.pressure_pa) && raw.pressure_pa >= 30000.0f && raw.pressure_pa <= 110000.0f)
      v.pressure = Reading::ok(raw.pressure_pa, now_ms, prov);
    else {
      v.pressure = Reading::bad(SensorHealth::Invalid, now_ms);
      v.any_invalid = true;
    }
  } else {
    v.pressure = Reading::bad(SensorHealth::Disabled, now_ms);
  }
  if (raw.has_gas) {
    if (isfinite(raw.gas_ohm) && raw.gas_ohm >= 1.0f && raw.gas_ohm <= 1.0e8f)
      v.gas = Reading::ok(raw.gas_ohm, now_ms, prov);
    else {
      v.gas = Reading::bad(SensorHealth::Invalid, now_ms);
      v.any_invalid = true;
    }
  } else {
    v.gas = Reading::bad(SensorHealth::Disabled, now_ms);
  }
  return v;
}

}  // namespace fa
