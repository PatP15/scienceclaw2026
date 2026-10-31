#include "network/CommandClient.h"

#include <stdio.h>

namespace fa {

bool CommandClient::poll(uint32_t now_ms, DesiredControlParse& out) {
  if (!http_ || !backoff_.ready(now_ms)) return false;
  char path[96];
  snprintf(path, sizeof path, "/api/v1/chambers/%s/desired-control", chamber_id_);
  std::string resp;
  ++polls_;
  const int st = http_->request("GET", path, nullptr, nullptr, 0, &resp, timeout_ms_);
  if (!httpOk(st)) {
    ++poll_failures_;
    if (httpTransient(st)) {
      backoff_.failure(now_ms);
    } else {
      if (onRejected) onRejected(st, resp);
      backoff_.failure(now_ms);  // 401/403/404 will not fix itself quickly; don't hammer
    }
    return false;
  }
  backoff_.success();
  // Receipt time is taken AFTER the body arrived: TTL counts from here.
  if (!parseDesiredControl(resp.data(), resp.size(), now_ms, out)) {
    ++poll_failures_;
    return false;
  }
  last_ok_ms_ = now_ms;
  if (out.server_time_epoch_ms >= 0 && onServerTime) onServerTime(out.server_time_epoch_ms, now_ms);
  return true;
}

size_t CommandClient::drainAcks(AckQueue& q, uint32_t now_ms, size_t max_posts) {
  if (!http_ || !ack_backoff_.ready(now_ms)) return 0;
  size_t sent = 0;
  AckRecord a;
  while (sent < max_posts && q.pop(a)) {
    char body[768];
    const size_t n = serializeAck(a, chamber_id_, device_id_, profile_, body, sizeof body);
    if (n == 0) {
      ++acks_dropped_;
      continue;
    }
    std::string resp;
    const int st = http_->request("POST", "/api/v1/control-ack", "application/json",
                                  reinterpret_cast<const uint8_t*>(body), n, &resp, timeout_ms_);
    if (httpOk(st)) {
      ++acks_sent_;
      ++sent;
      ack_backoff_.success();
      continue;
    }
    if (httpTransient(st) && ++a.attempts < max_attempts_) {
      q.pushFront(a);
      ack_backoff_.failure(now_ms);
      break;
    }
    ++acks_dropped_;
    if (!httpTransient(st) && onRejected) onRejected(st, resp);
  }
  return sent;
}

}  // namespace fa
