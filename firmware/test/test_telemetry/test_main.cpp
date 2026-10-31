// Telemetry tests: JSON matches the contract fixture field-for-field (v1.0), v1.1 superset,
// enum values, null-not-NaN, unsynced clock -> timestamp null, provenance/health maps; plus
// ISO-8601 utilities, scheduler and backoff.
#include <math.h>
#include <stdio.h>
#include <string>
#include <unity.h>
#include <set>

#include "telemetry_schema.h"
#include "util/Scheduler.h"
#include "util/TimeUtil.h"

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
#ifndef FERMENTAI_CONTRACTS_V11_DIR
#define FERMENTAI_CONTRACTS_V11_DIR "../contracts"
#endif

static std::string readPath(const std::string& path) {
  FILE* f = fopen(path.c_str(), "rb");
  if (!f) return std::string();
  std::string s;
  char buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
  fclose(f);
  return s;
}

static TelemetryRecord sample(bool synced) {
  TelemetryRecord t;
  t.chamber_id = "A";
  t.device_id = "fermentai-esp32-a";
  t.experiment_id = "trial_001";
  t.boot_id = "a1b2c3d4";
  t.firmware_version = "0.3.0";
  t.sequence_number = 120;
  t.uptime_ms = 120000;
  if (synced) {
    parseIso8601Utc("2026-10-31T12:00:00Z", t.timestamp_epoch_ms);
    t.timestamp_quality = TimestampQuality::Synced;
  }
  auto m = [](float v) { return Reading::ok(v, 1000, Provenance::Simulated); };
  t.obs.chamber_temp = m(24.3f);
  t.obs.starter_temp = m(23.9f);
  t.obs.int_heatsink = m(31.2f);
  t.obs.ext_heatsink = m(37.1f);
  t.obs.rh = m(72.0f);
  t.obs.pressure_pa = m(101325.0f);
  t.obs.gas_ohm = m(105432.0f);
  t.obs.door_known = true;
  t.obs.door_open = false;
  t.obs.setHealth("bme680", SensorHealth::Ok);
  t.obs.setHealth("ds18b20_starter", SensorHealth::Ok);
  t.obs.setHealth("ph", SensorHealth::Uncalibrated);
  t.obs.flags.add("ph_uncalibrated");
  t.peltier_mode = PeltierMode::Heating;
  t.peltier_output_fraction = 0.35f;
  t.setpoint = Reading::ok(25.0f, 0, Provenance::None);
  t.simulated = true;
  t.wifi_rssi_valid = true;
  t.wifi_rssi_dbm = -58;
  t.free_heap_bytes = 180000;
  t.last_command_id = "6f1c2a9e";
  t.last_sequence_number = 41;
  return t;
}

static void toDoc(const TelemetryRecord& t, ContractProfile p, JsonDocument& d, std::string& s) {
  char buf[4096];
  const size_t n = serializeTelemetry(t, buf, sizeof buf, p);
  TEST_ASSERT_TRUE(n > 0);
  s.assign(buf, n);
  TEST_ASSERT_TRUE(deserializeJson(d, buf) == DeserializationError::Ok);
}

void test_v10_keys_equal_fixture() {
  std::string fx = readPath(std::string(FERMENTAI_CONTRACT_DIR) + "/telemetry_example.json");
  TEST_ASSERT_TRUE_MESSAGE(!fx.empty(), "firmware_contract/telemetry_example.json not found");
  JsonDocument f;
  TEST_ASSERT_TRUE(deserializeJson(f, fx) == DeserializationError::Ok);
  JsonDocument d;
  std::string s;
  toDoc(sample(true), ContractProfile::V1_0, d, s);
  std::set<std::string> want, got;
  for (JsonPair kv : f.as<JsonObject>()) want.insert(kv.key().c_str());
  for (JsonPair kv : d.as<JsonObject>()) got.insert(kv.key().c_str());
  for (const auto& k : want) TEST_ASSERT_TRUE_MESSAGE(got.count(k), ("missing v1.0 key: " + k).c_str());
  for (const auto& k : got) TEST_ASSERT_TRUE_MESSAGE(want.count(k), ("extra key (v1.0 forbids): " + k).c_str());
  // The compiled-in key list also matches the fixture.
  TEST_ASSERT_EQUAL(want.size(), sizeof(kTelemetryV10Keys) / sizeof(kTelemetryV10Keys[0]));
  for (const char* k : kTelemetryV10Keys) TEST_ASSERT_TRUE(want.count(k));
  // Types compatible with the fixture for every non-null fixture value
  for (JsonPair kv : f.as<JsonObject>()) {
    JsonVariant fv = kv.value(), gv = d[kv.key()];
    if (fv.isNull() || gv.isNull()) continue;
    if (fv.is<const char*>()) TEST_ASSERT_TRUE_MESSAGE(gv.is<const char*>(), kv.key().c_str());
    if (fv.is<bool>()) TEST_ASSERT_TRUE_MESSAGE(gv.is<bool>(), kv.key().c_str());
    if (fv.is<JsonArray>()) TEST_ASSERT_TRUE_MESSAGE(gv.is<JsonArray>(), kv.key().c_str());
    if (fv.is<double>() && !fv.is<bool>())
      TEST_ASSERT_TRUE_MESSAGE(gv.is<double>() || gv.is<long>(), kv.key().c_str());
  }
  TEST_ASSERT_EQUAL_STRING("1.0", d["schema_version"]);
  TEST_ASSERT_EQUAL_STRING("heat", d["peltier_mode"]);       // v1.0 spelling
  TEST_ASSERT_EQUAL_STRING("simulated", d["source"]);
  TEST_ASSERT_EQUAL_STRING("2026-10-31T12:00:00Z", d["timestamp_utc"]);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 37.1, d["heatsink_temperature_c"].as<double>());  // hot side
  TEST_ASSERT_FLOAT_WITHIN(0.01, 35.0, d["peltier_duty_percent"].as<double>());
}

void test_v10_enum_values() {
  const char* allowed[] = {"idle", "heat", "cool", "switching", "fault", "off"};
  PeltierMode modes[] = {PeltierMode::Idle, PeltierMode::Heating, PeltierMode::Cooling,
                         PeltierMode::Switching, PeltierMode::Fault, PeltierMode::Off};
  for (PeltierMode m : modes) {
    const char* n = peltierModeName(m, ContractProfile::V1_0);
    bool ok = false;
    for (const char* a : allowed) ok |= !strcmp(a, n);
    TEST_ASSERT_TRUE_MESSAGE(ok, n);
  }
  TEST_ASSERT_EQUAL_STRING("real", sourceName(false, ContractProfile::V1_0));
}

void test_v11_superset_and_values() {
  JsonDocument d;
  std::string s;
  TelemetryRecord t = sample(false);
  toDoc(t, ContractProfile::V1_1, d, s);
  for (const char* k : kTelemetryV10Keys) TEST_ASSERT_TRUE_MESSAGE(hasKey(d.as<JsonObjectConst>(), k), k);
  for (const char* k : kTelemetryV11Keys) TEST_ASSERT_TRUE_MESSAGE(hasKey(d.as<JsonObjectConst>(), k), k);
  TEST_ASSERT_EQUAL_STRING("1.0", d["schema_version"]);  // v1.1 keeps "1.0" on the wire
  TEST_ASSERT_EQUAL_STRING("heating", d["peltier_mode"]);
  TEST_ASSERT_EQUAL_STRING("simulated", d["source"]);
  TEST_ASSERT_TRUE(d["timestamp_utc"].isNull());
  TEST_ASSERT_EQUAL_STRING("unsynced", d["timestamp_quality"]);
  TEST_ASSERT_EQUAL_STRING("a1b2c3d4", d["boot_id"]);
  TEST_ASSERT_EQUAL(120, d["sequence_number"].as<int>());
  TEST_ASSERT_EQUAL(120000, d["device_uptime_ms"].as<long>());
  TEST_ASSERT_FLOAT_WITHIN(0.001, 0.35, d["peltier_output_fraction"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(0.5, 101325.0, d["pressure_pa"].as<double>());
  TEST_ASSERT_EQUAL_STRING("ok", d["diagnostics"]["sensor_health"]["bme680"]);
  TEST_ASSERT_EQUAL_STRING("uncalibrated", d["diagnostics"]["sensor_health"]["ph"]);
  TEST_ASSERT_EQUAL(180000, d["diagnostics"]["free_heap_bytes"].as<long>());
  TEST_ASSERT_EQUAL_STRING("0.3.0", d["diagnostics"]["firmware_version"]);
  TEST_ASSERT_EQUAL_STRING("simulated", d["field_provenance"]["chamber_temperature_c"]);
  TEST_ASSERT_TRUE(d["field_provenance"]["ph"].isNull());  // null value -> no provenance entry
  TEST_ASSERT_EQUAL_STRING("measured", sourceName(false, ContractProfile::V1_1));
  const char* allowed[] = {"idle", "heat", "cool", "heating", "cooling", "switching", "fault", "off"};
  PeltierMode modes[] = {PeltierMode::Idle, PeltierMode::Heating, PeltierMode::Cooling,
                         PeltierMode::Switching, PeltierMode::Fault, PeltierMode::Off};
  for (PeltierMode m : modes) {
    bool ok = false;
    for (const char* a : allowed) ok |= !strcmp(a, peltierModeName(m, ContractProfile::V1_1));
    TEST_ASSERT_TRUE(ok);
  }
}

void test_v11_keys_within_published_schema_if_present() {
  std::string sc = readPath(std::string(FERMENTAI_CONTRACTS_V11_DIR) + "/telemetry_v1.schema.json");
  if (sc.empty()) {
    TEST_IGNORE_MESSAGE("contracts/telemetry_v1.schema.json not present yet (backend v1.1 in progress)");
    return;
  }
  JsonDocument schema;
  TEST_ASSERT_TRUE(deserializeJson(schema, sc) == DeserializationError::Ok);
  JsonObject props = schema["properties"];
  TEST_ASSERT_FALSE(props.isNull());
  JsonDocument d;
  std::string s;
  toDoc(sample(true), ContractProfile::V1_1, d, s);
  for (JsonPair kv : d.as<JsonObject>())
    TEST_ASSERT_TRUE_MESSAGE(hasKey(props, kv.key()), (std::string("not in schema: ") + kv.key().c_str()).c_str());
  JsonArray req = schema["required"];
  for (JsonVariant r : req) TEST_ASSERT_TRUE_MESSAGE(hasKey(d.as<JsonObjectConst>(), r.as<const char*>()), r.as<const char*>());
}

void test_missing_values_are_null_never_nan() {
  TelemetryRecord t;  // everything missing
  t.obs.chamber_temp.value = NAN;
  t.obs.chamber_temp.valid = true;  // even a buggy "valid NaN" must serialize as null
  t.peltier_output_fraction = NAN;
  char buf[4096];
  for (ContractProfile p : {ContractProfile::V1_0, ContractProfile::V1_1}) {
    TEST_ASSERT_TRUE(serializeTelemetry(t, buf, sizeof buf, p) > 0);
    TEST_ASSERT_NULL(strstr(buf, ":NaN"));
    TEST_ASSERT_NULL(strstr(buf, ":nan"));
    TEST_ASSERT_NULL(strstr(buf, ":Infinity"));
    TEST_ASSERT_NULL(strstr(buf, ":-Infinity"));
    JsonDocument d;
    TEST_ASSERT_TRUE(deserializeJson(d, buf) == DeserializationError::Ok);
    TEST_ASSERT_TRUE(d["chamber_temperature_c"].isNull());
    TEST_ASSERT_TRUE(d["ph"].isNull());
    TEST_ASSERT_TRUE(d["ph_method"].isNull());
    TEST_ASSERT_TRUE(d["door_open"].isNull());
    TEST_ASSERT_TRUE(d["temperature_setpoint_c"].isNull());
    TEST_ASSERT_TRUE(d["timestamp_utc"].isNull());
    TEST_ASSERT_TRUE(d["experiment_id"].isNull());
    TEST_ASSERT_EQUAL_STRING(p == ContractProfile::V1_0 ? "real" : "measured", d["source"]);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, d["peltier_duty_percent"].as<float>());
  }
}

void test_ph_method_only_with_valid_ph_and_faults_listed() {
  TelemetryRecord t = sample(false);
  t.ph_method = "probe_sample";
  t.fault_bits = kFaultSensorStale | kFaultHeatsinkOvertemp;
  char buf[4096];
  serializeTelemetry(t, buf, sizeof buf, ContractProfile::V1_0);
  JsonDocument d;
  deserializeJson(d, buf);
  TEST_ASSERT_TRUE(d["ph_method"].isNull());  // pH invalid -> method null
  TEST_ASSERT_EQUAL(2, d["fault_codes"].size());
  TEST_ASSERT_EQUAL_STRING("sensor_stale", d["fault_codes"][0]);
  TEST_ASSERT_EQUAL_STRING("heatsink_overtemp", d["fault_codes"][1]);
  t.obs.ph = Reading::ok(4.2f, 0, Provenance::Measured);
  serializeTelemetry(t, buf, sizeof buf, ContractProfile::V1_0);
  deserializeJson(d, buf);
  TEST_ASSERT_EQUAL_STRING("probe_sample", d["ph_method"]);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 4.2, d["ph"].as<double>());
}

void test_quality_flags_bounded_and_short() {
  TelemetryRecord t;
  for (int i = 0; i < 40; ++i) t.quality_flags.add("door_open_during_reading");  // dedup
  TEST_ASSERT_EQUAL(1, (int)t.quality_flags.n);
  char buf[4096];
  serializeTelemetry(t, buf, sizeof buf, ContractProfile::V1_1);
  JsonDocument d;
  deserializeJson(d, buf);
  for (JsonVariant f : d["quality_flags"].as<JsonArray>()) TEST_ASSERT_TRUE(strlen(f.as<const char*>()) <= 64);
}

void test_telemetry_fits_small_buffer_or_fails_cleanly() {
  char tiny[64];
  TEST_ASSERT_EQUAL(0, (int)serializeTelemetry(sample(true), tiny, sizeof tiny, ContractProfile::V1_1));
}

// Writes real serializer output for tools/validate_contract_samples.py, which validates it
// against the backend's own Pydantic models (src/fermentai/api/schemas.py).
#ifndef FERMENTAI_SAMPLES_DIR
#define FERMENTAI_SAMPLES_DIR "build_logs/contract_samples"
#endif
#include <sys/stat.h>
#include "command_schema.h"
static void writeSample(const char* name, const char* body) {
  mkdir(FERMENTAI_SAMPLES_DIR, 0755);
  std::string path = std::string(FERMENTAI_SAMPLES_DIR) + "/" + name;
  FILE* f = fopen(path.c_str(), "wb");
  TEST_ASSERT_NOT_NULL_MESSAGE(f, path.c_str());
  fputs(body, f);
  fclose(f);
}

void test_emit_contract_samples() {
  char buf[4096];
  TelemetryRecord t = sample(true);
  t.ph_method = "probe_sample";
  t.obs.ph = Reading::ok(4.31f, 0, Provenance::Manual);
  t.fault_bits = kFaultHeatsinkOvertemp;
  t.peltier_mode = PeltierMode::Fault;
  TEST_ASSERT_TRUE(serializeTelemetry(t, buf, sizeof buf, ContractProfile::V1_0) > 0);
  writeSample("telemetry_v1_0.json", buf);
  TEST_ASSERT_TRUE(serializeTelemetry(t, buf, sizeof buf, ContractProfile::V1_1) > 0);
  writeSample("telemetry_v1_1.json", buf);
  TelemetryRecord u = sample(false);
  u.simulated = false;
  u.peltier_mode = PeltierMode::Switching;
  u.obs.chamber_temp = Reading::bad(SensorHealth::Stale);
  u.obs.setHealth("bme680", SensorHealth::Stale);
  u.obs.flags.add("bme680_read_failed");
  u.fault_bits = kFaultSensorStale;
  TEST_ASSERT_TRUE(serializeTelemetry(u, buf, sizeof buf, ContractProfile::V1_1) > 0);
  writeSample("telemetry_v1_1_measured_unsynced.json", buf);
  TEST_ASSERT_TRUE(serializeTelemetry(u, buf, sizeof buf, ContractProfile::V1_0) > 0);
  writeSample("telemetry_v1_0_real_unsynced.json", buf);
  TelemetryRecord e;  // nothing known at all
  TEST_ASSERT_TRUE(serializeTelemetry(e, buf, sizeof buf, ContractProfile::V1_1) > 0);
  writeSample("telemetry_v1_1_empty.json", buf);

  AckRecord a;
  strcpy(a.command_id, "6f1c2a9e-0d3b-4c1e-9a57-2b8d4e6f7a10");
  a.sequence_number = 42;
  a.has_applied_setpoint = true;
  a.applied_setpoint_c = 35.0f;
  strcpy(a.message, "clamped locally from 40.00 to 35.00 C");
  a.uptime_ms = 120000;
  parseIso8601Utc("2026-10-31T12:00:02Z", a.epoch_ms);
  TEST_ASSERT_TRUE(serializeAck(a, "A", "fermentai-esp32-a", ContractProfile::V1_0, buf, sizeof buf) > 0);
  writeSample("ack_v1_0.json", buf);
  TEST_ASSERT_TRUE(serializeAck(a, "A", "fermentai-esp32-a", ContractProfile::V1_1, buf, sizeof buf) > 0);
  writeSample("ack_v1_1_setpoint.json", buf);
  AckRecord m;
  strcpy(m.command_id, "b2c4d6e8-1a3b-4c5d-8e9f-0a1b2c3d4e5f");
  m.status = AckStatus::Completed;
  m.ran_ms = 3000;
  strcpy(m.message, "ran 3.0 s");
  m.uptime_ms = 123456;
  TEST_ASSERT_TRUE(serializeAck(m, "A", "fermentai-esp32-a", ContractProfile::V1_1, buf, sizeof buf) > 0);
  writeSample("ack_v1_1_motor_completed.json", buf);
}

// ------------------------------------------------------------------ util
void test_iso8601_roundtrip() {
  int64_t ms;
  TEST_ASSERT_TRUE(parseIso8601Utc("2026-10-31T12:00:00Z", ms));
  TEST_ASSERT_EQUAL_INT64(1793448000000LL, ms);
  char b[24];
  formatIso8601Utc(ms, b, sizeof b);
  TEST_ASSERT_EQUAL_STRING("2026-10-31T12:00:00Z", b);
  TEST_ASSERT_TRUE(parseIso8601Utc("2026-10-31T12:00:00.250+00:00", ms));
  TEST_ASSERT_EQUAL_INT64(1793448000250LL, ms);
  TEST_ASSERT_TRUE(parseIso8601Utc("2024-02-29T23:59:59", ms));
  formatIso8601Utc(ms, b, sizeof b);
  TEST_ASSERT_EQUAL_STRING("2024-02-29T23:59:59Z", b);
  TEST_ASSERT_FALSE(parseIso8601Utc("2026-10-31T12:00:00+02:00", ms));  // non-UTC not guessed
  TEST_ASSERT_FALSE(parseIso8601Utc("garbage", ms));
  TEST_ASSERT_FALSE(parseIso8601Utc(nullptr, ms));
}

void test_clock_sync_quality() {
  ClockSync c;
  TEST_ASSERT_EQUAL_INT64(-1, c.nowEpochMs(5000));
  TEST_ASSERT_TRUE(c.quality(5000, 3600000) == TimestampQuality::Unsynced);
  c.syncFrom(1793448000000LL, 5000);
  TEST_ASSERT_EQUAL_INT64(1793448001000LL, c.nowEpochMs(6000));
  TEST_ASSERT_TRUE(c.quality(6000, 3600000) == TimestampQuality::Synced);
  TEST_ASSERT_TRUE(c.quality(5000 + 3600001, 3600000) == TimestampQuality::Estimated);
}

static int g_runs[3];
static void taskFn(uint32_t, void* ctx) { ++g_runs[(intptr_t)ctx]; }

void test_scheduler_rates_nonblocking() {
  Scheduler s;
  g_runs[0] = g_runs[1] = g_runs[2] = 0;
  s.add("safety", 200, taskFn, (void*)0);
  s.add("control", 1000, taskFn, (void*)1);
  s.add("telemetry", 10000, taskFn, (void*)2, 3000);
  for (uint32_t t = 0; t < 20000; t += 10) s.tick(t);
  TEST_ASSERT_EQUAL(100, g_runs[0]);
  TEST_ASSERT_EQUAL(20, g_runs[1]);
  TEST_ASSERT_EQUAL(2, g_runs[2]);
  // A long stall does not cause a burst of catch-up runs.
  s.tick(60000);
  s.tick(60010);
  TEST_ASSERT_EQUAL(101, g_runs[0]);
}

void test_backoff_sequence() {
  Backoff b(1000, 30000);
  TEST_ASSERT_TRUE(b.ready(0));
  uint32_t expect[] = {1000, 2000, 4000, 8000, 16000, 30000, 30000};
  for (uint32_t e : expect) {
    b.failure(0);
    TEST_ASSERT_EQUAL_UINT32(e, b.delayMs());
  }
  TEST_ASSERT_FALSE(b.ready(29999));
  TEST_ASSERT_TRUE(b.ready(30000));
  b.success();
  TEST_ASSERT_TRUE(b.ready(0));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_v10_keys_equal_fixture);
  RUN_TEST(test_v10_enum_values);
  RUN_TEST(test_v11_superset_and_values);
  RUN_TEST(test_v11_keys_within_published_schema_if_present);
  RUN_TEST(test_missing_values_are_null_never_nan);
  RUN_TEST(test_ph_method_only_with_valid_ph_and_faults_listed);
  RUN_TEST(test_quality_flags_bounded_and_short);
  RUN_TEST(test_telemetry_fits_small_buffer_or_fails_cleanly);
  RUN_TEST(test_emit_contract_samples);
  RUN_TEST(test_iso8601_roundtrip);
  RUN_TEST(test_clock_sync_quality);
  RUN_TEST(test_scheduler_rates_nonblocking);
  RUN_TEST(test_backoff_sequence);
  return UNITY_END();
}
