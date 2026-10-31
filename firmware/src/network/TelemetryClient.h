// TelemetryClient — sequence-numbered telemetry with a bounded local queue.
//
//  * Each record gets sequence_number = ++counter (starts at 1 per boot) when it is enqueued and
//    is serialized ONCE; retries resend the identical payload so the server can deduplicate on
//    (device_id, boot_id, sequence_number) (contracts/README.md "Dedup").
//  * Queue bounded (default 50 with PSRAM); when full the oldest record is dropped and counted.
//  * Oldest first. 2xx -> pop. Transient (network/5xx/408/429) -> stop and back off 1,2,4..30 s.
//    Other 4xx -> log the body once, drop that payload (never retry it in a tight loop).
//  * server_time_utc from each 2xx response is handed to onServerTime (clock sync).
#pragma once

#include <deque>
#include <functional>
#include <string>

#include "network/HttpTransport.h"
#include "telemetry_schema.h"
#include "util/TimeUtil.h"

namespace fa {

class TelemetryClient {
 public:
  TelemetryClient(IHttpTransport* http, size_t max_queue, uint32_t timeout_ms)
      : http_(http), max_(max_queue), timeout_ms_(timeout_ms) {}

  // Assigns the next sequence number, serializes and queues. Returns the sequence number, 0 on
  // serialization failure.
  uint32_t enqueue(TelemetryRecord& rec, ContractProfile p);
  // Try to send up to max_posts queued payloads. Returns the number delivered.
  size_t drain(uint32_t now_ms, size_t max_posts = 5);

  std::function<void(int64_t server_epoch_ms)> onServerTime;
  std::function<void(int status, const std::string& body)> onRejected;

  size_t queued() const { return q_.size(); }
  uint32_t sequence() const { return seq_; }
  uint32_t delivered() const { return delivered_; }
  uint32_t dropped() const { return dropped_; }
  uint32_t rejected() const { return rejected_; }
  uint32_t duplicates() const { return duplicates_; }
  const Backoff& backoff() const { return backoff_; }

 private:
  IHttpTransport* http_;
  size_t max_;
  uint32_t timeout_ms_;
  std::deque<std::string> q_;
  uint32_t seq_ = 0;
  uint32_t delivered_ = 0, dropped_ = 0, rejected_ = 0, duplicates_ = 0;
  Backoff backoff_{1000, 30000};
};

}  // namespace fa
