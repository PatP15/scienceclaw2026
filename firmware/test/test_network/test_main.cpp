// Network logic tests with a fake HTTP transport: telemetry sequence numbers and retry of the
// SAME payload, bounded offline queue, 4xx drop, clock sync from server_time_utc; ack retry /
// drop policy; desired-control poll with backoff.
#include <deque>
#include <string>
#include <unity.h>
#include <vector>

#include "network/AckQueue.h"
#include "network/CommandClient.h"
#include "network/TelemetryClient.h"

using namespace fa;

void setUp() {}
void tearDown() {}

struct FakeHttp : IHttpTransport {
  struct Call {
    std::string method, path, ctype, body;
  };
  std::vector<Call> calls;
  std::deque<std::pair<int, std::string>> replies;  // consumed in order; default 200 "{}"
  int request(const char* method, const char* path, const char* ctype, const uint8_t* body, size_t len,
              std::string* resp, uint32_t) override {
    calls.push_back({method, path, ctype ? ctype : "", std::string((const char*)body, body ? len : 0)});
    int st = 200;
    std::string r = "{\"ok\":true,\"server_time_utc\":\"2026-10-31T12:00:01Z\"}";
    if (!replies.empty()) {
      st = replies.front().first;
      r = replies.front().second;
      replies.pop_front();
    }
    if (resp) *resp = r;
    return st;
  }
};

static TelemetryRecord rec() {
  TelemetryRecord t;
  t.device_id = "fermentai-esp32-a";
  t.boot_id = "b00t";
  t.obs.chamber_temp = Reading::ok(24.0f, 0, Provenance::Measured);
  return t;
}

static int seqOf(const std::string& body) {
  JsonDocument d;
  deserializeJson(d, body);
  return d["sequence_number"] | -1;
}

void test_telemetry_sequence_starts_at_1_and_increments() {
  FakeHttp http;
  TelemetryClient tc(&http, 50, 4000);
  TelemetryRecord r = rec();
  TEST_ASSERT_EQUAL_UINT32(1, tc.enqueue(r, ContractProfile::V1_1));
  TEST_ASSERT_EQUAL_UINT32(2, tc.enqueue(r, ContractProfile::V1_1));
  TEST_ASSERT_EQUAL(2, (int)tc.drain(0));
  TEST_ASSERT_EQUAL(1, seqOf(http.calls[0].body));
  TEST_ASSERT_EQUAL(2, seqOf(http.calls[1].body));
  TEST_ASSERT_EQUAL_STRING("/api/v1/telemetry", http.calls[0].path.c_str());
  TEST_ASSERT_EQUAL_STRING("application/json", http.calls[0].ctype.c_str());
}

void test_telemetry_retry_resends_identical_payload_with_backoff() {
  FakeHttp http;
  TelemetryClient tc(&http, 50, 4000);
  TelemetryRecord r = rec();
  tc.enqueue(r, ContractProfile::V1_1);
  http.replies.push_back({503, "{}"});
  TEST_ASSERT_EQUAL(0, (int)tc.drain(0));
  TEST_ASSERT_EQUAL(1, (int)tc.queued());
  TEST_ASSERT_EQUAL(0, (int)tc.drain(500));  // backoff 1 s: no request
  TEST_ASSERT_EQUAL(1, (int)http.calls.size());
  http.replies.push_back({-1, ""});           // network error
  TEST_ASSERT_EQUAL(0, (int)tc.drain(1000));
  TEST_ASSERT_EQUAL(2, (int)http.calls.size());
  TEST_ASSERT_EQUAL(0, (int)tc.drain(2500));  // backoff now 2 s
  TEST_ASSERT_EQUAL(1, (int)tc.drain(3000));
  TEST_ASSERT_EQUAL(3, (int)http.calls.size());
  TEST_ASSERT_EQUAL_STRING(http.calls[0].body.c_str(), http.calls[2].body.c_str());  // same bytes
  TEST_ASSERT_EQUAL(0, (int)tc.queued());
}

void test_telemetry_offline_queue_bounded_drops_oldest() {
  FakeHttp http;
  TelemetryClient tc(&http, 3, 4000);
  TelemetryRecord r = rec();
  for (int i = 0; i < 5; ++i) tc.enqueue(r, ContractProfile::V1_1);
  TEST_ASSERT_EQUAL(3, (int)tc.queued());
  TEST_ASSERT_EQUAL_UINT32(2, tc.dropped());
  tc.drain(0);
  TEST_ASSERT_EQUAL(3, seqOf(http.calls[0].body));  // oldest surviving
  TEST_ASSERT_EQUAL(5, seqOf(http.calls[2].body));
}

void test_telemetry_422_dropped_not_retried() {
  FakeHttp http;
  TelemetryClient tc(&http, 50, 4000);
  int rejected_status = 0;
  tc.onRejected = [&](int st, const std::string&) { rejected_status = st; };
  TelemetryRecord r = rec();
  tc.enqueue(r, ContractProfile::V1_1);
  tc.enqueue(r, ContractProfile::V1_1);
  http.replies.push_back({422, "{\"detail\":\"bad\"}"});
  TEST_ASSERT_EQUAL(1, (int)tc.drain(0));  // first dropped, second delivered
  TEST_ASSERT_EQUAL(422, rejected_status);
  TEST_ASSERT_EQUAL_UINT32(1, tc.rejected());
  TEST_ASSERT_EQUAL(0, (int)tc.queued());
}

void test_telemetry_server_time_syncs_clock() {
  FakeHttp http;
  TelemetryClient tc(&http, 50, 4000);
  int64_t got = -1;
  tc.onServerTime = [&](int64_t ms) { got = ms; };
  TelemetryRecord r = rec();
  tc.enqueue(r, ContractProfile::V1_1);
  http.replies.push_back({200, "{\"ok\":true,\"duplicate\":true,\"server_time_utc\":\"2026-10-31T12:00:01Z\"}"});
  tc.drain(0);
  TEST_ASSERT_EQUAL_INT64(1793448001000LL, got);
  TEST_ASSERT_EQUAL_UINT32(1, tc.duplicates());
}

void test_poll_parses_and_backs_off() {
  FakeHttp http;
  CommandClient cc(&http, "A", "fermentai-esp32-a", ContractProfile::V1_1, 4000, 6);
  int64_t synced = -1;
  cc.onServerTime = [&](int64_t ms, uint32_t) { synced = ms; };
  http.replies.push_back({200, "{\"chamber_id\":\"A\",\"command_id\":\"c1\",\"sequence_number\":1,"
                               "\"ttl_s\":90,\"temperature_setpoint_c\":24,"
                               "\"server_time_utc\":\"2026-10-31T12:00:00Z\"}"});
  DesiredControlParse p;
  TEST_ASSERT_TRUE(cc.poll(1234, p));
  TEST_ASSERT_EQUAL_STRING("/api/v1/chambers/A/desired-control", http.calls[0].path.c_str());
  TEST_ASSERT_EQUAL_STRING("GET", http.calls[0].method.c_str());
  TEST_ASSERT_EQUAL_UINT32(1234, p.received_ms);
  TEST_ASSERT_EQUAL_UINT32(1, p.n);
  TEST_ASSERT_EQUAL_INT64(1793448000000LL, synced);
  http.replies.push_back({500, "{}"});
  TEST_ASSERT_FALSE(cc.poll(2000, p));
  TEST_ASSERT_FALSE(cc.poll(2500, p));  // in backoff: no request sent
  TEST_ASSERT_EQUAL(2, (int)http.calls.size());
}

void test_acks_retry_then_drop() {
  FakeHttp http;
  CommandClient cc(&http, "A", "fermentai-esp32-a", ContractProfile::V1_1, 4000, 3);
  AckQueue q(8);
  AckRecord a;
  strcpy(a.command_id, "c1");
  q.pushAck(a);
  strcpy(a.command_id, "c2");
  q.pushAck(a);
  http.replies.push_back({-1, ""});
  TEST_ASSERT_EQUAL(0, (int)cc.drainAcks(q, 0));
  TEST_ASSERT_EQUAL(2, (int)q.size());  // c1 requeued at the front
  TEST_ASSERT_EQUAL(2, (int)cc.drainAcks(q, 1000));
  TEST_ASSERT_EQUAL(0, (int)q.size());
  JsonDocument d;
  deserializeJson(d, http.calls[1].body);
  TEST_ASSERT_EQUAL_STRING("c1", d["command_id"]);
  TEST_ASSERT_EQUAL_STRING("fermentai-esp32-a", d["device_id"]);
  // 404: dropped immediately, never retried
  strcpy(a.command_id, "c3");
  q.pushAck(a);
  http.replies.push_back({404, "{\"detail\":\"unknown command\"}"});
  cc.drainAcks(q, 2000);
  TEST_ASSERT_EQUAL(0, (int)q.size());
  TEST_ASSERT_EQUAL_UINT32(1, cc.acksDropped());
}

void test_ack_queue_bounded() {
  AckQueue q(4);
  AckRecord a;
  for (int i = 0; i < 6; ++i) {
    snprintf(a.command_id, sizeof a.command_id, "c%d", i);
    q.pushAck(a);
  }
  TEST_ASSERT_EQUAL(4, (int)q.size());
  TEST_ASSERT_EQUAL_UINT32(2, q.dropped());
  AckRecord out;
  q.pop(out);
  TEST_ASSERT_EQUAL_STRING("c2", out.command_id);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_telemetry_sequence_starts_at_1_and_increments);
  RUN_TEST(test_telemetry_retry_resends_identical_payload_with_backoff);
  RUN_TEST(test_telemetry_offline_queue_bounded_drops_oldest);
  RUN_TEST(test_telemetry_422_dropped_not_retried);
  RUN_TEST(test_telemetry_server_time_syncs_clock);
  RUN_TEST(test_poll_parses_and_backs_off);
  RUN_TEST(test_acks_retry_then_drop);
  RUN_TEST(test_ack_queue_bounded);
  return UNITY_END();
}
