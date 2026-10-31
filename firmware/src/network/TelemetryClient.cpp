#include "network/TelemetryClient.h"

#include <ArduinoJson.h>

namespace fa {

uint32_t TelemetryClient::enqueue(TelemetryRecord& rec, ContractProfile p) {
  rec.sequence_number = seq_ + 1;
  JsonDocument doc;
  if (!buildTelemetryJson(rec, doc, p)) return 0;
  std::string s;
  serializeJson(doc, s);
  if (s.empty() || s.size() > 60000) return 0;  // server limit 64 KB
  ++seq_;
  if (q_.size() >= max_) {
    q_.pop_front();
    ++dropped_;
  }
  q_.push_back(std::move(s));
  return seq_;
}

size_t TelemetryClient::drain(uint32_t now_ms, size_t max_posts) {
  size_t sent = 0;
  if (!http_ || !backoff_.ready(now_ms)) return 0;
  while (!q_.empty() && sent < max_posts) {
    std::string resp;
    const std::string& body = q_.front();
    const int st = http_->request("POST", "/api/v1/telemetry", "application/json",
                                  reinterpret_cast<const uint8_t*>(body.data()), body.size(), &resp,
                                  timeout_ms_);
    if (httpOk(st)) {
      q_.pop_front();
      ++delivered_;
      ++sent;
      backoff_.success();
      JsonDocument d;
      if (deserializeJson(d, resp) == DeserializationError::Ok) {
        if (d["duplicate"] | false) ++duplicates_;
        const char* ts = d["server_time_utc"] | (const char*)nullptr;
        int64_t ms;
        if (ts && onServerTime && parseIso8601Utc(ts, ms)) onServerTime(ms);
      }
      continue;
    }
    if (httpTransient(st)) {
      backoff_.failure(now_ms);
      break;
    }
    // Permanent rejection (401/403/404/413/422...): report once, drop this payload.
    ++rejected_;
    if (onRejected) onRejected(st, resp);
    q_.pop_front();
  }
  return sent;
}

}  // namespace fa
