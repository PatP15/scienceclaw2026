#include "hal/ArduinoHal.h"

#if defined(ARDUINO)
#include <driver/gpio.h>

#include "board_config.h"

namespace fa {

BootPinSnapshot g_bootPins;

void holdOutputsSafe() {
#if !FERMENTAI_NOIO
  // Bring-up diagnostic (BRINGUP step 2, partial): sample each power-control pin as a plain
  // input (no internal pull) BEFORE driving it. A board pull-up (e.g. SD-card lines 38/40) reads 1
  // here; that is the level the pin sat at during the boot window. Also sample the ALT-map pins.
  const int probe[BootPinSnapshot::kN] = {pins::kPeltierPwmA, pins::kPeltierPwmB, pins::kPeltierEnable,
                                          pins::kMotorGate,   pins::kPeltierEnableAlt, pins::kMotorGateAlt};
  for (int i = 0; i < BootPinSnapshot::kN; ++i) {
    g_bootPins.gpio[i] = probe[i];
    pinMode(probe[i], INPUT);
    delayMicroseconds(20);
    g_bootPins.level[i] = (uint8_t)digitalRead(probe[i]);
    // Second sample with the weak internal pull-down (~45 k): a floating pin now reads 0, a pin
    // with a real board pull-up (~10 k) still reads 1. Pull-down is the safe direction for a
    // gate/enable line, and the pin is driven LOW right after this anyway.
    pinMode(probe[i], INPUT_PULLDOWN);
    delayMicroseconds(20);
    g_bootPins.level_pd[i] = (uint8_t)digitalRead(probe[i]);
    pinMode(probe[i], INPUT);
  }
  // Power-control outputs LOW immediately. External pull-downs keep them LOW during reset/boot
  // before this code runs (docs/SENSOR_WIRING.md) — software cannot cover that window.
  const int outs[] = {pins::kPeltierPwmA, pins::kPeltierPwmB, pins::kPeltierEnablePin,
                      pins::kMotorPin};
  for (int p : outs) {
    if (p < 0) continue;
    digitalWrite(p, LOW);
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }
#endif
}

// ------------------------------------------------------------------ GPIO
void GpioOut::begin() {
  if (pin_ < 0) return;
  digitalWrite(pin_, LOW);
  pinMode(pin_, OUTPUT);
  digitalWrite(pin_, LOW);
  state_ = false;
}
void GpioOut::write(bool high) {
  state_ = high;
  if (pin_ >= 0) digitalWrite(pin_, high ? HIGH : LOW);
}

void GpioIn::begin() {
  if (pin_ >= 0) pinMode(pin_, pullup_ ? INPUT_PULLUP : INPUT);
}
bool GpioIn::read() { return pin_ >= 0 && digitalRead(pin_) == HIGH; }

// ------------------------------------------------------------------ LEDC
void LedcPwmOut::begin() {
  if (pin_ < 0) return;
  ledcSetup(ch_, freq_, bits_);
  ledcAttachPin(pin_, ch_);
  ledcWrite(ch_, 0);
  attached_ = true;
  f_ = 0.0f;
}
void LedcPwmOut::setFraction(float f) {
  if (!(f > 0.0f)) f = 0.0f;  // NaN -> 0
  if (f > 1.0f) f = 1.0f;
  f_ = f;
  if (!attached_) return;
  const uint32_t max = (1u << bits_) - 1u;
  ledcWrite(ch_, (uint32_t)(f * (float)max + 0.5f));
}

// ------------------------------------------------------------------ ADC
void AdcIn::begin() {
  if (pin_ < 0) return;
  analogReadResolution(12);
  analogSetPinAttenuation(pin_, ADC_11db);  // ~0-3.1 V usable range on ESP32-S3
}
bool AdcIn::sample(int32_t& raw, float& mv) {
  if (pin_ < 0) return false;
  raw = analogRead(pin_);
  mv = (float)analogReadMilliVolts(pin_);  // eFuse-calibrated conversion
  return raw >= 0;
}

// ------------------------------------------------------------------ NVS
bool NvsStore::begin() {
  ok_ = prefs_.begin("fermentai", false);
  return ok_;
}
bool NvsStore::getString(const char* key, char* out, size_t n) {
  if (!ok_ || !prefs_.isKey(key) || n == 0) return false;
  const size_t len = prefs_.getString(key, out, n);
  return len > 0;
}
bool NvsStore::putString(const char* key, const char* value) {
  return ok_ && prefs_.putString(key, value ? value : "") > 0;
}
bool NvsStore::getFloat(const char* key, float& out) {
  if (!ok_ || !prefs_.isKey(key)) return false;
  out = prefs_.getFloat(key, NAN);
  return true;
}
bool NvsStore::putFloat(const char* key, float value) { return ok_ && prefs_.putFloat(key, value) > 0; }
bool NvsStore::getI64(const char* key, int64_t& out) {
  if (!ok_ || !prefs_.isKey(key)) return false;
  out = prefs_.getLong64(key, 0);
  return true;
}
bool NvsStore::putI64(const char* key, int64_t value) { return ok_ && prefs_.putLong64(key, value) > 0; }
bool NvsStore::remove(const char* key) { return ok_ && prefs_.isKey(key) && prefs_.remove(key); }
bool NvsStore::getFlag(const char* key, bool dflt) {
  if (!ok_ || !prefs_.isKey(key)) return dflt;
  return prefs_.getBool(key, dflt);
}
bool NvsStore::putFlag(const char* key, bool v) { return ok_ && prefs_.putBool(key, v) > 0; }

// ------------------------------------------------------------------ esp_timer gate cut-off
void GateCutoffTimer::onFire(void* arg) {
  GateCutoffTimer* self = static_cast<GateCutoffTimer*>(arg);
  if (self->pin_ >= 0) gpio_set_level((gpio_num_t)self->pin_, 0);
  self->fired_ = self->fired_ + 1;
}
bool GateCutoffTimer::begin() {
  esp_timer_create_args_t a = {};
  a.callback = &GateCutoffTimer::onFire;
  a.arg = this;
  a.dispatch_method = ESP_TIMER_TASK;
  a.name = "motor_cutoff";
  return esp_timer_create(&a, &h_) == ESP_OK;
}
bool GateCutoffTimer::start(uint32_t ms) {
  if (!h_) return false;
  esp_timer_stop(h_);  // ignore "not running"
  return esp_timer_start_once(h_, (uint64_t)ms * 1000ULL) == ESP_OK;
}
void GateCutoffTimer::cancel() {
  if (h_) esp_timer_stop(h_);
}

// ------------------------------------------------------------------ DS18B20
#if !FERMENTAI_NOIO
void DallasBus::begin() {
  dt_.begin();
  dt_.setResolution(12);
  dt_.setWaitForConversion(false);  // non-blocking: we poll after conversionTimeMs()
  dt_.setCheckForConversion(false);
}

size_t DallasBus::discover(RomAddr* out, size_t max) {
  size_t k = 0;
  uint8_t a[8];
  ow_.reset_search();
  while (k < max && ow_.search(a)) {
    if (OneWire::crc8(a, 7) != a[7]) continue;                       // corrupt ROM
    if (a[0] != 0x28 && a[0] != 0x22 && a[0] != 0x10) continue;      // not a DS18x20 family
    memcpy(out[k].b, a, 8);
    ++k;
  }
  ow_.reset_search();
  return k;
}

bool DallasBus::requestConversion() {
  if (!ow_.reset()) return false;  // no presence pulse: bus open/shorted or no probes
  dt_.requestTemperatures();
  return true;
}

bool DallasBus::readTempC(const RomAddr& rom, float& out) {
  // getTempC() reads the scratchpad and verifies its CRC; returns DEVICE_DISCONNECTED_C on
  // no-presence or CRC failure.
  const float t = dt_.getTempC(rom.b);
  if (t == DEVICE_DISCONNECTED_C) return false;
  out = t;
  return true;
}
#endif

}  // namespace fa
#endif  // ARDUINO
