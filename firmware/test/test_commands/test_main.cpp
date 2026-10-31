// Command tests: contract fixture parsing, TTL-from-receipt expiry, server-relative TTL, replay
// (ring buffer + sequence + epoch reset), reboot motor replay guard, local setpoint clamp
// (4-35 C from device_config.h), wrong chamber, ack serialization (v1.0 exact keys, v1.1 superset).
#include <stdio.h>
#include <string>
#include <unity.h>
#include <vector>

#include "app/CommandProcessor.h"
#include "command_schema.h"
#include "device_config.h"
#include "simulation/MockActuators.h"

using namespace fa;

// ArduinoJson 7 deprecates containsKey(); iterate instead (works for null-valued keys too).
[[maybe_unused]] static bool hasKey(JsonObjectConst o, const char* k) {
  for (JsonPairConst kv : o)
    if (!strcmp(kv.key().c_str(), k)) return true;
  return false;
}
[[maybe_unused]] static bool hasKey(JsonObjectConst o, const std::string& k) { return hasKey(o, k.c_str()); }
[[maybe_unused]] static bool hasKey(JsonObjectConst o, JsonString k) { return hasKey(o, k.c_str()); }

void setUp() {}
void tearDown() {}

#ifndef FERMENTAI_CONTRACT_DIR
#define FERMENTAI_CONTRACT_DIR "../firmware_contract"
#endif

static std::string readFile(const char* name) {
  std::string path = std::string(FERMENTAI_CONTRACT_DIR) + "/" + name;
  FILE* f = fopen(path.c_str(), "rb");
  if (!f) return std::string();
  std::string s;
  char buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
  fclose(f);
  return s;
}

struct Target : ICommandTarget {
  float setpoint = NAN;
  ControlMode mode = ControlMode::Auto;
  int setpoint_calls = 0;
  int motor_starts = 0;
  int motor_stops = 0;
  bool motor_running = false;
  bool refuse_motor = false;
  int images = 0;
  void applySetpoint(float c, ControlMode m, const char*, uint32_t) override {
    setpoint = c;
    mode = m;
    ++setpoint_calls;
  }
  bool startMotor(uint32_t d, const char*, const char*, int64_t, uint32_t, uint32_t* g, char* why,
                  size_t n) override {
    if (refuse_motor) {
      snprintf(why, n, "door open");
      return false;
    }
    ++motor_starts;
    motor_running = true;
    if (g) *g = d > 10000 ? 10000 : d;
    return true;
  }
  bool stopMotor(uint32_t, char* id, size_t n) override {
    ++motor_stops;
    const bool was = motor_running;
    motor_running = false;
    if (was && id) snprintf(id, n, "running-id");
    return was;
  }
  bool requestImage(const char*, int64_t, char*, size_t) override {
    ++images;
    return true;
  }
};

struct Sink : IAckSink {
  std::vector<AckRecord> acks;
  bool pushAck(const AckRecord& a) override {
    acks.push_back(a);
    return true;
  }
};

struct Rig {
  MockKeyValueStore kv;
  Target tgt;
  Sink sink;
  ClockSync clk;
  CommandProcessor cp{"A", &kv, &tgt, &sink};
  Rig() { cp.begin(); }
  void feed(const std::string& json, uint32_t recv_ms, uint32_t now_ms) {
    DesiredControlParse p;
    TEST_ASSERT_TRUE_MESSAGE(parseDesiredControl(json.c_str(), json.size(), recv_ms, p), p.error);
    cp.handle(p, now_ms, clk);
  }
};

static std::string flat(const char* id, int seq, float sp, int ttl, const char* epoch = "e1") {
  char b[512];
  snprintf(b, sizeof b,
           "{\"schema_version\":\"1.0\",\"command_type\":\"control\",\"command_id\":\"%s\","
           "\"sequence_number\":%d,\"server_epoch\":\"%s\",\"ttl_s\":%d,\"chamber_id\":\"A\","
           "\"experiment_id\":\"trial_001\",\"temperature_setpoint_c\":%.2f,"
           "\"motor\":{\"command_id\":null,\"action\":\"none\",\"run\":false,\"duration_s\":0.0}}",
           id, seq, epoch, ttl, sp);
  return b;
}

static std::string arr_seq(const char* id, int seq) {
  char b[512];
  snprintf(b, sizeof b,
           "{\"chamber_id\":\"A\",\"server_epoch\":\"e1\",\"server_time_utc\":\"2026-10-31T12:00:00Z\","
           "\"commands\":[{\"command_id\":\"%s\",\"chamber_id\":\"A\",\"action\":\"set_setpoint\","
           "\"parameters\":{\"setpoint_c\":20},\"issued_at_utc\":\"2026-10-31T12:00:00Z\","
           "\"expires_at_utc\":\"2026-10-31T12:01:30Z\",\"sequence_number\":%d}]}",
           id, seq);
  return b;
}

// ------------------------------------------------------------------ fixtures
void test_parse_contract_fixture_command_example() {
  std::string j = readFile("command_example.json");
  TEST_ASSERT_TRUE_MESSAGE(!j.empty(), "firmware_contract/command_example.json not found");
  DesiredControlParse p;
  TEST_ASSERT_TRUE(parseDesiredControl(j.c_str(), j.size(), 1000, p));
  // v1.1 fixture: commands[] (set_setpoint, animate_motor) + the same two as v1.0 flat fields
  TEST_ASSERT_EQUAL_UINT32(4, p.n);
  TEST_ASSERT_TRUE(p.cmds[0].action == CmdAction::SetSetpoint && !p.cmds[0].flat);
  TEST_ASSERT_EQUAL_INT64(90000, p.cmds[0].ttl_ms);  // expires_at - server_time
  TEST_ASSERT_TRUE(p.cmds[1].action == CmdAction::AnimateMotor && !p.cmds[1].flat);
  TEST_ASSERT_EQUAL_UINT32(3000, p.cmds[1].duration_ms);
  TEST_ASSERT_EQUAL_INT64(60000, p.cmds[1].ttl_ms);
  const DeviceCommand& c = p.cmds[2];
  TEST_ASSERT_TRUE(c.flat);
  TEST_ASSERT_TRUE(c.action == CmdAction::SetSetpoint);
  TEST_ASSERT_EQUAL_STRING("6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10", c.command_id);
  TEST_ASSERT_EQUAL_INT64(42, c.sequence_number);
  TEST_ASSERT_TRUE(c.has_ttl);
  TEST_ASSERT_EQUAL_INT64(90000, c.ttl_ms);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.0f, c.setpoint_c);
  TEST_ASSERT_EQUAL_STRING("experiment_001", c.experiment_id);
  const DeviceCommand& m = p.cmds[3];
  TEST_ASSERT_TRUE(m.action == CmdAction::AnimateMotor && m.flat);
  TEST_ASSERT_EQUAL_UINT32(3000, m.duration_ms);
  TEST_ASSERT_EQUAL_STRING(p.cmds[1].command_id, m.command_id);  // same command, both shapes
  TEST_ASSERT_EQUAL_INT64(60000, m.ttl_ms);
  TEST_ASSERT_EQUAL_STRING("wiggle", m.pattern);
  TEST_ASSERT_EQUAL_STRING("3f9a1c2b7d4e", p.server_epoch);
  TEST_ASSERT_TRUE(p.server_time_epoch_ms > 0);
}

void test_parse_contract_fixture_no_motor() {
  std::string j = readFile("command_no_motor_example.json");
  TEST_ASSERT_TRUE(!j.empty());
  DesiredControlParse p;
  TEST_ASSERT_TRUE(parseDesiredControl(j.c_str(), j.size(), 0, p));
  // set_setpoint in commands[] + flat control; motor action "none" is not a command
  TEST_ASSERT_EQUAL_UINT32(2, p.n);
  TEST_ASSERT_EQUAL_STRING(p.cmds[0].command_id, p.cmds[1].command_id);
}

void test_fixture_flat_and_array_execute_once() {
  std::string j = readFile("command_example.json");
  Rig r;
  r.feed(j, 0, 100);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  TEST_ASSERT_EQUAL(1, r.tgt.motor_starts);
  TEST_ASSERT_EQUAL(2, (int)r.sink.acks.size());  // one ack per command, not per shape
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.0f, r.sink.acks[0].applied_setpoint_c);
  r.feed(j, 5000, 5000);  // next poll serves the same commands again
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  TEST_ASSERT_EQUAL(1, r.tgt.motor_starts);
  TEST_ASSERT_EQUAL(2, (int)r.sink.acks.size());
}

void test_v11_array_sequence_le_highest_ignored() {
  Rig r;
  r.feed(arr_seq("q1", 50), 0, 0);
  r.feed(arr_seq("q2", 50), 0, 0);  // different id, same number -> replay
  r.feed(arr_seq("q3", 49), 0, 0);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  r.feed(arr_seq("q4", 51), 0, 0);
  TEST_ASSERT_EQUAL(2, r.tgt.setpoint_calls);
}

void test_parse_garbage_rejected() {
  DesiredControlParse p;
  TEST_ASSERT_FALSE(parseDesiredControl("{not json", 9, 0, p));
  TEST_ASSERT_FALSE(p.ok);
  TEST_ASSERT_FALSE(parseDesiredControl("[1,2]", 5, 0, p));
}

// ------------------------------------------------------------------ apply / ack / replay
void test_new_command_applied_and_acked_once() {
  Rig r;
  r.feed(flat("c1", 42, 24.0f, 90), 1000, 1000);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.0f, r.tgt.setpoint);
  TEST_ASSERT_EQUAL(1, (int)r.sink.acks.size());
  TEST_ASSERT_TRUE(r.sink.acks[0].status == AckStatus::Accepted);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.0f, r.sink.acks[0].applied_setpoint_c);
  TEST_ASSERT_EQUAL_INT64(42, r.sink.acks[0].sequence_number);
  TEST_ASSERT_EQUAL_INT64(-1, r.sink.acks[0].epoch_ms);  // unsynced -> timestamp null
  // Re-delivery of the same id: no-op, no re-ack.
  r.feed(flat("c1", 42, 24.0f, 85), 6000, 6000);
  r.feed(flat("c1", 42, 24.0f, 80), 11000, 11000);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  TEST_ASSERT_EQUAL(1, (int)r.sink.acks.size());
  TEST_ASSERT_EQUAL_UINT32(2, r.cp.stats().replays);
  // ...but it refreshes the "valid control seen" timer (link + command alive).
  TEST_ASSERT_EQUAL_UINT32(11000, r.cp.lastValidControlMs());
}

void test_setpoint_clamped_to_local_limits() {
  TEST_ASSERT_EQUAL_FLOAT(4.0f, limits::kSetpointMinC);
  TEST_ASSERT_EQUAL_FLOAT(35.0f, limits::kSetpointMaxC);
  Rig r;
  r.feed(flat("hi", 1, 40.0f, 90), 0, 0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 35.0f, r.tgt.setpoint);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Accepted);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 35.0f, r.sink.acks.back().applied_setpoint_c);
  TEST_ASSERT_NOT_NULL(strstr(r.sink.acks.back().message, "clamped locally"));
  r.feed(flat("lo", 2, -10.0f, 90), 0, 0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 4.0f, r.tgt.setpoint);
  r.feed(flat("ok", 3, 22.5f, 90), 0, 0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 22.5f, r.tgt.setpoint);
  TEST_ASSERT_EQUAL_STRING("", r.sink.acks.back().message);
  // Huge / non-finite values never get through
  r.feed("{\"chamber_id\":\"A\",\"command_id\":\"big\",\"sequence_number\":4,\"ttl_s\":90,"
         "\"temperature_setpoint_c\":1e30}", 0, 0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 35.0f, r.tgt.setpoint);
  r.feed("{\"chamber_id\":\"A\",\"command_id\":\"nul\",\"sequence_number\":5,\"ttl_s\":90,"
         "\"temperature_setpoint_c\":null}", 0, 0);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Rejected);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 35.0f, r.tgt.setpoint);
}

void test_expiry_relative_to_receipt() {
  Rig r;
  // ttl_s = 0 -> stale on arrival
  r.feed(flat("e0", 1, 20.0f, 0), 1000, 1000);
  TEST_ASSERT_EQUAL(0, r.tgt.setpoint_calls);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Expired);
  // received at 1000 with ttl 5 s, processed at 7000 (queued too long) -> expired
  r.feed(flat("e1", 2, 20.0f, 5), 1000, 7000);
  TEST_ASSERT_EQUAL(0, r.tgt.setpoint_calls);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Expired);
  // same TTL processed in time -> applied
  r.feed(flat("e2", 3, 20.0f, 5), 1000, 5999);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  // device uptime wrap-around is handled (uint32 arithmetic)
  r.feed(flat("e3", 4, 21.0f, 5), 0xFFFFF000u, 0x00000100u);
  TEST_ASSERT_EQUAL(2, r.tgt.setpoint_calls);
}

void test_expired_ack_sent_once() {
  Rig r;
  r.feed(flat("x", 1, 20.0f, 0), 0, 0);
  r.feed(flat("x", 1, 20.0f, 0), 0, 5000);
  TEST_ASSERT_EQUAL(1, (int)r.sink.acks.size());
}

void test_lower_sequence_ignored() {
  Rig r;
  r.feed(flat("s42", 42, 24.0f, 90), 0, 0);
  r.feed(flat("s41", 41, 30.0f, 90), 0, 0);  // older -> replay / stale response
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.0f, r.tgt.setpoint);
  TEST_ASSERT_EQUAL(1, (int)r.sink.acks.size());
  TEST_ASSERT_EQUAL_UINT32(1, r.cp.stats().stale_sequence);
  r.feed(flat("s43", 43, 26.0f, 90), 0, 0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 26.0f, r.tgt.setpoint);
}

void test_epoch_change_resets_sequence() {
  Rig r;
  r.feed(flat("a", 42, 24.0f, 90, "e1"), 0, 0);
  r.feed(flat("b", 1, 25.0f, 90, "e2"), 0, 0);  // server DB wiped
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 25.0f, r.tgt.setpoint);
}

void test_wrong_chamber_rejected() {
  Rig r;
  r.feed("{\"chamber_id\":\"B\",\"command_id\":\"w\",\"sequence_number\":1,\"ttl_s\":90,"
         "\"temperature_setpoint_c\":20}", 0, 0);
  TEST_ASSERT_EQUAL(0, r.tgt.setpoint_calls);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Rejected);
}

void test_ring_buffer_bounded_and_replay() {
  Rig r;
  for (int i = 0; i < 40; ++i) {
    char id[16];
    snprintf(id, sizeof id, "id%d", i);
    r.feed(flat(id, 100 + i, 20.0f, 90), 0, 0);
  }
  TEST_ASSERT_EQUAL(40, r.tgt.setpoint_calls);
  r.feed(flat("id39", 139, 20.0f, 90), 0, 0);  // recent -> remembered
  TEST_ASSERT_EQUAL(40, r.tgt.setpoint_calls);
  r.feed(flat("id0", 100, 20.0f, 90), 0, 0);  // evicted, but sequence guard still blocks it
  TEST_ASSERT_EQUAL(40, r.tgt.setpoint_calls);
}

// ------------------------------------------------------------------ motor replay across reboot
void test_motor_not_replayed_after_reboot() {
  MockKeyValueStore kv;  // survives the "reboot"
  const char* j =
      "{\"chamber_id\":\"A\",\"server_epoch\":\"e1\",\"motor\":{\"command_id\":\"mot-1\","
      "\"action\":\"run\",\"run\":true,\"duration_s\":5.0,\"sequence_number\":7,\"ttl_s\":60}}";
  {
    Target tgt;
    Sink sink;
    ClockSync clk;
    CommandProcessor cp("A", &kv, &tgt, &sink);
    cp.begin();
    DesiredControlParse p;
    parseDesiredControl(j, strlen(j), 0, p);
    cp.handle(p, 0, clk);
    TEST_ASSERT_EQUAL(1, tgt.motor_starts);
  }
  Target tgt;
  Sink sink;
  ClockSync clk;
  CommandProcessor cp("A", &kv, &tgt, &sink);
  cp.begin();
  DesiredControlParse p;
  parseDesiredControl(j, strlen(j), 100, p);  // server still serving it (ttl > 0)
  cp.handle(p, 100, clk);
  TEST_ASSERT_EQUAL(0, tgt.motor_starts);
  TEST_ASSERT_EQUAL(0, (int)sink.acks.size());
  // An older motor sequence is also refused after reboot
  const char* j2 =
      "{\"chamber_id\":\"A\",\"server_epoch\":\"e1\",\"motor\":{\"command_id\":\"mot-0\","
      "\"action\":\"run\",\"run\":true,\"duration_s\":5.0,\"sequence_number\":6,\"ttl_s\":60}}";
  parseDesiredControl(j2, strlen(j2), 200, p);
  cp.handle(p, 200, clk);
  TEST_ASSERT_EQUAL(0, tgt.motor_starts);
}

void test_motor_rejected_reason_propagates() {
  Rig r;
  r.tgt.refuse_motor = true;
  r.feed("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m\",\"action\":\"run\",\"run\":true,"
         "\"duration_s\":3,\"sequence_number\":1,\"ttl_s\":60}}", 0, 0);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Rejected);
  TEST_ASSERT_EQUAL_STRING("door open", r.sink.acks.back().message);
}

// ------------------------------------------------------------------ v1.1 commands array
static std::string arr(const char* body, const char* server_time = "2026-10-31T12:00:00Z") {
  return std::string("{\"chamber_id\":\"A\",\"server_epoch\":\"e1\",\"server_time_utc\":\"") +
         server_time + "\",\"commands\":[" + body + "]}";
}

void test_v11_array_all_actions() {
  Rig r;
  r.feed(arr("{\"command_id\":\"k1\",\"chamber_id\":\"A\",\"experiment_id\":\"exp9\","
             "\"action\":\"set_setpoint\",\"parameters\":{\"setpoint_c\":26.5,\"control_mode\":\"auto\"},"
             "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:30Z\","
             "\"sequence_number\":10},"
             "{\"command_id\":\"k2\",\"chamber_id\":\"A\",\"action\":\"animate_motor\","
             "\"parameters\":{\"duration_ms\":3000,\"pattern\":\"nod\"},"
             "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
             "\"sequence_number\":11},"
             "{\"command_id\":\"k3\",\"chamber_id\":\"A\",\"action\":\"capture_image\",\"parameters\":{},"
             "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
             "\"sequence_number\":12},"
             "{\"command_id\":\"k4\",\"chamber_id\":\"A\",\"action\":\"noop\",\"parameters\":{},"
             "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
             "\"sequence_number\":13},"
             "{\"command_id\":\"k5\",\"chamber_id\":\"A\",\"action\":\"stop_motor\","
             "\"parameters\":{\"target_command_id\":\"k2\"},"
             "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
             "\"sequence_number\":14}"),
         5000, 5000);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 26.5f, r.tgt.setpoint);
  TEST_ASSERT_EQUAL_STRING("exp9", r.cp.experimentId());
  TEST_ASSERT_EQUAL(1, r.tgt.motor_starts);
  TEST_ASSERT_EQUAL(1, r.tgt.images);
  TEST_ASSERT_EQUAL(1, r.tgt.motor_stops);
  // acks: k1 accepted, k2 accepted, k3 accepted, k4 completed, k5 completed, + cancel for running id
  TEST_ASSERT_EQUAL(6, (int)r.sink.acks.size());
  TEST_ASSERT_TRUE(r.sink.acks[3].status == AckStatus::Completed);
  TEST_ASSERT_TRUE(r.sink.acks[4].status == AckStatus::Completed);
  TEST_ASSERT_TRUE(r.sink.acks[5].status == AckStatus::Cancelled);
}

void test_v11_server_relative_ttl_without_device_clock() {
  Rig r;  // device clock NOT synced
  // expires 30 s after server_time -> ttl 30 s from receipt
  std::string j = arr(
      "{\"command_id\":\"t1\",\"chamber_id\":\"A\",\"action\":\"set_setpoint\","
      "\"parameters\":{\"setpoint_c\":20},\"issued_at_utc\":\"2026-10-31T11:59:00Z\","
      "\"expires_at_utc\":\"2026-10-31T12:00:30Z\",\"sequence_number\":1}");
  DesiredControlParse p;
  parseDesiredControl(j.c_str(), j.size(), 1000, p);
  TEST_ASSERT_TRUE(p.cmds[0].has_ttl);
  TEST_ASSERT_EQUAL_INT64(30000, p.cmds[0].ttl_ms);
  r.cp.handle(p, 31001, r.clk);  // processed 30.001 s after receipt -> expired
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Expired);
  TEST_ASSERT_EQUAL(0, r.tgt.setpoint_calls);
}

void test_v11_no_expiry_reference_rejected() {
  Rig r;  // no server_time, no ttl, device unsynced -> cannot prove freshness
  r.feed("{\"chamber_id\":\"A\",\"commands\":[{\"command_id\":\"n1\",\"chamber_id\":\"A\","
         "\"action\":\"set_setpoint\",\"parameters\":{\"setpoint_c\":20},"
         "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
         "\"sequence_number\":1}]}", 0, 0);
  TEST_ASSERT_EQUAL(0, r.tgt.setpoint_calls);
  TEST_ASSERT_TRUE(r.sink.acks.back().status == AckStatus::Rejected);
  // Once the clock is synced, the absolute expiry can be checked.
  int64_t now;
  parseIso8601Utc("2026-10-31T12:00:10Z", now);
  r.clk.syncFrom(now, 0);
  r.feed("{\"chamber_id\":\"A\",\"commands\":[{\"command_id\":\"n2\",\"chamber_id\":\"A\","
         "\"action\":\"set_setpoint\",\"parameters\":{\"setpoint_c\":21},"
         "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
         "\"sequence_number\":2}]}", 0, 1000);
  TEST_ASSERT_EQUAL(1, r.tgt.setpoint_calls);
  TEST_ASSERT_TRUE(r.sink.acks.back().epoch_ms > 0);
}

void test_v11_array_reack_rate_limited() {
  Rig r;
  std::string j = arr(
      "{\"command_id\":\"r1\",\"chamber_id\":\"A\",\"action\":\"noop\",\"parameters\":{},"
      "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
      "\"sequence_number\":1}");
  r.feed(j, 0, 0);
  r.feed(j, 2000, 2000);
  TEST_ASSERT_EQUAL(1, (int)r.sink.acks.size());  // too soon
  r.feed(j, 16000, 16000);
  TEST_ASSERT_EQUAL(2, (int)r.sink.acks.size());  // re-ack so the server can retire it
  TEST_ASSERT_EQUAL_STRING("r1", r.sink.acks[1].command_id);
}

void test_v11_motor_duration_from_seconds_or_ms() {
  DesiredControlParse p;
  std::string j = arr(
      "{\"command_id\":\"d1\",\"chamber_id\":\"A\",\"action\":\"animate_motor\","
      "\"parameters\":{\"duration_s\":2.5},\"issued_at_utc\":\"2026-10-31T12:00:00Z\","
      "\"expires_at_utc\":\"2026-10-31T12:01:00Z\",\"sequence_number\":1}");
  parseDesiredControl(j.c_str(), j.size(), 0, p);
  TEST_ASSERT_EQUAL_UINT32(2500, p.cmds[0].duration_ms);
}

// ------------------------------------------------------------------ ack serialization
static std::vector<std::string> keysOf(const char* json) {
  JsonDocument d;
  deserializeJson(d, json);
  std::vector<std::string> k;
  for (JsonPair kv : d.as<JsonObject>()) k.push_back(kv.key().c_str());
  return k;
}

#ifndef FERMENTAI_CONTRACTS_V11_DIR
#define FERMENTAI_CONTRACTS_V11_DIR "../contracts"
#endif

// Frozen v1.0 ack keys (firmware_contract/README_FOR_ESP32_AGENT.md §3.3).
static const char* kAckV10Keys[] = {"schema_version", "command_id", "chamber_id", "status",
                                    "sequence_number", "timestamp_utc", "applied_setpoint_c", "message"};

void test_ack_v10_keys_match_frozen_list_exactly() {
  AckRecord a;
  strcpy(a.command_id, "c1");
  a.has_applied_setpoint = true;
  a.applied_setpoint_c = 24.0f;
  a.sequence_number = 42;
  char buf[512];
  TEST_ASSERT_TRUE(serializeAck(a, "A", "fermentai-esp32-a", ContractProfile::V1_0, buf, sizeof buf) > 0);
  auto got = keysOf(buf);
  TEST_ASSERT_EQUAL(sizeof(kAckV10Keys) / sizeof(kAckV10Keys[0]), got.size());
  for (size_t i = 0; i < got.size(); ++i) TEST_ASSERT_EQUAL_STRING(kAckV10Keys[i], got[i].c_str());
  TEST_ASSERT_NOT_NULL(strstr(buf, "\"status\":\"accepted\""));
  TEST_ASSERT_NOT_NULL(strstr(buf, "\"timestamp_utc\":null"));
  // Every key of the generated v1.0 fixture that is non-null is one we can emit.
  std::string fx = readFile("control_ack_example.json");
  TEST_ASSERT_TRUE(!fx.empty());
  JsonDocument f;
  deserializeJson(f, fx);
  for (JsonPair kv : f.as<JsonObject>()) {
    bool ours = false;
    for (const char* k : kAckV10Keys) ours |= !strcmp(k, kv.key().c_str());
    if (!ours) TEST_ASSERT_TRUE_MESSAGE(kv.value().isNull(), kv.key().c_str());
  }
}

void test_ack_keys_within_schema() {
  std::string path = std::string(FERMENTAI_CONTRACTS_V11_DIR) + "/acknowledgment_v1.schema.json";
  FILE* fp = fopen(path.c_str(), "rb");
  if (!fp) {
    TEST_IGNORE_MESSAGE("contracts/acknowledgment_v1.schema.json not present");
    return;
  }
  std::string sc;
  char b[4096];
  size_t n;
  while ((n = fread(b, 1, sizeof b, fp)) > 0) sc.append(b, n);
  fclose(fp);
  JsonDocument schema;
  TEST_ASSERT_TRUE(deserializeJson(schema, sc) == DeserializationError::Ok);
  JsonObject props = schema["properties"];
  AckRecord a;
  strcpy(a.command_id, "c1");
  a.has_applied_setpoint = true;
  a.applied_setpoint_c = 24.0f;
  a.ran_ms = 10;
  strcpy(a.message, "x");
  char buf[512];
  for (ContractProfile p : {ContractProfile::V1_0, ContractProfile::V1_1}) {
    serializeAck(a, "A", "fermentai-esp32-a", p, buf, sizeof buf);
    for (const auto& k : keysOf(buf))
      TEST_ASSERT_TRUE_MESSAGE(hasKey(props, k.c_str()), (std::string("not in schema: ") + k).c_str());
  }
  JsonArray st = schema["properties"]["status"]["enum"];
  if (!st.isNull()) {
    for (AckStatus s : {AckStatus::Accepted, AckStatus::Rejected, AckStatus::Completed, AckStatus::Failed,
                        AckStatus::Cancelled, AckStatus::Expired}) {
      bool ok = false;
      for (JsonVariant v : st) ok |= !strcmp(v.as<const char*>(), ackStatusName(s));
      TEST_ASSERT_TRUE_MESSAGE(ok, ackStatusName(s));
    }
  }
}

void test_ack_v11_superset() {
  AckRecord a;
  strcpy(a.command_id, "m1");
  a.status = AckStatus::Completed;
  a.ran_ms = 3000;
  strcpy(a.message, "ran 3.0 s");
  a.uptime_ms = 123456;
  char buf[512];
  TEST_ASSERT_TRUE(serializeAck(a, "A", "fermentai-esp32-a", ContractProfile::V1_1, buf, sizeof buf) > 0);
  JsonDocument d;
  TEST_ASSERT_TRUE(deserializeJson(d, buf) == DeserializationError::Ok);
  TEST_ASSERT_EQUAL_STRING("1.0", d["schema_version"]);
  TEST_ASSERT_EQUAL_STRING("fermentai-esp32-a", d["device_id"]);
  TEST_ASSERT_EQUAL_UINT32(123456, d["device_uptime_ms"].as<uint32_t>());
  TEST_ASSERT_EQUAL_STRING("ran 3.0 s", d["detail"]);
  TEST_ASSERT_EQUAL_STRING("completed", d["status"]);
  TEST_ASSERT_EQUAL(3000, d["result"]["ran_ms"].as<int>());
  TEST_ASSERT_TRUE(d["applied_setpoint_c"].isNull());
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_parse_contract_fixture_command_example);
  RUN_TEST(test_parse_contract_fixture_no_motor);
  RUN_TEST(test_parse_garbage_rejected);
  RUN_TEST(test_fixture_flat_and_array_execute_once);
  RUN_TEST(test_v11_array_sequence_le_highest_ignored);
  RUN_TEST(test_new_command_applied_and_acked_once);
  RUN_TEST(test_setpoint_clamped_to_local_limits);
  RUN_TEST(test_expiry_relative_to_receipt);
  RUN_TEST(test_expired_ack_sent_once);
  RUN_TEST(test_lower_sequence_ignored);
  RUN_TEST(test_epoch_change_resets_sequence);
  RUN_TEST(test_wrong_chamber_rejected);
  RUN_TEST(test_ring_buffer_bounded_and_replay);
  RUN_TEST(test_motor_not_replayed_after_reboot);
  RUN_TEST(test_motor_rejected_reason_propagates);
  RUN_TEST(test_v11_array_all_actions);
  RUN_TEST(test_v11_server_relative_ttl_without_device_clock);
  RUN_TEST(test_v11_no_expiry_reference_rejected);
  RUN_TEST(test_v11_array_reack_rate_limited);
  RUN_TEST(test_v11_motor_duration_from_seconds_or_ms);
  RUN_TEST(test_ack_v10_keys_match_frozen_list_exactly);
  RUN_TEST(test_ack_keys_within_schema);
  RUN_TEST(test_ack_v11_superset);
  return UNITY_END();
}
