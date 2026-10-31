#include "sensors/ENS160AHT21Sensor.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO
#include <math.h>

namespace fa {
namespace {
// ENS160 register map (ScioSense ENS160 datasheet, rev 0.95, §7)
constexpr uint8_t kRegPartId = 0x00;      // 2 bytes, expect 0x0160
constexpr uint8_t kRegOpMode = 0x10;      // 0x00 deep sleep, 0x01 idle, 0x02 standard, 0xF0 reset
constexpr uint8_t kRegTempIn = 0x13;      // 2 bytes, Kelvin * 64
constexpr uint8_t kRegRhIn = 0x15;        // 2 bytes, %RH * 512
constexpr uint8_t kRegDeviceStatus = 0x20;
constexpr uint8_t kRegDataAqi = 0x21;     // 1 byte (bits 0-2)
constexpr uint8_t kRegDataTvoc = 0x22;    // 2 bytes ppb
constexpr uint8_t kRegDataEco2 = 0x24;    // 2 bytes ppm
constexpr uint16_t kPartId = 0x0160;
constexpr uint8_t kOpStandard = 0x02;
constexpr uint8_t kOpReset = 0xF0;
// DEVICE_STATUS bits
constexpr uint8_t kStatErr = 0x40;
constexpr uint8_t kStatNewData = 0x02;
constexpr uint8_t kStatValidityMask = 0x0C;  // 0 normal, 1 warm-up, 2 initial start-up, 3 invalid
}  // namespace

bool ENS160AHT21Sensor::ensWrite(uint8_t reg, const uint8_t* data, size_t n) {
  wire_->beginTransmission(ens_addr_);
  wire_->write(reg);
  for (size_t i = 0; i < n; ++i) wire_->write(data[i]);
  return wire_->endTransmission() == 0;
}

bool ENS160AHT21Sensor::ensRead(uint8_t reg, uint8_t* data, size_t n) {
  wire_->beginTransmission(ens_addr_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0) return false;
  if (wire_->requestFrom((int)ens_addr_, (int)n) != (int)n) return false;
  for (size_t i = 0; i < n; ++i) data[i] = wire_->read();
  return true;
}

bool ENS160AHT21Sensor::ensBegin(uint8_t addr) {
  ens_addr_ = addr;
  uint8_t id[2];
  if (!ensRead(kRegPartId, id, 2)) return false;
  if ((uint16_t)(id[0] | (id[1] << 8)) != kPartId) return false;
  uint8_t op = kOpReset;
  ensWrite(kRegOpMode, &op, 1);
  delay(10);
  op = kOpStandard;
  if (!ensWrite(kRegOpMode, &op, 1)) return false;
  delay(20);
  return true;
}

void ENS160AHT21Sensor::ensCompensate(float t_c, float rh_pct) {
  if (!ens_present_ || isnan(t_c) || isnan(rh_pct)) return;
  const uint16_t t = (uint16_t)lroundf((t_c + 273.15f) * 64.0f);
  const uint16_t h = (uint16_t)lroundf(rh_pct * 512.0f);
  const uint8_t buf[4] = {(uint8_t)(t & 0xFF), (uint8_t)(t >> 8), (uint8_t)(h & 0xFF), (uint8_t)(h >> 8)};
  ensWrite(kRegTempIn, buf, 4);
}

bool ENS160AHT21Sensor::ensPoll() {
  uint8_t st;
  if (!ensRead(kRegDeviceStatus, &st, 1)) return false;
  if (st & kStatErr) return false;
  const uint8_t validity = (st & kStatValidityMask) >> 2;
  ens_warmup_ = (validity == 1 || validity == 2);
  if (validity == 3) return false;
  uint8_t d[5];
  if (!ensRead(kRegDataAqi, d, 5)) return false;
  aqi_ = d[0] & 0x07;
  tvoc_ppb_ = (uint16_t)(d[1] | (d[2] << 8));
  eco2_ppm_ = (uint16_t)(d[3] | (d[4] << 8));
  return true;
}

bool ENS160AHT21Sensor::begin() {
  aht_present_ = aht_.begin(wire_, 0, 0x38);
  ens_present_ = ensBegin(0x53) || ensBegin(0x52);
  if (!ens_present_) ens_addr_ = 0;
  ens_valid_ = false;
  fails_ = 0;
  return aht_present_;
}

bool ENS160AHT21Sensor::fetch(EnvRaw& out) {
  if (!aht_present_) return false;
  sensors_event_t hum, temp;
  if (!aht_.getEvent(&hum, &temp)) {
    if (++fails_ >= 3) aht_present_ = false;  // SensorManager re-runs begin() later
    return false;
  }
  fails_ = 0;
  if (isnan(temp.temperature) || isnan(hum.relative_humidity)) return false;
  out.temperature_c = temp.temperature;
  out.humidity_pct = hum.relative_humidity;
  out.has_pressure = false;
  out.has_gas = false;
  if (ens_present_) {
    ensCompensate(out.temperature_c, out.humidity_pct);
    ens_valid_ = ensPoll();
  }
  return true;
}

}  // namespace fa
#endif
