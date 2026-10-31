// Control-logic tests: hysteresis, heat<->cool dead time, PWM limiting / ramp, channel
// exclusivity, FAULT on sensor loss / heatsink over-temp / over-temp latch, boot behaviour.
#include <unity.h>

#include "app/ChamberStateMachine.h"
#include "control/PeltierDriver.h"
#include "control/SafetyManager.h"
#include "control/TemperatureController.h"
#include "device_config.h"
#include "simulation/MockActuators.h"
#include "simulation/MockSensors.h"

using namespace fa;

void setUp() {}
void tearDown() {}

static RomAddr rom(uint8_t last) {
  RomAddr r{{0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05, last}};
  return r;
}

// ------------------------------------------------------------------ TemperatureController
static TemperatureController::Config ctlCfg(TemperatureController::Mode m) {
  TemperatureController::Config c;
  c.mode = m;
  c.hysteresis_c = 0.5f;
  c.fixed_fraction = 0.6f;
  c.kp_per_c = 0.25f;
  c.min_fraction = 0.15f;
  c.max_fraction = 0.8f;
  return c;
}

void test_hysteresis_band() {
  TemperatureController c(ctlCfg(TemperatureController::Mode::Hysteresis));
  TEST_ASSERT_TRUE(c.compute(24.7f, 25.0f).dir == Direction::None);  // inside band
  TEST_ASSERT_TRUE(c.compute(24.4f, 25.0f).dir == Direction::Heat);  // below sp - h
  TEST_ASSERT_TRUE(c.compute(24.8f, 25.0f).dir == Direction::Heat);  // keeps heating until sp
  TEST_ASSERT_TRUE(c.compute(25.0f, 25.0f).dir == Direction::None);  // reached sp
  TEST_ASSERT_TRUE(c.compute(25.3f, 25.0f).dir == Direction::None);  // inside band
  TEST_ASSERT_TRUE(c.compute(25.6f, 25.0f).dir == Direction::Cool);
  TEST_ASSERT_TRUE(c.compute(25.1f, 25.0f).dir == Direction::Cool);
  TEST_ASSERT_TRUE(c.compute(24.99f, 25.0f).dir == Direction::None);
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.0f, c.compute(25.2f, 25.0f).fraction);
}

void test_hysteresis_minimum_enforced() {
  auto cfg = ctlCfg(TemperatureController::Mode::Hysteresis);
  cfg.hysteresis_c = 0.05f;  // below contract minimum
  TemperatureController c(cfg);
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.3f, c.config().hysteresis_c);
}

void test_proportional_fraction_limited() {
  TemperatureController c(ctlCfg(TemperatureController::Mode::Proportional));
  ControlDemand d = c.compute(10.0f, 30.0f);  // huge error
  TEST_ASSERT_TRUE(d.dir == Direction::Heat);
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.8f, d.fraction);
  c.reset();
  d = c.compute(24.4f, 25.0f);  // small error -> min fraction
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.15f, d.fraction);
  c.reset();
  TEST_ASSERT_TRUE(c.compute(NAN, 25.0f).dir == Direction::None);
}

// ------------------------------------------------------------------ PeltierDriver
struct Rig {
  MockPwmOut a, b;
  MockDigitalOut en;
  PeltierConfig cfg;
  PeltierDriver drv;
  Rig() : cfg(makeCfg()), drv(&a, &b, &en, cfg) {}
  static PeltierConfig makeCfg() {
    PeltierConfig c;
    c.max_fraction = 0.8f;
    c.ramp_per_s = 0.1f;
    c.dead_time_ms = 60000;
    c.heat_on_channel_a = true;
    return c;
  }
  void checkExclusive() {
    TEST_ASSERT_FALSE_MESSAGE(a.fraction() > 0 && b.fraction() > 0, "both BTS7960 sides active");
  }
};

void test_peltier_boot_off() {
  Rig r;
  r.drv.setArmed(true);
  r.drv.begin(0);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.a.fraction());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
  TEST_ASSERT_FALSE(r.en.state());
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Idle);
}

void test_peltier_ramp_and_pwm_limit() {
  Rig r;
  r.drv.setArmed(true);
  r.drv.begin(0);
  r.drv.request(Direction::Heat, 1.0f, 0);  // above limit
  uint32_t t = 0;
  r.drv.update(t);
  for (int i = 0; i < 200; ++i) {
    t += 100;
    r.drv.update(t);
    r.checkExclusive();
    TEST_ASSERT_TRUE(r.a.fraction() <= 0.8f + 1e-6f);
  }
  // after 1 s the ramp allows at most 0.1
  Rig r2;
  r2.drv.setArmed(true);
  r2.drv.begin(0);
  r2.drv.request(Direction::Heat, 0.8f, 0);
  r2.drv.update(0);
  r2.drv.update(1000);
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.1f, r2.a.fraction());
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.8f, r.a.fraction());
  TEST_ASSERT_TRUE(r.en.state());
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Heating);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
}

void test_peltier_heat_to_cool_dead_time() {
  Rig r;
  r.drv.setArmed(true);
  r.drv.begin(0);
  r.drv.request(Direction::Heat, 0.5f, 0);
  for (uint32_t t = 0; t <= 10000; t += 200) r.drv.update(t);
  TEST_ASSERT_TRUE(r.a.fraction() > 0.4f);
  // Request reversal at t=10 s.
  r.drv.request(Direction::Cool, 0.5f, 10000);
  r.drv.update(10200);
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Switching);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.a.fraction());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
  TEST_ASSERT_FALSE(r.en.state());
  // Throughout the dead time both outputs stay at zero.
  uint32_t t = 10200;
  for (; t < 10200 + 59000; t += 500) {
    r.drv.update(t);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.a.fraction());
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
    TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Switching);
  }
  // After >= 60 s of zero output the new direction is energised (ramping) on channel B only.
  for (; t < 10200 + 75000; t += 500) {
    r.drv.update(t);
    r.checkExclusive();
  }
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Cooling);
  TEST_ASSERT_TRUE(r.b.fraction() > 0.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.a.fraction());
  TEST_ASSERT_TRUE(r.a.max_seen > 0 && r.b.max_seen > 0);
}

void test_peltier_same_direction_no_dead_time() {
  Rig r;
  r.drv.setArmed(true);
  r.drv.begin(0);
  r.drv.request(Direction::Heat, 0.5f, 0);
  for (uint32_t t = 0; t <= 6000; t += 200) r.drv.update(t);
  r.drv.request(Direction::None, 0, 6000);
  r.drv.update(6200);
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Idle);
  r.drv.request(Direction::Heat, 0.5f, 7000);
  r.drv.update(7200);
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Heating);
}

void test_peltier_disarmed_outputs_zero() {
  Rig r;
  r.drv.begin(0);  // not armed
  r.drv.request(Direction::Heat, 0.8f, 0);
  for (uint32_t t = 0; t <= 20000; t += 200) r.drv.update(t);
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Heating);  // logic runs
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.a.max_seen);              // but nothing energised
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.max_seen);
  TEST_ASSERT_EQUAL_UINT32(0, r.en.rising_edges);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.drv.appliedFraction());
}

void test_peltier_fault_immediate_off() {
  Rig r;
  r.drv.setArmed(true);
  r.drv.begin(0);
  r.drv.request(Direction::Cool, 0.5f, 0);
  for (uint32_t t = 0; t <= 10000; t += 200) r.drv.update(t);
  TEST_ASSERT_TRUE(r.b.fraction() > 0);
  r.drv.forceFault(10100);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
  TEST_ASSERT_FALSE(r.en.state());
  r.drv.update(10300);  // still wants cool, but fault holds it off
  TEST_ASSERT_TRUE(r.drv.mode() == PeltierMode::Fault);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.b.fraction());
}

// ------------------------------------------------------------------ SafetyManager
static Reading rd(float v, uint32_t t) { return Reading::ok(v, t, Provenance::Measured); }

void test_safety_sensor_stale_after_30s() {
  SafetyManager s(SafetyManager::defaults());
  s.begin(0);
  SafetyInputs in;
  in.heatsink_probes_required = false;
  in.chamber = rd(24.0f, 0);
  s.update(in, 1000);
  TEST_ASSERT_FALSE(s.tripped());
  in.chamber = Reading::bad(SensorHealth::Missing);
  s.update(in, 20000);
  TEST_ASSERT_FALSE(s.tripped());
  s.update(in, 31000);
  TEST_ASSERT_TRUE(s.faults() & kFaultSensorStale);
  in.chamber = rd(24.0f, 31500);
  s.update(in, 31500);
  TEST_ASSERT_FALSE(s.faults() & kFaultSensorStale);
}

void test_safety_implausible_counts_as_invalid() {
  SafetyManager s(SafetyManager::defaults());
  s.begin(0);
  SafetyInputs in;
  in.heatsink_probes_required = false;
  in.chamber = rd(150.0f, 0);  // implausible
  s.update(in, 31000);
  TEST_ASSERT_TRUE(s.faults() & kFaultSensorStale);
  TEST_ASSERT_FALSE(s.faults() & kFaultOvertemp);
}

void test_safety_overtemp_latch_60s() {
  SafetyManager s(SafetyManager::defaults());
  s.begin(0);
  SafetyInputs in;
  in.heatsink_probes_required = false;
  in.chamber = rd(38.5f, 0);
  s.update(in, 1000);
  TEST_ASSERT_TRUE(s.faults() & kFaultOvertemp);
  in.chamber = rd(30.0f, 2000);
  s.update(in, 2000);
  s.update(in, 61000);
  TEST_ASSERT_TRUE(s.faults() & kFaultOvertemp);  // 59 s in range: still latched
  s.update(in, 62500);
  TEST_ASSERT_FALSE(s.faults() & kFaultOvertemp);
  in.chamber = rd(1.5f, 63000);
  s.update(in, 63000);
  TEST_ASSERT_TRUE(s.faults() & kFaultUndertemp);
}

void test_safety_heatsink_overtemp_and_missing() {
  SafetyManager s(SafetyManager::defaults());
  s.begin(0);
  SafetyInputs in;
  in.chamber = rd(24.0f, 0);
  in.int_heatsink = rd(30.0f, 0);
  in.ext_heatsink = rd(61.0f, 0);
  s.update(in, 1000);
  TEST_ASSERT_TRUE(s.faults() & kFaultHeatsinkOvertemp);
  in.ext_heatsink = rd(55.0f, 0);  // below max but above clear threshold -> stays latched
  s.update(in, 100000);
  TEST_ASSERT_TRUE(s.faults() & kFaultHeatsinkOvertemp);
  in.ext_heatsink = rd(45.0f, 0);
  s.update(in, 101000);
  s.update(in, 162000);
  TEST_ASSERT_FALSE(s.faults() & kFaultHeatsinkOvertemp);
  in.int_heatsink = Reading::bad(SensorHealth::Missing);
  s.update(in, 163000);
  TEST_ASSERT_TRUE(s.faults() & kFaultHeatsinkSensor);
}

// ------------------------------------------------------------------ full chamber integration
struct Chamber {
  MockKeyValueStore kv;
  MockEnvSensor env{"bme680"};
  MockOneWireBus bus;
  MockAnalogIn adc;
  MockDigitalIn door_in;
  MockPwmOut pa, pb;
  MockDigitalOut en, gate;
  MockOneShotTimer timer{&gate};
  DS18B20Manager ds{&bus, &kv, 15000, 30000, Provenance::Simulated};
  PHSensor ph{&adc, &kv, PHSensor::Config(), Provenance::Simulated};
  DoorSensor door{&door_in, true, 60};
  SensorManager sensors{&env, &ds, &ph, &door, SensorManager::Config()};
  SafetyManager safety{SafetyManager::defaults()};
  TemperatureController ctl{TemperatureController::Config()};
  PeltierDriver peltier{&pa, &pb, &en, Rig::makeCfg()};
  MotorController motor{&gate, &timer, 10000};
  ClockSync clock;
  struct Sink : IAckSink {
    AckRecord last;
    int n = 0;
    bool pushAck(const AckRecord& a) override {
      last = a;
      ++n;
      return true;
    }
  } sink;
  ChamberStateMachine::Config cfg;
  ChamberStateMachine* csm;
  ThermalTestPlant plant{&pa, &pb, &env, &bus};
  uint32_t t = 0;

  Chamber() {
    bus.addProbe(rom(1), 21.0f);
    bus.addProbe(rom(2), 21.0f);
    bus.addProbe(rom(3), 21.0f);
    env.raw.temperature_c = 21.0f;
    env.raw.humidity_pct = 70.0f;
    cfg.chamber_id = "A";
    csm = new ChamberStateMachine(cfg, sensors, safety, ctl, peltier, &motor, &kv, &sink, clock);
    sensors.begin(0);
    ds.assignRole(rom(1), ProbeRole::Starter);
    ds.assignRole(rom(2), ProbeRole::InternalHeatsink);
    ds.assignRole(rom(3), ProbeRole::ExternalHeatsink);
    peltier.setArmed(true);
    motor.setArmed(true);
    csm->begin(0);
  }
  ~Chamber() { delete csm; }
  void run(uint32_t ms) {
    const uint32_t end = t + ms;
    for (; t < end; t += 20) {
      if (t % 200 == 0) {
        plant.step(0.2f);
        sensors.updateEnv(t);
        sensors.updateDs(t);
        sensors.updateDoor(t);
        csm->safetyTick(t);
      }
      if (t % 1000 == 0) csm->controlTick(t);
      csm->motorTick(t);
      TEST_ASSERT_FALSE_MESSAGE(pa.fraction() > 0 && pb.fraction() > 0, "both sides on");
    }
  }
  void sendSetpoint(const char* id, float sp, int seq) {
    char json[256];
    snprintf(json, sizeof json,
             "{\"chamber_id\":\"A\",\"command_id\":\"%s\",\"sequence_number\":%d,\"ttl_s\":90,"
             "\"temperature_setpoint_c\":%.2f,\"server_epoch\":\"e1\"}",
             id, seq, sp);
    DesiredControlParse p;
    TEST_ASSERT_TRUE(parseDesiredControl(json, strlen(json), t, p));
    csm->handleDesiredControl(p, t);
  }
};

void test_chamber_no_control_before_command() {
  Chamber c;
  c.run(10000);
  TEST_ASSERT_TRUE(c.csm->runState() == RunState::WaitingForCommand);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.max_seen);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pb.max_seen);
}

void test_chamber_heats_toward_setpoint() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 27.0f, 1);
  c.run(20000);
  TEST_ASSERT_TRUE(c.csm->runState() == RunState::Active);
  TEST_ASSERT_TRUE(c.peltier.mode() == PeltierMode::Heating);
  TEST_ASSERT_TRUE(c.pa.fraction() > 0.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pb.fraction());
}

void test_chamber_fault_on_sensor_loss() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 30.0f, 1);
  c.run(15000);
  TEST_ASSERT_TRUE(c.pa.fraction() > 0.0f);
  c.env.fail_fetch = true;  // BME680 stops answering
  c.run(20000);             // env stale_ms (20 s) not yet + safety 30 s not yet
  c.run(35000);
  TEST_ASSERT_TRUE(c.safety.faults() & kFaultSensorStale);
  TEST_ASSERT_TRUE(c.peltier.mode() == PeltierMode::Fault);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.fraction());
  TEST_ASSERT_FALSE(c.en.state());
  TelemetryRecord r;
  c.csm->fillTelemetry(r, c.t);
  TEST_ASSERT_TRUE(r.peltier_mode == PeltierMode::Fault);
  TEST_ASSERT_FALSE(r.obs.chamber_temp.valid);
  TEST_ASSERT_TRUE(r.obs.flags.has("bme680_read_failed"));
}

void test_chamber_fault_on_heatsink_overtemp() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 30.0f, 1);
  c.run(15000);
  TEST_ASSERT_TRUE(c.pa.fraction() > 0.0f);
  c.plant.int_hs_c = 70.0f;
  c.bus.probe(1).temp_c = 70.0f;
  // keep the plant from overwriting the probe for this test
  ThermalTestPlant frozen(nullptr, nullptr, nullptr, nullptr);
  c.plant = frozen;
  c.run(4000);
  TEST_ASSERT_TRUE(c.safety.faults() & kFaultHeatsinkOvertemp);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.fraction());
  TEST_ASSERT_TRUE(c.peltier.mode() == PeltierMode::Fault);
}

void test_chamber_fault_on_heatsink_probe_disconnect() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 30.0f, 1);
  c.run(15000);
  c.bus.probe(2).connected = false;
  c.run(40000);
  TEST_ASSERT_TRUE(c.safety.faults() & kFaultHeatsinkSensor);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.fraction());
}

void test_chamber_door_open_idles_peltier() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 30.0f, 1);
  c.run(15000);
  TEST_ASSERT_TRUE(c.pa.fraction() > 0.0f);
  c.door_in.level = true;  // open (open_level_high)
  c.run(1000);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.fraction());
  TEST_ASSERT_TRUE(c.peltier.mode() == PeltierMode::Idle);
  TEST_ASSERT_FALSE(c.safety.tripped());
}

void test_chamber_offline_hold_goes_idle() {
  Chamber c;
  c.run(5000);
  c.sendSetpoint("c1", 30.0f, 1);
  c.run(10000);
  TEST_ASSERT_TRUE(c.csm->runState() == RunState::Active);
  c.run(limits::kOfflineHoldMs);
  TEST_ASSERT_TRUE(c.csm->runState() == RunState::OfflineHold);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.pa.fraction());
  c.sendSetpoint("c2", 30.0f, 2);
  c.run(2000);
  TEST_ASSERT_TRUE(c.csm->runState() == RunState::Active);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_hysteresis_band);
  RUN_TEST(test_hysteresis_minimum_enforced);
  RUN_TEST(test_proportional_fraction_limited);
  RUN_TEST(test_peltier_boot_off);
  RUN_TEST(test_peltier_ramp_and_pwm_limit);
  RUN_TEST(test_peltier_heat_to_cool_dead_time);
  RUN_TEST(test_peltier_same_direction_no_dead_time);
  RUN_TEST(test_peltier_disarmed_outputs_zero);
  RUN_TEST(test_peltier_fault_immediate_off);
  RUN_TEST(test_safety_sensor_stale_after_30s);
  RUN_TEST(test_safety_implausible_counts_as_invalid);
  RUN_TEST(test_safety_overtemp_latch_60s);
  RUN_TEST(test_safety_heatsink_overtemp_and_missing);
  RUN_TEST(test_chamber_no_control_before_command);
  RUN_TEST(test_chamber_heats_toward_setpoint);
  RUN_TEST(test_chamber_fault_on_sensor_loss);
  RUN_TEST(test_chamber_fault_on_heatsink_overtemp);
  RUN_TEST(test_chamber_fault_on_heatsink_probe_disconnect);
  RUN_TEST(test_chamber_door_open_idles_peltier);
  RUN_TEST(test_chamber_offline_hold_goes_idle);
  return UNITY_END();
}
