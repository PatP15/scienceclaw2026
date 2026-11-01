// Sensor tests: DS18B20 ROM->role mapping (multiple ROMs, persistence, disconnect, bad values),
// missing / uncalibrated / calibrated pH, invalid BME680 readings -> flags, door debounce.
#include <unity.h>

#include "sensors/DS18B20Manager.h"
#include "sensors/DoorSensor.h"
#include "sensors/PHSensor.h"
#include "sensors/SensorManager.h"
#include "simulation/MockActuators.h"
#include "simulation/MockSensors.h"

using namespace fa;

void setUp() {}
void tearDown() {}

static RomAddr rom(uint8_t last) {
  RomAddr r{{0x28, 0xFF, 0x64, 0x1E, 0x0F, 0x12, 0x34, last}};
  return r;
}

// Drive a DS manager through one complete conversion cycle.
static void cycle(DS18B20Manager& m, uint32_t& t) {
  for (int i = 0; i < 20; ++i) {
    t += 250;
    m.update(t);
  }
}

// ------------------------------------------------------------------ DS18B20
void test_ds_rom_parse_format_roundtrip() {
  RomAddr r;
  TEST_ASSERT_TRUE(DS18B20Manager::parseRom("28:ff:64:1e:0f:12:34:56", r));
  char buf[17];
  DS18B20Manager::formatRom(r, buf, sizeof buf);
  TEST_ASSERT_EQUAL_STRING("28ff641e0f123456", buf);
  TEST_ASSERT_FALSE(DS18B20Manager::parseRom("28ff641e0f1234", r));      // too short
  TEST_ASSERT_FALSE(DS18B20Manager::parseRom("28ff641e0f12345678", r));  // too long
  TEST_ASSERT_FALSE(DS18B20Manager::parseRom("zz", r));
  TEST_ASSERT_FALSE(DS18B20Manager::parseRom("0000000000000000", r));
}

void test_ds_roles_by_address_not_order() {
  MockKeyValueStore kv;
  MockOneWireBus bus;
  // Bus order deliberately differs from role order.
  bus.addProbe(rom(0x30), 45.0f);  // external heatsink
  bus.addProbe(rom(0x10), 23.5f);  // starter
  bus.addProbe(rom(0x20), 28.0f);  // internal heatsink
  DS18B20Manager m(&bus, &kv, 15000, 30000, Provenance::Measured);
  uint32_t t = 0;
  m.begin(t);
  TEST_ASSERT_EQUAL_UINT32(3, m.discoveredCount());
  TEST_ASSERT_EQUAL_UINT32(3, m.unassignedCount());
  cycle(m, t);
  // Nothing is published before assignment, even though probes are present.
  TEST_ASSERT_FALSE(m.reading(ProbeRole::Starter, t).valid);
  TEST_ASSERT_TRUE(m.health(ProbeRole::Starter, t) == SensorHealth::Missing);

  TEST_ASSERT_TRUE(m.assignRole(rom(0x10), ProbeRole::Starter));
  TEST_ASSERT_TRUE(m.assignRole(rom(0x20), ProbeRole::InternalHeatsink));
  TEST_ASSERT_TRUE(m.assignRole(rom(0x30), ProbeRole::ExternalHeatsink));
  TEST_ASSERT_EQUAL_UINT32(0, m.unassignedCount());
  cycle(m, t);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 23.5f, m.reading(ProbeRole::Starter, t).value);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 28.0f, m.reading(ProbeRole::InternalHeatsink, t).value);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 45.0f, m.reading(ProbeRole::ExternalHeatsink, t).value);
}

void test_ds_mapping_persists_across_reboot() {
  MockKeyValueStore kv;
  MockOneWireBus bus;
  bus.addProbe(rom(0x10), 23.5f);
  bus.addProbe(rom(0x20), 28.0f);
  {
    DS18B20Manager m(&bus, &kv, 15000, 30000, Provenance::Measured);
    m.begin(0);
    m.assignRole(rom(0x20), ProbeRole::Starter);
  }
  // "Reboot": new manager, same NVS, probes now enumerate in another order.
  MockOneWireBus bus2;
  bus2.addProbe(rom(0x20), 24.25f);
  bus2.addProbe(rom(0x10), 30.0f);
  DS18B20Manager m2(&bus2, &kv, 15000, 30000, Provenance::Measured);
  uint32_t t = 0;
  m2.begin(t);
  cycle(m2, t);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 24.25f, m2.reading(ProbeRole::Starter, t).value);
  RomAddr r;
  TEST_ASSERT_TRUE(m2.roleRom(ProbeRole::Starter, r));
  TEST_ASSERT_TRUE(r == rom(0x20));
}

void test_ds_reassign_moves_rom_between_roles() {
  MockKeyValueStore kv;
  MockOneWireBus bus;
  bus.addProbe(rom(0x10), 23.5f);
  DS18B20Manager m(&bus, &kv, 15000, 30000, Provenance::Measured);
  m.begin(0);
  m.assignRole(rom(0x10), ProbeRole::Starter);
  m.assignRole(rom(0x10), ProbeRole::ExternalHeatsink);  // one ROM = one role
  RomAddr r;
  TEST_ASSERT_FALSE(m.roleRom(ProbeRole::Starter, r));
  TEST_ASSERT_TRUE(m.roleRom(ProbeRole::ExternalHeatsink, r));
  TEST_ASSERT_TRUE(m.clearRole(ProbeRole::ExternalHeatsink));
  TEST_ASSERT_FALSE(m.roleRom(ProbeRole::ExternalHeatsink, r));
}

void test_ds_disconnect_goes_missing_after_stale() {
  MockKeyValueStore kv;
  MockOneWireBus bus;
  bus.addProbe(rom(0x10), 23.5f);
  bus.addProbe(rom(0x20), 28.0f);
  DS18B20Manager m(&bus, &kv, 15000, 30000, Provenance::Measured);
  uint32_t t = 0;
  m.begin(t);
  m.assignRole(rom(0x10), ProbeRole::Starter);
  m.assignRole(rom(0x20), ProbeRole::InternalHeatsink);
  cycle(m, t);
  TEST_ASSERT_TRUE(m.reading(ProbeRole::Starter, t).valid);
  bus.probe(0).connected = false;  // unplug starter probe
  cycle(m, t);
  for (int i = 0; i < 4; ++i) cycle(m, t);  // > 15 s without a valid read
  TEST_ASSERT_FALSE(m.reading(ProbeRole::Starter, t).valid);
  TEST_ASSERT_TRUE(m.health(ProbeRole::Starter, t) == SensorHealth::Missing);
  // The other probe on the same bus is unaffected.
  TEST_ASSERT_TRUE(m.reading(ProbeRole::InternalHeatsink, t).valid);
  // Reconnect -> recovers.
  bus.probe(0).connected = true;
  cycle(m, t);
  TEST_ASSERT_TRUE(m.reading(ProbeRole::Starter, t).valid);
}

void test_ds_rejects_85_and_minus127_and_crc() {
  MockKeyValueStore kv;
  MockOneWireBus bus;
  bus.addProbe(rom(0x10), 85.0f);  // power-on-reset value
  DS18B20Manager m(&bus, &kv, 15000, 30000, Provenance::Measured);
  uint32_t t = 0;
  m.begin(t);
  m.assignRole(rom(0x10), ProbeRole::Starter);
  cycle(m, t);
  TEST_ASSERT_FALSE(m.reading(ProbeRole::Starter, t).valid);
  TEST_ASSERT_TRUE(m.health(ProbeRole::Starter, t) == SensorHealth::Invalid);
  bus.probe(0).temp_c = -127.0f;
  cycle(m, t);
  TEST_ASSERT_FALSE(m.reading(ProbeRole::Starter, t).valid);
  bus.probe(0).temp_c = 22.0f;
  bus.probe(0).crc_error = true;
  cycle(m, t);
  TEST_ASSERT_FALSE(m.reading(ProbeRole::Starter, t).valid);
  TEST_ASSERT_TRUE(m.crcErrors() > 0);
  bus.probe(0).crc_error = false;
  cycle(m, t);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 22.0f, m.reading(ProbeRole::Starter, t).value);
}

// ------------------------------------------------------------------ pH
void test_ph_disabled_is_null_and_not_reported() {
  MockKeyValueStore kv;
  MockAnalogIn adc;
  PHSensor::Config c;
  c.enabled = false;
  PHSensor ph(&adc, &kv, c, Provenance::Measured);
  ph.begin();
  ph.update(1000);
  TEST_ASSERT_FALSE(ph.ph(1000).valid);
  TEST_ASSERT_TRUE(ph.health(1000) == SensorHealth::Disabled);
  SensorManager sm(nullptr, nullptr, &ph, nullptr, SensorManager::Config());
  sm.begin(0);
  ChamberObservation o = sm.observe(1000);
  TEST_ASSERT_FALSE(o.ph.valid);
  for (size_t i = 0; i < o.n_health; ++i) TEST_ASSERT_TRUE(strcmp(o.health[i].name, "ph") != 0);
  TEST_ASSERT_FALSE(o.flags.has("ph_uncalibrated"));
}

void test_ph_enabled_missing_adc() {
  MockKeyValueStore kv;
  MockAnalogIn adc;
  adc.fail = true;
  PHSensor::Config c;
  c.enabled = true;
  PHSensor ph(&adc, &kv, c, Provenance::Measured);
  ph.begin();
  ph.update(1000);
  TEST_ASSERT_TRUE(ph.health(1000) == SensorHealth::Missing);
  TEST_ASSERT_FALSE(ph.ph(1000).valid);
  TEST_ASSERT_EQUAL_INT32(-1, ph.rawAdc());
}

void test_ph_uncalibrated_is_null_but_voltage_published() {
  MockKeyValueStore kv;
  MockAnalogIn adc;
  adc.millivolts = 1650.0f;
  PHSensor::Config c;
  c.enabled = true;
  PHSensor ph(&adc, &kv, c, Provenance::Measured);
  ph.begin();
  ph.update(1000);
  TEST_ASSERT_TRUE(ph.health(1000) == SensorHealth::Uncalibrated);
  TEST_ASSERT_FALSE(ph.ph(1000).valid);
  TEST_ASSERT_TRUE(ph.voltageMv(1000).valid);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 1650.0f, ph.voltageMv(1000).value);
  TEST_ASSERT_TRUE(ph.rawAdc() > 0);
}

void test_ph_two_point_calibration_and_persistence() {
  MockKeyValueStore kv;
  MockAnalogIn adc;
  PHSensor::Config c;
  c.enabled = true;
  {
    PHSensor ph(&adc, &kv, c, Provenance::Measured);
    ph.begin();
    adc.millivolts = 2030.0f;  // pH 4 buffer
    ph.update(1000);
    TEST_ASSERT_TRUE(ph.calibratePoint(4, "2026-10-31T12:00:00Z"));
    adc.millivolts = 1500.0f;  // pH 7 buffer
    ph.update(2000);
    TEST_ASSERT_TRUE(ph.calibratePoint(7, "2026-10-31T12:05:00Z"));
    TEST_ASSERT_TRUE(ph.calibrated());
    TEST_ASSERT_FALSE(ph.calibratePoint(10, "x"));  // only 4 and 7 supported
  }
  PHSensor ph2(&adc, &kv, c, Provenance::Measured);  // reboot
  ph2.begin();
  TEST_ASSERT_TRUE(ph2.calibrated());
  TEST_ASSERT_EQUAL_STRING("2026-10-31T12:05:00Z", ph2.calibration().date);
  adc.millivolts = 1676.67f;  // one third of the way from pH7 to pH4 -> pH 6
  ph2.update(3000);
  TEST_ASSERT_TRUE(ph2.ph(3000).valid);
  TEST_ASSERT_FLOAT_WITHIN(0.02, 6.0f, ph2.ph(3000).value);
  // Saturated input is invalid (no pH value reported)
  adc.millivolts = 3200.0f;
  ph2.update(4000);
  TEST_ASSERT_FALSE(ph2.ph(4000).valid);
  TEST_ASSERT_TRUE(ph2.health(4000) == SensorHealth::Invalid);
}

void test_ph_tiny_span_calibration_invalid() {
  MockKeyValueStore kv;
  MockAnalogIn adc;
  PHSensor::Config c;
  c.enabled = true;
  PHSensor ph(&adc, &kv, c, Provenance::Measured);
  ph.begin();
  adc.millivolts = 1500.0f;
  ph.update(1000);
  ph.calibratePoint(4, "d");
  adc.millivolts = 1520.0f;
  ph.update(2000);
  ph.calibratePoint(7, "d");
  TEST_ASSERT_FALSE(ph.calibrated());
}

// ------------------------------------------------------------------ BME680 via SensorManager
static void runEnv(SensorManager& sm, uint32_t& t, uint32_t ms) {
  const uint32_t end = t + ms;
  for (; t < end; t += 50) sm.updateEnv(t);
}

void test_bme680_valid_reading() {
  MockEnvSensor env("bme680");
  env.raw = EnvRaw{24.5f, 72.0f, 101325.0f, 105432.0f, true, true};
  SensorManager::Config cfg;
  cfg.prov = Provenance::Measured;
  SensorManager sm(&env, nullptr, nullptr, nullptr, cfg);
  uint32_t t = 0;
  sm.begin(t);
  runEnv(sm, t, 1000);
  ChamberObservation o = sm.observe(t);
  TEST_ASSERT_TRUE(o.chamber_temp.valid);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 24.5f, o.chamber_temp.value);
  TEST_ASSERT_FLOAT_WITHIN(1.0, 105432.0f, o.gas_ohm.value);
  TEST_ASSERT_TRUE(o.flags.has("sensor_warming_up"));
  TEST_ASSERT_FALSE(o.flags.has("bme680_invalid_reading"));
  runEnv(sm, t, 301000);
  o = sm.observe(t);
  TEST_ASSERT_FALSE(o.flags.has("sensor_warming_up"));
}

void test_bme680_invalid_readings_flagged() {
  MockEnvSensor env("bme680");
  env.raw = EnvRaw{150.0f, 0.0f, 5.0f, -1.0f, true, true};  // nonsense
  SensorManager sm(&env, nullptr, nullptr, nullptr, SensorManager::Config());
  uint32_t t = 0;
  sm.begin(t);
  runEnv(sm, t, 1000);
  ChamberObservation o = sm.observe(t);
  TEST_ASSERT_FALSE(o.chamber_temp.valid);
  TEST_ASSERT_FALSE(o.rh.valid);
  TEST_ASSERT_FALSE(o.pressure_pa.valid);
  TEST_ASSERT_FALSE(o.gas_ohm.valid);
  TEST_ASSERT_TRUE(o.flags.has("bme680_invalid_reading"));
  bool found = false;
  for (size_t i = 0; i < o.n_health; ++i)
    if (!strcmp(o.health[i].name, "bme680")) {
      found = true;
      TEST_ASSERT_TRUE(o.health[i].health == SensorHealth::Invalid);
    }
  TEST_ASSERT_TRUE(found);
  // NaN from the driver is also invalid, never propagated.
  env.raw = EnvRaw{NAN, 50.0f, 101000.0f, 1000.0f, true, true};
  runEnv(sm, t, 6000);
  o = sm.observe(t);
  TEST_ASSERT_FALSE(o.chamber_temp.valid);
  TEST_ASSERT_TRUE(isnan(o.chamber_temp.value));
}

void test_bme680_read_failure_and_stale() {
  MockEnvSensor env("bme680");
  env.raw = EnvRaw{24.5f, 72.0f, 101325.0f, 105432.0f, true, true};
  SensorManager sm(&env, nullptr, nullptr, nullptr, SensorManager::Config());
  uint32_t t = 0;
  sm.begin(t);
  runEnv(sm, t, 1000);
  TEST_ASSERT_TRUE(sm.chamberTemp(t).valid);
  env.fail_fetch = true;
  runEnv(sm, t, 6000);
  ChamberObservation o = sm.observe(t);
  TEST_ASSERT_TRUE(o.flags.has("bme680_read_failed"));
  runEnv(sm, t, 20000);
  TEST_ASSERT_FALSE(sm.chamberTemp(t).valid);
  TEST_ASSERT_TRUE(sm.chamberTemp(t).health == SensorHealth::Stale);
}

void test_bme680_absent() {
  MockEnvSensor env("bme680");
  env.absent = true;
  SensorManager sm(&env, nullptr, nullptr, nullptr, SensorManager::Config());
  uint32_t t = 0;
  sm.begin(t);
  runEnv(sm, t, 2000);
  ChamberObservation o = sm.observe(t);
  TEST_ASSERT_TRUE(o.flags.has("bme680_missing"));
  TEST_ASSERT_FALSE(o.chamber_temp.valid);
}

void test_stuck_value_becomes_stale() {
  MockEnvSensor env("bme680");
  env.raw = EnvRaw{24.5f, 72.0f, 101325.0f, 105432.0f, true, true};
  SensorManager::Config cfg;
  cfg.stuck_ms = 60000;
  SensorManager sm(&env, nullptr, nullptr, nullptr, cfg);
  uint32_t t = 0;
  sm.begin(t);
  runEnv(sm, t, 61000);
  TEST_ASSERT_FALSE(sm.chamberTemp(t).valid);
  env.raw.temperature_c = 24.6f;
  runEnv(sm, t, 6000);
  TEST_ASSERT_TRUE(sm.chamberTemp(t).valid);
}

// ------------------------------------------------------------------ door
void test_door_debounce_and_count() {
  MockDigitalIn in;
  in.level = false;  // closed (open_level_high)
  DoorSensor d(&in, true, 60);
  uint32_t t = 0;
  d.begin(t);
  for (; t < 100; t += 20) d.update(t);
  TEST_ASSERT_TRUE(d.known());
  TEST_ASSERT_FALSE(d.isOpen());
  // 40 ms bounce: never accepted
  for (int i = 0; i < 10; ++i) {
    in.level = !in.level;
    t += 20;
    d.update(t);
  }
  in.level = false;
  for (int i = 0; i < 5; ++i) { t += 20; d.update(t); }
  TEST_ASSERT_FALSE(d.isOpen());
  TEST_ASSERT_EQUAL_UINT32(0, d.openCount());
  // Real opening
  in.level = true;
  t += 20;
  d.update(t);
  t += 20;
  d.update(t);
  TEST_ASSERT_FALSE(d.isOpen());  // 20 ms stable: not yet
  for (int i = 0; i < 4; ++i) { t += 20; d.update(t); }
  TEST_ASSERT_TRUE(d.isOpen());
  TEST_ASSERT_EQUAL_UINT32(1, d.openCount());
  bool now_open = false;
  TEST_ASSERT_TRUE(d.takeEdge(now_open));
  TEST_ASSERT_TRUE(now_open);
  TEST_ASSERT_FALSE(d.takeEdge(now_open));
  TEST_ASSERT_TRUE(d.msSinceChange(t) < 100);
  in.level = false;
  for (int i = 0; i < 6; ++i) { t += 20; d.update(t); }
  TEST_ASSERT_FALSE(d.isOpen());
  in.level = true;
  for (int i = 0; i < 6; ++i) { t += 20; d.update(t); }
  TEST_ASSERT_EQUAL_UINT32(2, d.openCount());
}

void test_door_polarity_configurable() {
  MockDigitalIn in;
  in.level = false;
  DoorSensor d(&in, false, 60);  // open when LOW (NC reed or inverted mounting)
  uint32_t t = 0;
  d.begin(t);
  for (; t < 200; t += 20) d.update(t);
  TEST_ASSERT_TRUE(d.isOpen());
  TEST_ASSERT_EQUAL_UINT32(0, d.openCount());  // initial state is not an opening event
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_ds_rom_parse_format_roundtrip);
  RUN_TEST(test_ds_roles_by_address_not_order);
  RUN_TEST(test_ds_mapping_persists_across_reboot);
  RUN_TEST(test_ds_reassign_moves_rom_between_roles);
  RUN_TEST(test_ds_disconnect_goes_missing_after_stale);
  RUN_TEST(test_ds_rejects_85_and_minus127_and_crc);
  RUN_TEST(test_ph_disabled_is_null_and_not_reported);
  RUN_TEST(test_ph_enabled_missing_adc);
  RUN_TEST(test_ph_uncalibrated_is_null_but_voltage_published);
  RUN_TEST(test_ph_two_point_calibration_and_persistence);
  RUN_TEST(test_ph_tiny_span_calibration_invalid);
  RUN_TEST(test_bme680_valid_reading);
  RUN_TEST(test_bme680_invalid_readings_flagged);
  RUN_TEST(test_bme680_read_failure_and_stale);
  RUN_TEST(test_bme680_absent);
  RUN_TEST(test_stuck_value_becomes_stale);
  RUN_TEST(test_door_debounce_and_count);
  RUN_TEST(test_door_polarity_configurable);
  return UNITY_END();
}
