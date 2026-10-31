// CommandClient — polls GET /api/v1/chambers/{id}/desired-control and posts acknowledgments.
//
// poll(): one GET; on 2xx the body is parsed with receipt time = now_ms (expiry is measured from
// receipt) and server_time_utc is handed to onServerTime. Parsing happens here (network task);
// EXECUTION happens in the control loop (CommandProcessor), so a slow network can only make a
// command expire, never run late.
// drainAcks(): oldest first; transient failure -> requeue at the front (attempts <= max), stop;
// 404/409/422 -> drop (server does not know / not ours / invalid).
#pragma once

#include <functional>
#include <string>

#include "command_schema.h"
#include "network/AckQueue.h"
#include "network/HttpTransport.h"
#include "util/TimeUtil.h"

namespace fa {

class CommandClient {
 public:
  CommandClient(IHttpTransport* http, const char* chamber_id, const char* device_id,
                ContractProfile profile, uint32_t timeout_ms, uint8_t max_ack_attempts)
      : http_(http), chamber_id_(chamber_id), device_id_(device_id), profile_(profile),
        timeout_ms_(timeout_ms), max_attempts_(max_ack_attempts) {}

  // Returns true if a response was received and parsed into `out`.
  bool poll(uint32_t now_ms, DesiredControlParse& out);
  size_t drainAcks(AckQueue& q, uint32_t now_ms, size_t max_posts = 8);

  std::function<void(int64_t server_epoch_ms, uint32_t received_ms)> onServerTime;
  std::function<void(int status, const std::string& body)> onRejected;

  uint32_t polls() const { return polls_; }
  uint32_t pollFailures() const { return poll_failures_; }
  uint32_t acksSent() const { return acks_sent_; }
  uint32_t acksDropped() const { return acks_dropped_; }
  uint32_t lastPollOkMs() const { return last_ok_ms_; }
  const Backoff& backoff() const { return backoff_; }

 private:
  IHttpTransport* http_;
  const char* chamber_id_;
  const char* device_id_;
  ContractProfile profile_;
  uint32_t timeout_ms_;
  uint8_t max_attempts_;
  uint32_t polls_ = 0, poll_failures_ = 0, acks_sent_ = 0, acks_dropped_ = 0, last_ok_ms_ = 0;
  Backoff backoff_{1000, 30000};
  Backoff ack_backoff_{1000, 30000};
};

}  // namespace fa
