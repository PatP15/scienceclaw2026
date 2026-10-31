#include "network/NetworkManager.h"

#if defined(ARDUINO)
#include <HTTPClient.h>
#include <WiFi.h>

namespace fa {

void NetworkManager::begin() {
  WiFi.persistent(false);  // credentials live in our own NVS namespace, not the Wi-Fi blob
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);    // lower latency for polling; the demo is mains powered
  WiFi.setAutoReconnect(true);
  if (configured()) {
    WiFi.begin(cfg_.ssid, cfg_.pass);
    last_attempt_ms_ = millis();
  }
}

bool NetworkManager::connected() const { return WiFi.status() == WL_CONNECTED; }

int32_t NetworkManager::rssi() const { return connected() ? WiFi.RSSI() : 0; }

void NetworkManager::maintain(uint32_t now_ms) {
  if (!configured()) return;
  const bool up = connected();
  if (up) {
    if (!was_connected_) {
      log_i("wifi: connected ip=%s rssi=%d", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      backoff_.success();
    }
    was_connected_ = true;
    return;
  }
  if (was_connected_) {
    log_w("wifi: link lost");
    was_connected_ = false;
  }
  // Give a pending association 10 s before forcing a new attempt (bounded by backoff).
  if ((uint32_t)(now_ms - last_attempt_ms_) < 10000 || !backoff_.ready(now_ms)) return;
  last_attempt_ms_ = now_ms;
  ++reconnects_;
  backoff_.failure(now_ms);
  WiFi.disconnect(false, false);
  WiFi.begin(cfg_.ssid, cfg_.pass);
}

int NetworkManager::request(const char* method, const char* path_and_query, const char* content_type,
                            const uint8_t* body, size_t len, std::string* response,
                            uint32_t timeout_ms) {
  if (!connected()) return -1;
  WiFiClient client;
  HTTPClient http;
  String url = String(cfg_.base_url) + path_and_query;
  if (!http.begin(client, url)) return -2;
  http.setConnectTimeout((int32_t)timeout_ms);
  http.setTimeout((uint16_t)(timeout_ms > 65000 ? 65000 : timeout_ms));
  http.setReuse(false);
  if (cfg_.token[0]) http.addHeader("Authorization", String("Bearer ") + cfg_.token);
  if (cfg_.device_id && cfg_.device_id[0]) http.addHeader("X-Device-Id", cfg_.device_id);
  if (content_type) http.addHeader("Content-Type", content_type);
  int st;
  if (!strcmp(method, "GET"))
    st = http.GET();
  else
    st = http.sendRequest(method, const_cast<uint8_t*>(body), len);
  if (st > 0 && response) {
    // Bounded read: desired-control / ack / telemetry responses are small JSON documents.
    const int clen = http.getSize();
    if (clen < 0 || clen <= 32768) {
      String s = http.getString();
      response->assign(s.c_str(), s.length());
    }
  }
  http.end();
  return st;
}

}  // namespace fa
#endif
