// HttpTransport — minimal HTTP abstraction so the retry/queue logic in TelemetryClient and
// CommandClient is portable and unit-tested natively with a fake transport.
// The ESP32 implementation (HTTPClient over Wi-Fi) is NetworkManager.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <string>

namespace fa {

class IHttpTransport {
 public:
  virtual ~IHttpTransport() = default;
  // Returns the HTTP status, or <= 0 on a transport error (no Wi-Fi, connect/timeout failure).
  // Every request carries Authorization: Bearer <token> and X-Device-Id (added by the transport).
  virtual int request(const char* method, const char* path_and_query, const char* content_type,
                      const uint8_t* body, size_t len, std::string* response, uint32_t timeout_ms) = 0;
};

inline bool httpOk(int s) { return s >= 200 && s < 300; }
// Worth retrying the SAME payload later (contract §3: retry 5xx / network errors with backoff;
// don't loop on 4xx).
inline bool httpTransient(int s) { return s <= 0 || s == 408 || s == 429 || s >= 500; }

}  // namespace fa
