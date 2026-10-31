// ArduinoHal — real ESP32 implementations of the HAL interfaces (Arduino-ESP32 2.0.x).
// Not compiled for the native test target, and not used by the esp32s3cam_noio build.
#pragma once

#include "hal_interfaces.h"

#if defined(ARDUINO)
#include <Arduino.h>
#include <Preferences.h>
#include <esp_timer.h>

#if !FERMENTAI_NOIO
#include <DallasTemperature.h>
#include <OneWire.h>
#endif

namespace fa {

// Drive every power-control pin LOW as the very first thing at boot (before any other init).
// Levels of the power-control pins sampled as inputs at the very start of setup(), before they
// are driven LOW (bring-up evidence for the SD-card pull-up question, docs/GPIO_MAP.md §3).
struct BootPinSnapshot {
  static constexpr int kN = 6;
  int gpio[kN] = {-1, -1, -1, -1, -1, -1};
  uint8_t level[kN] = {0, 0, 0, 0, 0, 0};     // plain input
  uint8_t level_pd[kN] = {0, 0, 0, 0, 0, 0};  // with internal pull-down: 1 = strong board pull-up
};
extern BootPinSnapshot g_bootPins;
void holdOutputsSafe();

class GpioOut : public IDigitalOut {
 public:
  explicit GpioOut(int pin) : pin_(pin) {}
  void begin();
  void write(bool high) override;
  bool state() const override { return state_; }
  int pin() const { return pin_; }

 private:
  int pin_;
  bool state_ = false;
};

class GpioIn : public IDigitalIn {
 public:
  GpioIn(int pin, bool pullup) : pin_(pin), pullup_(pullup) {}
  void begin();
  bool read() override;

 private:
  int pin_;
  bool pullup_;
};

// LEDC PWM. Channels 2/3 (LEDC timer 1) are used for the Peltier so they never share a timer
// with the camera XCLK (LEDC channel 0 / timer 0).
class LedcPwmOut : public IPwmOut {
 public:
  LedcPwmOut(int pin, uint8_t channel, uint32_t freq_hz, uint8_t bits)
      : pin_(pin), ch_(channel), freq_(freq_hz), bits_(bits) {}
  void begin();
  void setFraction(float f) override;
  float fraction() const override { return f_; }

 private:
  int pin_;
  uint8_t ch_;
  uint32_t freq_;
  uint8_t bits_;
  float f_ = 0.0f;
  bool attached_ = false;
};

class AdcIn : public IAnalogIn {
 public:
  explicit AdcIn(int pin) : pin_(pin) {}
  void begin();
  bool sample(int32_t& raw, float& mv) override;

 private:
  int pin_;
};

// NVS via Preferences, namespace "fermentai".
class NvsStore : public IKeyValueStore {
 public:
  bool begin();
  bool getString(const char* key, char* out, size_t n) override;
  bool putString(const char* key, const char* value) override;
  bool getFloat(const char* key, float& out) override;
  bool putFloat(const char* key, float value) override;
  bool getI64(const char* key, int64_t& out) override;
  bool putI64(const char* key, int64_t value) override;
  bool remove(const char* key) override;
  bool getFlag(const char* key, bool dflt = false);
  bool putFlag(const char* key, bool v);

 private:
  Preferences prefs_;
  bool ok_ = false;
};

// esp_timer one-shot that forces a GPIO LOW from the esp_timer task. Independent of loop().
class GateCutoffTimer : public IOneShotTimer {
 public:
  explicit GateCutoffTimer(int pin) : pin_(pin) {}
  bool begin();
  bool start(uint32_t ms) override;
  void cancel() override;
  uint32_t fired() const { return fired_; }

 private:
  static void onFire(void* arg);
  int pin_;
  esp_timer_handle_t h_ = nullptr;
  volatile uint32_t fired_ = 0;
};

#if !FERMENTAI_NOIO
class DallasBus : public IOneWireTempBus {
 public:
  explicit DallasBus(int pin) : ow_(pin), dt_(&ow_) {}
  void begin();
  size_t discover(RomAddr* out, size_t max) override;
  bool requestConversion() override;
  uint32_t conversionTimeMs() const override { return 760; }  // 12-bit
  bool readTempC(const RomAddr& rom, float& out) override;

 private:
  OneWire ow_;
  DallasTemperature dt_;
};
#endif

}  // namespace fa
#endif  // ARDUINO
