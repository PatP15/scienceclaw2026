// Motor tests: max duration clamp, auto-timeout, no indefinite ON, hardware cut-off timer,
// safe-off on boot, door interlock, disarmed refusal, completion acks.
#include <unity.h>

#include "app/ChamberStateMachine.h"
#include "device_config.h"
#include "control/MotorController.h"
#include "simulation/MockActuators.h"
#include "simulation/MockSensors.h"

using namespace fa;

void setUp() {}
void tearDown() {}

void test_motor_safe_off_on_boot() {
  MockDigitalOut gate;
  gate.write(true);  // garbage state before init
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  TEST_ASSERT_FALSE(gate.state());
  TEST_ASSERT_FALSE(m.isMotorRunning());
  TEST_ASSERT_FALSE(m.armed());  // disarmed by default
}

void test_motor_refused_when_disarmed_or_zero() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  TEST_ASSERT_FALSE(m.startMotor(3000, 0));
  TEST_ASSERT_FALSE(gate.state());
  m.setArmed(true);
  TEST_ASSERT_FALSE(m.startMotor(0, 0));
  TEST_ASSERT_FALSE(gate.state());
}

void test_motor_duration_clamped_to_max() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  uint32_t granted = 0;
  TEST_ASSERT_TRUE(m.startMotor(600000, 0, MotorPattern::Continuous, &granted));  // 10 min asked
  TEST_ASSERT_EQUAL_UINT32(10000, granted);
  TEST_ASSERT_TRUE(timer.armed);
  TEST_ASSERT_TRUE(timer.period_ms <= 10100);
  uint32_t t = 0;
  for (; t < 9980; t += 20) {
    m.update(t);
    TEST_ASSERT_TRUE(m.isMotorRunning());
  }
  for (; t <= 10040; t += 20) m.update(t);
  TEST_ASSERT_FALSE(m.isMotorRunning());
  TEST_ASSERT_FALSE(gate.state());
  MotorController::StopReason why;
  uint32_t ran;
  TEST_ASSERT_TRUE(m.takeFinished(why, ran));
  TEST_ASSERT_TRUE(why == MotorController::StopReason::Completed);
  TEST_ASSERT_EQUAL_UINT32(10000, ran);
  TEST_ASSERT_FALSE(timer.armed);  // cancelled after normal stop
}

void test_motor_auto_timeout_exact() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  TEST_ASSERT_TRUE(m.startMotor(3000, 1000));
  m.update(3999);
  TEST_ASSERT_TRUE(m.isMotorRunning());
  m.update(4000);
  TEST_ASSERT_FALSE(m.isMotorRunning());
  TEST_ASSERT_FALSE(gate.state());
}

void test_motor_hw_timer_cuts_gate_if_loop_stalls() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  TEST_ASSERT_TRUE(m.startMotor(3000, 0));
  TEST_ASSERT_TRUE(gate.state());
  // Main loop "stalls": update() is not called; the independent timer fires.
  timer.fire();
  TEST_ASSERT_FALSE(gate.state());
  // When the loop resumes the controller also finalises the run.
  m.update(30000);
  TEST_ASSERT_FALSE(m.isMotorRunning());
  TEST_ASSERT_FALSE(gate.state());
}

void test_motor_never_on_when_not_running() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  gate.write(true);  // something glitches the pin high
  m.update(10);
  TEST_ASSERT_FALSE(gate.state());
}

void test_motor_no_double_start_and_stop() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  TEST_ASSERT_TRUE(m.startMotor(5000, 0));
  TEST_ASSERT_FALSE(m.startMotor(5000, 100));  // cannot extend by re-starting
  m.stopMotor(1500);
  TEST_ASSERT_FALSE(m.isMotorRunning());
  TEST_ASSERT_FALSE(gate.state());
  MotorController::StopReason why;
  uint32_t ran;
  TEST_ASSERT_TRUE(m.takeFinished(why, ran));
  TEST_ASSERT_TRUE(why == MotorController::StopReason::Stopped);
  TEST_ASSERT_EQUAL_UINT32(1500, ran);
}

void test_motor_wiggle_pattern_pulses_but_bounded() {
  MockDigitalOut gate;
  MockOneShotTimer timer(&gate);
  MotorController m(&gate, &timer, 10000);
  m.begin();
  m.setArmed(true);
  TEST_ASSERT_TRUE(m.startMotor(3000, 0, MotorPattern::Wiggle));
  uint32_t on_ticks = 0, off_ticks = 0;
  for (uint32_t t = 0; t < 5000; t += 20) {
    m.update(t);
    (gate.state() ? on_ticks : off_ticks)++;
  }
  TEST_ASSERT_TRUE(gate.rising_edges > 2);
  TEST_ASSERT_TRUE(on_ticks * 20 <= 3000);
  TEST_ASSERT_FALSE(gate.state());
}

// ------------------------------------------------------------------ via the state machine
struct MotorChamber {
  MockKeyValueStore kv;
  MockEnvSensor env{"bme680"};
  MockDigitalIn door_in;
  MockPwmOut pa, pb;
  MockDigitalOut en, gate;
  MockOneShotTimer timer{&gate};
  DoorSensor door{&door_in, true, 60};
  SensorManager sensors{&env, nullptr, nullptr, &door, SensorManager::Config()};
  SafetyManager safety{SafetyManager::defaults()};
  TemperatureController ctl{TemperatureController::Config()};
  PeltierDriver peltier{&pa, &pb, &en, PeltierConfig()};
  MotorController motor{&gate, &timer, 10000};
  ClockSync clock;
  struct Sink : IAckSink {
    AckRecord acks[16];
    int n = 0;
    bool pushAck(const AckRecord& a) override {
      if (n < 16) acks[n++] = a;
      return true;
    }
  } sink;
  ChamberStateMachine::Config cfg;
  ChamberStateMachine csm{cfg, sensors, safety, ctl, peltier, &motor, &kv, &sink, clock};
  uint32_t t = 0;
  MotorChamber() {
    env.raw = EnvRaw{24.0f, 60.0f, 101000.0f, 1e5f, true, true};
    sensors.begin(0);
    csm.begin(0);
    motor.setArmed(true);
  }
  void run(uint32_t ms) {
    const uint32_t end = t + ms;
    for (; t < end; t += 20) {
      sensors.updateDoor(t);
      if (t % 200 == 0) {
        sensors.updateEnv(t);
        csm.safetyTick(t);
      }
      csm.motorTick(t);
    }
  }
  void send(const char* json) {
    DesiredControlParse p;
    TEST_ASSERT_TRUE(parseDesiredControl(json, strlen(json), t, p));
    csm.handleDesiredControl(p, t);
  }
};

void test_csm_animate_motor_accepted_then_completed() {
  MotorChamber c;
  c.run(200);
  c.send("{\"chamber_id\":\"A\",\"server_time_utc\":\"2026-10-31T12:00:00Z\",\"commands\":[{"
         "\"command_id\":\"m1\",\"chamber_id\":\"A\",\"action\":\"animate_motor\","
         "\"parameters\":{\"duration_ms\":3000,\"pattern\":\"wiggle\"},"
         "\"issued_at_utc\":\"2026-10-31T12:00:00Z\",\"expires_at_utc\":\"2026-10-31T12:01:00Z\","
         "\"sequence_number\":5}]}");
  TEST_ASSERT_TRUE(c.motor.isMotorRunning());
  TEST_ASSERT_EQUAL(1, c.sink.n);
  TEST_ASSERT_TRUE(c.sink.acks[0].status == AckStatus::Accepted);
  c.run(3500);
  TEST_ASSERT_FALSE(c.motor.isMotorRunning());
  TEST_ASSERT_EQUAL(2, c.sink.n);
  TEST_ASSERT_TRUE(c.sink.acks[1].status == AckStatus::Completed);
  TEST_ASSERT_EQUAL_STRING("m1", c.sink.acks[1].command_id);
  TEST_ASSERT_TRUE(c.sink.acks[1].ran_ms >= 2900 && c.sink.acks[1].ran_ms <= 3000);
}

void test_csm_motor_rejected_when_door_open() {
  MotorChamber c;
  c.door_in.level = true;
  c.run(400);
  TEST_ASSERT_TRUE(c.csm.doorOpen());
  c.send("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m2\",\"action\":\"run\",\"run\":true,"
         "\"duration_s\":5.0,\"sequence_number\":7,\"ttl_s\":60}}");
  if (limits::kMotorDoorInterlock) {
    TEST_ASSERT_FALSE(c.motor.isMotorRunning());
    TEST_ASSERT_EQUAL(1, c.sink.n);
    TEST_ASSERT_TRUE(c.sink.acks[0].status == AckStatus::Rejected);
    TEST_ASSERT_EQUAL_STRING("door open", c.sink.acks[0].message);
  } else {
    // Interlock compiled out (default): the decorative hat runs with the door open.
    TEST_ASSERT_TRUE(c.motor.isMotorRunning());
  }
}

void test_csm_door_opening_stops_running_motor() {
  MotorChamber c;
  c.run(400);
  c.send("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m3\",\"action\":\"run\",\"run\":true,"
         "\"duration_s\":8.0,\"sequence_number\":8,\"ttl_s\":60}}");
  TEST_ASSERT_TRUE(c.motor.isMotorRunning());
  c.run(1000);
  c.door_in.level = true;
  c.run(400);
  if (limits::kMotorDoorInterlock) {
    TEST_ASSERT_FALSE(c.motor.isMotorRunning());
    TEST_ASSERT_FALSE(c.gate.state());
    TEST_ASSERT_TRUE(c.sink.acks[c.sink.n - 1].status == AckStatus::Failed);
  } else {
    TEST_ASSERT_TRUE(c.motor.isMotorRunning());
  }
}

void test_csm_v10_stop_directive_cancels() {
  MotorChamber c;
  c.run(400);
  c.send("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m4\",\"action\":\"run\",\"run\":true,"
         "\"duration_s\":8.0,\"sequence_number\":9,\"ttl_s\":60}}");
  c.run(1000);
  // Same id, now as a stop directive (server-side DELETE /commands/m4)
  c.send("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m4\",\"action\":\"stop\",\"run\":false,"
         "\"duration_s\":0,\"sequence_number\":9,\"ttl_s\":30}}");
  TEST_ASSERT_FALSE(c.motor.isMotorRunning());
  TEST_ASSERT_TRUE(c.sink.acks[c.sink.n - 1].status == AckStatus::Cancelled);
  const int n = c.sink.n;
  // Re-delivered stop: acked once only
  c.send("{\"chamber_id\":\"A\",\"motor\":{\"command_id\":\"m4\",\"action\":\"stop\",\"run\":false,"
         "\"duration_s\":0,\"sequence_number\":9,\"ttl_s\":30}}");
  c.run(500);
  TEST_ASSERT_EQUAL(n, c.sink.n);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_motor_safe_off_on_boot);
  RUN_TEST(test_motor_refused_when_disarmed_or_zero);
  RUN_TEST(test_motor_duration_clamped_to_max);
  RUN_TEST(test_motor_auto_timeout_exact);
  RUN_TEST(test_motor_hw_timer_cuts_gate_if_loop_stalls);
  RUN_TEST(test_motor_never_on_when_not_running);
  RUN_TEST(test_motor_no_double_start_and_stop);
  RUN_TEST(test_motor_wiggle_pattern_pulses_but_bounded);
  RUN_TEST(test_csm_animate_motor_accepted_then_completed);
  RUN_TEST(test_csm_motor_rejected_when_door_open);
  RUN_TEST(test_csm_door_opening_stops_running_motor);
  RUN_TEST(test_csm_v10_stop_directive_cancels);
  return UNITY_END();
}
