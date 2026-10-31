// hal_interfaces.h — hardware abstraction. Real drivers live in src/hal and src/sensors
// (Arduino only); mock drivers live in src/simulation (all targets, used by native tests and
// the esp32s3cam_noio build). Application logic depends only on these interfaces.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace fa {

class IDigitalOut {
 public:
  virtual ~IDigitalOut() = default;
  virtual void write(bool high) = 0;
  virtual bool state() const = 0;
};

class IDigitalIn {
 public:
  virtual ~IDigitalIn() = default;
  virtual bool read() = 0;  // raw electrical level (true = HIGH)
};

class IPwmOut {
 public:
  virtual ~IPwmOut() = default;
  virtual void setFraction(float f) = 0;  // 0..1
  virtual float fraction() const = 0;
};

class IAnalogIn {
 public:
  virtual ~IAnalogIn() = default;
  // One sample: raw counts and calibrated millivolts (esp_adc_cal on the ESP32). false on error.
  virtual bool sample(int32_t& raw, float& mv) = 0;
};

// Persistent key/value store (NVS Preferences on device, in-memory map in tests).
class IKeyValueStore {
 public:
  virtual ~IKeyValueStore() = default;
  virtual bool getString(const char* key, char* out, size_t n) = 0;
  virtual bool putString(const char* key, const char* value) = 0;
  virtual bool getFloat(const char* key, float& out) = 0;
  virtual bool putFloat(const char* key, float value) = 0;
  virtual bool getI64(const char* key, int64_t& out) = 0;
  virtual bool putI64(const char* key, int64_t value) = 0;
  virtual bool remove(const char* key) = 0;
};

// One-shot timer that runs `fn(arg)` from timer context (esp_timer on device). Used as an
// independent motor cutoff that does not depend on the main loop running.
class IOneShotTimer {
 public:
  virtual ~IOneShotTimer() = default;
  virtual bool start(uint32_t ms) = 0;
  virtual void cancel() = 0;
};

struct RomAddr {
  uint8_t b[8];
  bool operator==(const RomAddr& o) const {
    for (int i = 0; i < 8; ++i)
      if (b[i] != o.b[i]) return false;
    return true;
  }
  bool isZero() const {
    for (int i = 0; i < 8; ++i)
      if (b[i]) return false;
    return true;
  }
};

// Shared OneWire bus with DS18B20 probes (DallasTemperature on device).
class IOneWireTempBus {
 public:
  virtual ~IOneWireTempBus() = default;
  virtual size_t discover(RomAddr* out, size_t max) = 0;  // ROMs with valid CRC, family 0x28
  virtual bool requestConversion() = 0;                   // async, all probes
  virtual uint32_t conversionTimeMs() const = 0;
  // Read the last conversion. false on CRC failure / no presence / disconnected (-127).
  virtual bool readTempC(const RomAddr& rom, float& out) = 0;
};

struct EnvRaw {
  float temperature_c = 0;
  float humidity_pct = 0;
  float pressure_pa = 0;
  float gas_ohm = 0;
  bool has_pressure = false;
  bool has_gas = false;
};

// BME680 / SHT31 abstraction. Measurement is split so the BME680 gas heater cycle never blocks.
class IEnvSensor {
 public:
  virtual ~IEnvSensor() = default;
  virtual bool begin() = 0;                 // detect + configure; false if absent
  virtual bool present() const = 0;
  virtual const char* name() const = 0;     // "bme680" | "sht31" | "mock_env"
  virtual uint32_t startMeasurement() = 0;  // returns ms until data ready, 0 on failure
  virtual bool fetch(EnvRaw& out) = 0;      // call after the returned delay
};

}  // namespace fa
