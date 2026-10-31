// NetworkManager — Wi-Fi station management (non-blocking reconnect with backoff) and the
// HTTPClient implementation of IHttpTransport for the Mac backend on the isolated demo LAN.
//
// Credentials/URL come from NVS (set via the serial console) and fall back to build-time values
// in include/secrets.h (gitignored). Every request sends:
//   Authorization: Bearer <device token or shared FERMENTAI_API_TOKEN>
//   X-Device-Id: <DEVICE_ID>
// No cloud credentials of any kind are stored on the device.
#pragma once

#include "network/HttpTransport.h"

#if defined(ARDUINO)
#include <Arduino.h>

#include "util/TimeUtil.h"

namespace fa {

struct NetConfig {
  char ssid[33] = {0};
  char pass[65] = {0};
  char base_url[96] = {0};  // e.g. http://192.168.8.10:8000 (no trailing slash)
  char token[97] = {0};
  const char* device_id = "";
};

class NetworkManager : public IHttpTransport {
 public:
  explicit NetworkManager(const NetConfig& cfg) : cfg_(cfg) {}
  void begin();
  void maintain(uint32_t now_ms);  // call from the network task loop
  bool connected() const;
  int32_t rssi() const;
  bool configured() const { return cfg_.ssid[0] != 0 && cfg_.base_url[0] != 0; }
  const NetConfig& config() const { return cfg_; }

  int request(const char* method, const char* path_and_query, const char* content_type,
              const uint8_t* body, size_t len, std::string* response, uint32_t timeout_ms) override;

  uint32_t reconnects() const { return reconnects_; }

 private:
  NetConfig cfg_;
  Backoff backoff_{2000, 30000};
  bool was_connected_ = false;
  uint32_t reconnects_ = 0;
  uint32_t last_attempt_ms_ = 0;
};

}  // namespace fa
#endif
