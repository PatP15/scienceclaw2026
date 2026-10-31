// SerialTransport tests (serial_v1, newline-delimited JSON over USB CDC/UART), driven through the
// UNCHANGED TelemetryClient / CommandClient / CommandProcessor, exactly as main.cpp would use them.
#include <string>
#include <unity.h>
#include <vector>

#include "app/CommandProcessor.h"
#include "network/AckQueue.h"
#include "network/CommandClient.h"
#include "network/SerialTransport.h"
#include "network/TelemetryClient.h"
#include "simulation/MockActuators.h"
#include "telemetry_schema.h"

using namespace fa;

void setUp() {}
void tearDown() {}

// In-memory full-duplex port: `in` = host->device bytes, `out` = device->host bytes.
struct FakePort : ISerialPort {
  std::string in;
  size_t pos = 0;
  std::string out;
  uint32_t writes = 0;
  int available() override { return (int)(in.size() - pos); }
  int read() override { return pos < in.size() ? (unsigned char)in[pos++] : -1; }
  size_t write(const uint8_t* d, size_t n) override {
    out.append((const char*)d, n);
    ++writes;
    return n;
  }
  void host(const std::string& line) { in += line + "\n"; }
  std::vector<std::string> lines() {  // complete device->host lines
    std::vector<std::string> v;
    size_t s = 0, e;
    while ((e = out.find('\n', s)) != std::string::npos) {
      v.push_back(out.substr(s, e - s));
      s = e + 1;
    }
    return v;
  }
  std::vector<std::string> linesOfType(const char* t) {
    std::vector<std::string> v;
    for (auto& l : lines()) {
      JsonDocument d;
      if (deserializeJson(d, l) == DeserializationError::Ok && d["type"] == t) v.push_back(l);
    }
    return v;
  }
};

struct Rig {
  FakePort port;
  SerialTransportConfig cfg;
  SerialTransport* st;
  uint32_t t = 1000;
  Rig() {
    cfg.device_id = "fermentai-esp32-a";
    cfg.chamber_id = "A";
    cfg.firmware_version = "0.3.0";
    cfg.boot_id = "a1b2c3d4";
    st = new SerialTransport(&port, cfg);
    st->nowFn = [this] { return t; };
  }
  ~Rig() { delete st; }
  void ack(const char* ts = "2026-10-31T12:00:00Z") {
    port.host(std::string("{\"type\":\"hello_ack\",\"v\":\"1.0\",\"chamber_id\":\"A\",\"server_time_utc\":\"") +
              ts + "\",\"poll_interval_s\":2}");
    st->poll(t);
  }
};

static std::string cmdLine(const char* id, const char* action, const char* params, int seq,
                           const char* expires = "2026-10-31T12:01:30Z") {
  char b[1024];
  snprintf(b, sizeof b,
           "{\"type\":\"command\",\"v\":\"1.0\",\"payload\":{\"schema_version\":\"1.0\",\"command_id\":\"%s\","
           "\"chamber_id\":\"A\",\"experiment_id\":\"exp1\",\"issued_at_utc\":\"2026-10-31T12:00:00Z\","
           "\"expires_at_utc\":\"%s\",\"sequence_number\":%d,\"action\":\"%s\",\"parameters\":%s}}",
           id, expires, seq, action, params);
  return b;
}

// ------------------------------------------------------------------ hello handshake
void test_hello_at_boot_and_every_30s_until_ack() {
  Rig r;
  r.st->begin(r.t);
  auto h = r.port.linesOfType("hello");
  TEST_ASSERT_EQUAL(1, (int)h.size());
  JsonDocument d;
  deserializeJson(d, h[0]);
  TEST_ASSERT_EQUAL_STRING("1.0", d["v"]);
  TEST_ASSERT_EQUAL_STRING("fermentai-esp32-a", d["device_id"]);
  TEST_ASSERT_EQUAL_STRING("A", d["chamber_id"]);
  TEST_ASSERT_EQUAL_STRING("0.3.0", d["firmware_version"]);
  TEST_ASSERT_EQUAL_STRING("a1b2c3d4", d["boot_id"]);
  TEST_ASSERT_TRUE(d["capabilities"].size() >= 5);
  for (uint32_t k = 0; k < 29; ++k) r.st->poll(r.t += 1000);
  TEST_ASSERT_EQUAL(1, (int)r.port.linesOfType("hello").size());
  r.st->poll(r.t += 1000);  // 30 s
  TEST_ASSERT_EQUAL(2, (int)r.port.linesOfType("hello").size());
  TEST_ASSERT_FALSE(r.st->connected());
  r.ack();
  TEST_ASSERT_TRUE(r.st->connected());
  TEST_ASSERT_EQUAL_UINT32(2, r.st->pollIntervalS());
  for (int k = 0; k < 120; ++k) r.st->poll(r.t += 1000);
  TEST_ASSERT_EQUAL(2, (int)r.port.linesOfType("hello").size());  // stops after hello_ack
}

void test_hello_ack_sets_clock_and_wrong_chamber_ignored() {
  Rig r;
  r.st->begin(r.t);
  r.port.host("{\"type\":\"hello_ack\",\"v\":\"1.0\",\"chamber_id\":\"B\",\"server_time_utc\":\"2026-10-31T12:00:00Z\"}");
  r.st->poll(r.t);
  TEST_ASSERT_FALSE(r.st->connected());
  TEST_ASSERT_FALSE(r.st->clock().hasSync());
  TEST_ASSERT_EQUAL(1, (int)r.port.linesOfType("log").size());
  r.ack("2026-10-31T12:00:00Z");
  TEST_ASSERT_TRUE(r.st->clock().hasSync());
  TEST_ASSERT_EQUAL_INT64(1793448005000LL, r.st->clock().nowEpochMs(r.t + 5000));
}

void test_requests_transient_before_hello_ack() {
  Rig r;
  r.st->begin(r.t);
  TelemetryClient tc(r.st, 50, 4000);
  TelemetryRecord rec;
  tc.enqueue(rec, ContractProfile::V1_1);
  TEST_ASSERT_EQUAL(0, (int)tc.drain(r.t));
  TEST_ASSERT_EQUAL(1, (int)tc.queued());  // kept, not lost
  TEST_ASSERT_EQUAL(0, (int)r.port.linesOfType("telemetry").size());
  r.ack();
  TEST_ASSERT_EQUAL(1, (int)tc.drain(r.t += 2000));  // after backoff
  TEST_ASSERT_EQUAL(1, (int)r.port.linesOfType("telemetry").size());
}

// ------------------------------------------------------------------ device -> host payloads
void test_telemetry_line_wraps_exact_telemetry_v1_payload() {
  Rig r;
  r.st->begin(r.t);
  r.ack();
  TelemetryClient tc(r.st, 50, 4000);
  TelemetryRecord rec;
  rec.device_id = "fermentai-esp32-a";
  rec.boot_id = "a1b2c3d4";
  rec.obs.chamber_temp = Reading::ok(24.25f, 0, Provenance::Measured);
  tc.enqueue(rec, ContractProfile::V1_1);
  TEST_ASSERT_EQUAL(1, (int)tc.drain(r.t));
  auto l = r.port.linesOfType("telemetry");
  TEST_ASSERT_EQUAL(1, (int)l.size());
  TEST_ASSERT_TRUE(l[0].size() < 8192);
  JsonDocument d;
  TEST_ASSERT_TRUE(deserializeJson(d, l[0]) == DeserializationError::Ok);
  TEST_ASSERT_EQUAL_STRING("1.0", d["v"]);
  JsonObject p = d["payload"];
  // Byte-identical to what the telemetry_schema.h serializer produces for HTTP.
  JsonDocument ref;
  buildTelemetryJson(rec, ref, ContractProfile::V1_1);
  std::string a, b;
  serializeJson(p, a);
  serializeJson(ref, b);
  TEST_ASSERT_EQUAL_STRING(b.c_str(), a.c_str());
  TEST_ASSERT_EQUAL(1, p["sequence_number"].as<int>());
  TEST_ASSERT_EQUAL_FLOAT(24.25f, p["chamber_temperature_c"].as<float>());
}

void test_ack_line_from_command_client() {
  Rig r;
  r.st->begin(r.t);
  r.ack();
  CommandClient cc(r.st, "A", "fermentai-esp32-a", ContractProfile::V1_1, 4000, 6);
  AckQueue q;
  AckRecord a;
  strcpy(a.command_id, "c1");
  a.status = AckStatus::Completed;
  a.ran_ms = 3000;
  q.pushAck(a);
  TEST_ASSERT_EQUAL(1, (int)cc.drainAcks(q, r.t));
  auto l = r.port.linesOfType("ack");
  TEST_ASSERT_EQUAL(1, (int)l.size());
  JsonDocument d;
  deserializeJson(d, l[0]);
  TEST_ASSERT_EQUAL_STRING("c1", d["payload"]["command_id"]);
  TEST_ASSERT_EQUAL_STRING("completed", d["payload"]["status"]);
  TEST_ASSERT_EQUAL_STRING("fermentai-esp32-a", d["payload"]["device_id"]);
  TEST_ASSERT_EQUAL(3000, d["payload"]["result"]["ran_ms"].as<int>());
}

void test_oversize_outbound_payload_is_permanent_413() {
  Rig r;
  r.cfg.max_line_bytes = 256;
  SerialTransport st(&r.port, r.cfg);
  st.begin(r.t);
  r.port.host("{\"type\":\"hello_ack\",\"v\":\"1.0\",\"chamber_id\":\"A\"}");
  st.poll(r.t);
  std::string big = "{\"x\":\"" + std::string(400, 'a') + "\"}";
  std::string resp;
  TEST_ASSERT_EQUAL(413, st.request("POST", "/api/v1/telemetry", "application/json",
                                    (const uint8_t*)big.data(), big.size(), &resp, 1000));
  TEST_ASSERT_EQUAL(0, (int)r.port.linesOfType("telemetry").size());
}

// ------------------------------------------------------------------ host -> device commands
struct Target : ICommandTarget {
  float sp = NAN;
  int setpoints = 0, motors = 0, stops = 0;
  void applySetpoint(float c, ControlMode, const char*, uint32_t) override {
    sp = c;
    ++setpoints;
  }
  bool startMotor(uint32_t d, const char*, const char*, int64_t, uint32_t, uint32_t* g, char*, size_t) override {
    ++motors;
    if (g) *g = d;
    return true;
  }
  bool stopMotor(uint32_t, char*, size_t) override {
    ++stops;
    return false;
  }
  bool requestImage(const char*, int64_t, char* why, size_t n) override {
    snprintf(why, n, "camera not available");
    return false;
  }
};

struct E2E : Rig {
  MockKeyValueStore kv;
  Target tgt;
  AckQueue acks;
  ClockSync clk;  // the firmware's clock (g.clock in main.cpp), synced via CommandClient
  CommandProcessor cp{"A", &kv, &tgt, &acks};
  CommandClient cc{st, "A", "fermentai-esp32-a", ContractProfile::V1_1, 4000, 6};
  E2E() {
    cp.begin();
    cc.onServerTime = [this](int64_t ms, uint32_t recv) { clk.syncFrom(ms, recv); };
    st->begin(t);
    ack();
  }
  bool pollOnce() {  // what netTask + tCommands do
    st->poll(t);
    DesiredControlParse p;
    if (!cc.poll(t, p)) return false;
    cp.handle(p, t, clk);
    cc.drainAcks(acks, t);
    return true;
  }
};

void test_command_set_setpoint_end_to_end() {
  E2E e;
  e.port.host(cmdLine("s1", "set_setpoint", "{\"setpoint_c\":40,\"control_mode\":\"auto\"}", 10));
  TEST_ASSERT_TRUE(e.pollOnce());
  TEST_ASSERT_EQUAL(1, e.tgt.setpoints);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 35.0f, e.tgt.sp);  // local clamp still applies
  TEST_ASSERT_TRUE(e.clk.hasSync());                 // envelope server_time -> firmware clock
  auto l = e.port.linesOfType("ack");
  TEST_ASSERT_EQUAL(1, (int)l.size());
  JsonDocument d;
  deserializeJson(d, l[0]);
  TEST_ASSERT_EQUAL_STRING("s1", d["payload"]["command_id"]);
  TEST_ASSERT_EQUAL_STRING("accepted", d["payload"]["status"]);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 35.0, d["payload"]["result"]["applied_setpoint_c"].as<double>());
  TEST_ASSERT_TRUE(d["payload"]["timestamp_utc"].is<const char*>());  // synced -> real timestamp
  // Host re-sends the same command (no ack seen yet): not executed again.
  e.port.host(cmdLine("s1", "set_setpoint", "{\"setpoint_c\":40}", 10));
  e.t += 2000;
  e.pollOnce();
  TEST_ASSERT_EQUAL(1, e.tgt.setpoints);
}

void test_command_ttl_measured_from_receipt_not_poll() {
  E2E e;  // clock synced at 12:00:00 by hello_ack, at t=1000
  // Expires 5 s after the device's notion of "now" at receipt.
  e.port.host(cmdLine("x1", "set_setpoint", "{\"setpoint_c\":20}", 11, "2026-10-31T12:00:05Z"));
  e.st->poll(e.t);  // received at t=1000 -> ttl 5 s
  e.t += 6000;      // CommandClient polls 6 s later
  TEST_ASSERT_TRUE(e.pollOnce());
  TEST_ASSERT_EQUAL(0, e.tgt.setpoints);
  auto l = e.port.linesOfType("ack");
  JsonDocument d;
  deserializeJson(d, l.back());
  TEST_ASSERT_EQUAL_STRING("expired", d["payload"]["status"]);
}

void test_command_server_time_on_message_used_for_ttl() {
  Rig r;  // NO hello_ack clock: connect with a hello_ack lacking server_time
  r.st->begin(r.t);
  r.port.host("{\"type\":\"hello_ack\",\"v\":\"1.0\",\"chamber_id\":\"A\"}");
  r.st->poll(r.t);
  TEST_ASSERT_FALSE(r.st->clock().hasSync());
  std::string line = cmdLine("m1", "animate_motor", "{\"duration_ms\":3000,\"pattern\":\"wiggle\"}", 12,
                             "2026-10-31T12:01:00Z");
  line.insert(line.size() - 1, ",\"server_time_utc\":\"2026-10-31T12:00:00Z\"");
  r.port.host(line);
  r.st->poll(r.t);
  r.t += 1500;
  std::string resp;
  TEST_ASSERT_EQUAL(200, r.st->request("GET", "/api/v1/chambers/A/desired-control", nullptr, nullptr, 0, &resp, 0));
  DesiredControlParse p;
  TEST_ASSERT_TRUE(parseDesiredControl(resp.data(), resp.size(), r.t, p));
  TEST_ASSERT_EQUAL_UINT32(1, p.n);
  TEST_ASSERT_TRUE(p.cmds[0].action == CmdAction::AnimateMotor);
  TEST_ASSERT_EQUAL_UINT32(3000, p.cmds[0].duration_ms);
  TEST_ASSERT_TRUE(p.cmds[0].has_ttl);
  TEST_ASSERT_INT64_WITHIN(1, 58500, p.cmds[0].ttl_ms);  // 60 s - 1.5 s queued
  TEST_ASSERT_FALSE(p.cmds[0].flat);
  // Drained: next poll is empty
  TEST_ASSERT_EQUAL(200, r.st->request("GET", "/api/v1/chambers/A/desired-control", nullptr, nullptr, 0, &resp, 0));
  parseDesiredControl(resp.data(), resp.size(), r.t, p);
  TEST_ASSERT_EQUAL_UINT32(0, p.n);
}

void test_commands_sorted_and_batched_8_per_poll() {
  E2E e;
  for (int i = 20; i >= 11; --i) {  // 10 commands, arriving in descending order
    char id[8];
    snprintf(id, sizeof id, "n%d", i);
    e.port.host(cmdLine(id, "noop", "{}", i));
  }
  e.st->poll(e.t);
  TEST_ASSERT_EQUAL(10, (int)e.st->pendingCommands());
  e.pollOnce();
  TEST_ASSERT_EQUAL(2, (int)e.st->pendingCommands());
  e.t += 2000;
  e.pollOnce();
  auto acks = e.port.linesOfType("ack");
  TEST_ASSERT_EQUAL(10, (int)acks.size());  // ascending processing: nothing rejected as stale
  for (auto& l : acks) {
    JsonDocument d;
    deserializeJson(d, l);
    TEST_ASSERT_EQUAL_STRING("completed", d["payload"]["status"]);
  }
}

void test_motor_and_stop_end_to_end() {
  E2E e;
  e.port.host(cmdLine("m1", "animate_motor", "{\"duration_ms\":3000}", 30));
  e.port.host(cmdLine("m2", "stop_motor", "{\"target_command_id\":\"m1\"}", 31));
  e.pollOnce();
  TEST_ASSERT_EQUAL(1, e.tgt.motors);
  TEST_ASSERT_EQUAL(1, e.tgt.stops);
}

// ------------------------------------------------------------------ framing robustness
void test_time_ping_pong_and_partial_lines() {
  Rig r;
  r.st->begin(r.t);
  r.port.in += "{\"type\":\"ti";  // partial line across polls
  r.st->poll(r.t);
  TEST_ASSERT_FALSE(r.st->clock().hasSync());
  r.port.in += "me\",\"v\":\"1.0\",\"server_time_utc\":\"2026-10-31T13:00:00Z\"}\r\n";
  r.st->poll(r.t);
  TEST_ASSERT_TRUE(r.st->clock().hasSync());
  r.port.host("{\"type\":\"ping\",\"v\":\"1.0\",\"id\":42}");
  r.st->poll(r.t);
  auto p = r.port.linesOfType("pong");
  TEST_ASSERT_EQUAL(1, (int)p.size());
  JsonDocument d;
  deserializeJson(d, p[0]);
  TEST_ASSERT_EQUAL(42, d["id"].as<int>());
  TEST_ASSERT_EQUAL_STRING("1.0", d["v"]);
}

void test_oversize_line_dropped_next_line_ok() {
  Rig r;
  r.st->begin(r.t);
  r.port.host("{\"type\":\"command\",\"pad\":\"" + std::string(9000, 'x') + "\"}");
  r.port.host("{\"type\":\"ping\",\"v\":\"1.0\"}");
  for (int i = 0; i < 5; ++i) r.st->poll(r.t);  // rx budget 4096/poll
  TEST_ASSERT_EQUAL_UINT32(1, r.st->stats().oversize_dropped);
  TEST_ASSERT_EQUAL(1, (int)r.port.linesOfType("pong").size());
  TEST_ASSERT_EQUAL(0, (int)r.st->pendingCommands());
}

void test_garbage_non_json_unknown_and_bad_version_ignored() {
  Rig r;
  r.st->begin(r.t);
  r.port.host("FermentAI 0.3.0 chamber=A boot banner");  // console noise
  r.port.host("{not json");
  r.port.host("{\"type\":\"teleport\",\"v\":\"1.0\"}");
  r.port.host("{\"type\":\"ping\",\"v\":\"2.0\"}");
  r.port.host("{\"type\":\"command\",\"v\":\"1.0\",\"payload\":{}}");
  r.st->poll(r.t);
  const auto& s = r.st->stats();
  TEST_ASSERT_EQUAL_UINT32(1, s.non_json);
  TEST_ASSERT_EQUAL_UINT32(2, s.bad_json);  // "{not json" + command without command_id
  TEST_ASSERT_EQUAL_UINT32(1, s.unknown_type);
  TEST_ASSERT_EQUAL_UINT32(1, s.bad_version);
  TEST_ASSERT_EQUAL(0, (int)r.port.linesOfType("pong").size());
  TEST_ASSERT_EQUAL(0, (int)r.st->pendingCommands());
}

void test_rx_is_bounded_per_poll() {
  Rig r;
  r.st->begin(r.t);
  for (int i = 0; i < 400; ++i) r.port.host("{\"type\":\"ping\",\"v\":\"1.0\"}");  // ~10 KB
  r.st->poll(r.t);
  TEST_ASSERT_TRUE(r.port.pos <= r.cfg.rx_budget_bytes);  // one poll never drains everything
}

void test_log_helper_queued_bounded_and_flushed() {
  Rig r;
  r.st->begin(r.t);
  for (int i = 0; i < 40; ++i) r.st->log("info", "hello from the safety loop");  // never writes
  TEST_ASSERT_EQUAL(0, (int)r.port.linesOfType("log").size());
  r.st->poll(r.t);
  auto l = r.port.linesOfType("log");
  TEST_ASSERT_EQUAL((int)r.cfg.max_log_lines, (int)l.size());
  TEST_ASSERT_EQUAL_UINT32(40 - r.cfg.max_log_lines, r.st->stats().logs_dropped);
  JsonDocument d;
  deserializeJson(d, l[0]);
  TEST_ASSERT_EQUAL_STRING("info", d["level"]);
  TEST_ASSERT_EQUAL_STRING("1.0", d["v"]);
}

void test_every_line_is_one_write_and_newline_terminated() {
  Rig r;
  r.st->begin(r.t);
  r.ack();
  r.st->log("warn", "x");
  r.st->poll(r.t);
  TEST_ASSERT_EQUAL(r.port.writes, (uint32_t)r.port.lines().size());
  TEST_ASSERT_EQUAL('\n', r.port.out.back());
}

void test_images_not_available_over_serial() {
  Rig r;
  r.st->begin(r.t);
  r.ack();
  uint8_t jpg[4] = {0xFF, 0xD8, 0xFF, 0xD9};
  TEST_ASSERT_EQUAL(501, r.st->request("POST", "/api/v1/images?chamber_id=A", "image/jpeg", jpg, 4, nullptr, 0));
}

void test_host_timeout_optional_rehello() {
  Rig r;
  r.cfg.host_timeout_ms = 60000;
  SerialTransport st(&r.port, r.cfg);
  st.begin(r.t);
  r.port.host("{\"type\":\"hello_ack\",\"v\":\"1.0\",\"chamber_id\":\"A\"}");
  st.poll(r.t);
  TEST_ASSERT_TRUE(st.connected());
  st.poll(r.t += 61000);
  TEST_ASSERT_FALSE(st.connected());
  TEST_ASSERT_EQUAL(2, (int)r.port.linesOfType("hello").size());
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_hello_at_boot_and_every_30s_until_ack);
  RUN_TEST(test_hello_ack_sets_clock_and_wrong_chamber_ignored);
  RUN_TEST(test_requests_transient_before_hello_ack);
  RUN_TEST(test_telemetry_line_wraps_exact_telemetry_v1_payload);
  RUN_TEST(test_ack_line_from_command_client);
  RUN_TEST(test_oversize_outbound_payload_is_permanent_413);
  RUN_TEST(test_command_set_setpoint_end_to_end);
  RUN_TEST(test_command_ttl_measured_from_receipt_not_poll);
  RUN_TEST(test_command_server_time_on_message_used_for_ttl);
  RUN_TEST(test_commands_sorted_and_batched_8_per_poll);
  RUN_TEST(test_motor_and_stop_end_to_end);
  RUN_TEST(test_time_ping_pong_and_partial_lines);
  RUN_TEST(test_oversize_line_dropped_next_line_ok);
  RUN_TEST(test_garbage_non_json_unknown_and_bad_version_ignored);
  RUN_TEST(test_rx_is_bounded_per_poll);
  RUN_TEST(test_log_helper_queued_bounded_and_flushed);
  RUN_TEST(test_every_line_is_one_write_and_newline_terminated);
  RUN_TEST(test_images_not_available_over_serial);
  RUN_TEST(test_host_timeout_optional_rehello);
  return UNITY_END();
}
