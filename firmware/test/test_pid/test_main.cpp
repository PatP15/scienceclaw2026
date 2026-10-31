// PID tests: PidController maths, PidMode adapter (deadband, dead time, reset), closed loop on the
// first-order ThermalTestPlant through the real PeltierDriver, console + NVS round trip.
// Independent of any hook into main.cpp / TemperatureController.
#include <unity.h>

#include <math.h>
#include <string.h>

#include "app/PidConsole.h"
#include "control/PeltierDriver.h"
#include "control/PidController.h"
#include "control/PidMode.h"
#include "simulation/MockActuators.h"
#include "simulation/MockSensors.h"

using namespace fa;

void setUp() {}
void tearDown() {}

namespace {

struct Loop {
  MockPwmOut pa, pb;
  MockDigitalOut en;
  PeltierDriver drv;
  ThermalTestPlant plant;
  PidMode pm;
  uint32_t t_ms = 1000;
  float max_temp = -1e9f, min_temp = 1e9f;
  bool both_nonzero = false;

  static PeltierConfig pc(float maxf) {
    PeltierConfig c;
    c.max_fraction = maxf;
    c.ramp_per_s = 0.1f;
    c.dead_time_ms = 60000;
    c.heat_on_channel_a = true;
    return c;
  }
  static PidModeConfig mc(float maxf) {
    PidModeConfig c;
    c.max_fraction = maxf;
    return c;
  }
  explicit Loop(float maxf = 0.8f, float start_c = 21.0f)
      : drv(&pa, &pb, &en, pc(maxf)), plant(&pa, &pb, nullptr, nullptr), pm(mc(maxf)) {
    plant.chamber_c = start_c;
    drv.begin(t_ms);
    drv.setArmed(true);
  }
  void gains(float kp, float ki, float kd, float alpha = 0.8f) {
    PidGains g;
    g.kp = kp; g.ki = ki; g.kd = kd; g.alpha = alpha;
    TEST_ASSERT_TRUE(pm.setGains(g));
    pm.select(ControlSel::Pid);
  }
  ControlDemand last{};
  void step(float sp) {
    plant.step(1.0f);
    t_ms += 1000;
    last = pm.compute(plant.chamber_c, sp, t_ms);
    drv.request(last.dir, last.fraction, t_ms);
    drv.update(t_ms);
    if (plant.chamber_c > max_temp) max_temp = plant.chamber_c;
    if (plant.chamber_c < min_temp) min_temp = plant.chamber_c;
    if (pa.fraction() > 0 && pb.fraction() > 0) both_nonzero = true;
  }
  void run(float sp, int secs) { for (int i = 0; i < secs; ++i) step(sp); }
};

}  // namespace

// ------------------------------------------------------------------ closed loop
void test_default_gains_step_response() {
  Loop l;  // Kp 0.25, Ki 0, Kd 0 = existing proportional behaviour
  l.pm.select(ControlSel::Pid);
  l.run(25.0f, 4 * 3600);
  TEST_ASSERT_TRUE(l.max_temp <= 25.0f + 0.1f);                       // no overshoot
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 25.0f, l.plant.chamber_c);           // P-only: steady offset < 1 C
  TEST_ASSERT_FALSE(l.both_nonzero);
}

void test_tuned_pi_step_response() {
  Loop l;
  l.gains(0.5f, 0.0004f, 0.0f);
  l.run(25.0f, 4 * 3600);
  const float step = 4.0f;
  TEST_ASSERT_TRUE_MESSAGE(l.max_temp <= 25.0f + 0.10f * step, "overshoot > 10% of step");
  TEST_ASSERT_FLOAT_WITHIN(0.3f, 25.0f, l.plant.chamber_c);
  TEST_ASSERT_FALSE(l.both_nonzero);
}

void test_tuned_pid_with_derivative() {
  Loop l;
  l.gains(0.5f, 0.0004f, 20.0f, 0.9f);
  l.run(25.0f, 4 * 3600);
  TEST_ASSERT_TRUE(l.max_temp <= 25.0f + 0.4f);
  TEST_ASSERT_FLOAT_WITHIN(0.3f, 25.0f, l.plant.chamber_c);
}

void test_pwm_cap_never_exceeded() {
  Loop l(0.30f);
  l.gains(1.5f, 0.001f, 0.0f);
  l.run(30.0f, 3600);
  TEST_ASSERT_TRUE(l.pa.max_seen <= 0.30f + 1e-4f);
  TEST_ASSERT_TRUE(l.pb.max_seen <= 0.30f + 1e-4f);
}

// ------------------------------------------------------------------ anti-windup
void test_antiwindup_integral_bounded_in_saturation() {
  PidController c(0.3f);
  PidGains g; g.kp = 0.25f; g.ki = 0.01f;
  c.setGains(g);
  for (int i = 0; i < 5000; ++i) c.update(35.0f, 21.0f, 1.0f);  // P = 3.5 >> max: always saturated
  TEST_ASSERT_TRUE(c.iTerm() <= 0.3f + 1e-6f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.iTerm());                      // P alone saturates -> I never grew
  TEST_ASSERT_EQUAL_FLOAT(0.3f, c.output());
  // Setpoint drops below the measurement: output reverses on the very next sample.
  const float u = c.update(20.0f, 21.0f, 1.0f);
  TEST_ASSERT_TRUE(u < 0.0f);
}

void test_antiwindup_plant_no_large_overshoot_after_drop() {
  // Cap 0.30 -> plant can only reach ~26.4 C; hold an unreachable setpoint for 2 h, then drop.
  Loop l(0.30f);
  l.gains(0.15f, 0.002f, 0.0f);
  l.run(35.0f, 7200);
  const float i_after = l.pm.pid().iTerm();
  TEST_ASSERT_TRUE(i_after <= 0.30f + 1e-4f);
  l.min_temp = 1e9f;
  l.run(24.0f, 6 * 3600);
  TEST_ASSERT_TRUE_MESSAGE(l.min_temp >= 24.0f - 1.0f, "undershoot after setpoint drop");
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 24.0f, l.plant.chamber_c);
}

// ------------------------------------------------------------------ derivative
void test_no_derivative_kick_on_setpoint_step() {
  PidController c(0.8f);
  PidGains g; g.kp = 0.1f; g.ki = 0.0f; g.kd = 100.0f; g.alpha = 0.0f;
  c.setGains(g);
  for (int i = 0; i < 10; ++i) c.update(25.0f, 23.0f, 1.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.dTerm());
  c.update(30.0f, 23.0f, 1.0f);  // setpoint step, measurement flat
  TEST_ASSERT_EQUAL_FLOAT(0.0f, c.dTerm());
  c.update(30.0f, 23.5f, 1.0f);  // measurement rises 0.5 C/s -> D opposes it
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, -50.0f, c.dTerm());
}

void test_derivative_filter_smooths() {
  PidController c(0.8f);
  PidGains g; g.kp = 0.0f; g.kd = 10.0f; g.alpha = 0.5f;
  c.setGains(g);
  c.update(25.0f, 23.0f, 1.0f);
  c.update(25.0f, 24.0f, 1.0f);  // raw = -10, filtered = -5
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, -5.0f, c.dTerm());
  c.update(25.0f, 25.0f, 1.0f);  // raw -10 -> -7.5
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, -7.5f, c.dTerm());
}

void test_bumpless_transfer() {
  PidController c(0.8f);
  PidGains g; g.kp = 0.25f; g.ki = 0.001f;
  c.setGains(g);
  c.engage(0.40f);
  const float u = c.update(25.0f, 24.0f, 1.0f);  // P = 0.25
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 0.40f, u);
  // Without Ki there is nothing to initialise: pure proportional.
  PidController p(0.8f);
  PidGains g2; g2.kp = 0.25f;
  p.setGains(g2);
  p.engage(0.40f);
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, 0.25f, p.update(25.0f, 24.0f, 1.0f));
}

// ------------------------------------------------------------------ dead time / reset / deadband
void test_direction_change_honours_dead_time() {
  PidMode pm;
  PidGains g; g.kp = 0.5f;
  pm.setGains(g);
  uint32_t t = 1000;
  uint32_t last_heat = 0, first_cool = 0;
  bool saw_cool = false;
  // Cold: heating demand for 30 s.
  for (int i = 0; i < 30; ++i, t += 1000) {
    ControlDemand d = pm.compute(20.0f, 25.0f, t);
    TEST_ASSERT_TRUE(d.dir == Direction::Heat);
    last_heat = t;
  }
  // Now far too hot: Cool is wanted, must be held at zero for 60 s since last heat demand.
  for (int i = 0; i < 120; ++i, t += 1000) {
    ControlDemand d = pm.compute(30.0f, 25.0f, t);
    if (d.dir == Direction::Cool && !saw_cool) { saw_cool = true; first_cool = t; }
    if (!saw_cool) {
      TEST_ASSERT_TRUE(d.dir == Direction::None);
      TEST_ASSERT_EQUAL_FLOAT(0.0f, d.fraction);
      TEST_ASSERT_TRUE(pm.inDeadTimeHold());
    }
  }
  TEST_ASSERT_TRUE(saw_cool);
  TEST_ASSERT_TRUE(first_cool - last_heat >= 60000);
}

void test_dead_time_survives_reset_and_driver_never_double_drives() {
  Loop l;
  l.gains(0.5f, 0.0f, 0.0f);
  l.run(30.0f, 120);   // heating
  l.pm.reset();        // e.g. door opened: I/D cleared but the stage was just heating
  l.plant.chamber_c = 40.0f;  // artificially hot
  uint32_t first_cool_ms = 0, last_heat_ms = l.t_ms;
  for (int i = 0; i < 200 && !first_cool_ms; ++i) {
    l.step(25.0f);
    if (l.last.dir == Direction::Cool) first_cool_ms = l.t_ms;
    l.plant.chamber_c = 40.0f;
  }
  TEST_ASSERT_TRUE(first_cool_ms != 0);
  TEST_ASSERT_TRUE(first_cool_ms - last_heat_ms >= 59000);
  TEST_ASSERT_FALSE(l.both_nonzero);
}

void test_reset_clears_integral_and_state() {
  PidMode pm;
  PidGains g; g.kp = 0.1f; g.ki = 0.01f; g.kd = 5.0f;
  pm.setGains(g);
  uint32_t t = 1000;
  for (int i = 0; i < 30; ++i, t += 1000) pm.compute(23.0f + 0.01f * i, 25.0f, t);
  TEST_ASSERT_TRUE(pm.pid().iTerm() > 0.0f);
  TEST_ASSERT_TRUE(pm.active());
  pm.reset();  // FAULT / door open / idle
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pm.pid().iTerm());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pm.pid().dTerm());
  TEST_ASSERT_FALSE(pm.active());
  // Non-finite input also resets and returns no demand.
  for (int i = 0; i < 5; ++i, t += 1000) pm.compute(23.0f, 25.0f, t);
  ControlDemand d = pm.compute(NAN, 25.0f, t);
  TEST_ASSERT_TRUE(d.dir == Direction::None);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pm.pid().iTerm());
}

void test_deadband_no_chatter() {
  PidMode pm;  // Kp 0.25: start at |e| >= 0.6 C, stop at |e| < 0.3 C
  uint32_t t = 1000;
  int transitions = 0;
  Direction prev = Direction::None;
  // Hover near the start threshold (e = 0.6 +- 0.04 C) -> at most one start, no chatter.
  for (int i = 0; i < 600; ++i, t += 1000) {
    const float noise = (i % 2 ? 0.04f : -0.04f);
    ControlDemand d = pm.compute(25.0f - 0.6f - noise, 25.0f, t);
    if (d.dir != prev) { ++transitions; prev = d.dir; }
  }
  TEST_ASSERT_TRUE(transitions <= 1);
  // Inside the deadband from idle: never starts.
  PidMode q;
  for (int i = 0; i < 100; ++i, t += 1000)
    TEST_ASSERT_TRUE(q.compute(25.0f - 0.4f, 25.0f, t).dir == Direction::None);
  // Once active, hovering around the stop point (0.3 C) with noise: hysteresis keeps it from flapping.
  int tr2 = 0;
  prev = Direction::None;
  PidMode r;
  r.compute(24.0f, 25.0f, t); t += 1000;  // start heating
  for (int i = 0; i < 600; ++i, t += 1000) {
    const float noise = (i % 2 ? 0.04f : -0.04f);
    ControlDemand d = r.compute(25.0f - 0.45f - noise, 25.0f, t);  // u ~ 0.11: between 0.075 and 0.15
    if (d.dir != prev) { ++tr2; prev = d.dir; }
  }
  TEST_ASSERT_TRUE(tr2 <= 1);
}

void test_fraction_within_min_max() {
  PidMode pm;
  uint32_t t = 1000;
  ControlDemand d = pm.compute(0.0f + 10.0f, 25.0f, t);  // e = 15 -> saturated
  TEST_ASSERT_TRUE(d.dir == Direction::Heat);
  TEST_ASSERT_EQUAL_FLOAT(0.8f, d.fraction);
  PidMode q;
  d = q.compute(24.35f, 25.0f, t);  // u = 0.1625: just above min
  TEST_ASSERT_TRUE(d.dir == Direction::Heat);
  TEST_ASSERT_TRUE(d.fraction >= 0.15f);
}

// ------------------------------------------------------------------ console + NVS
static bool con(PidMode& pm, MockKeyValueStore& kv, const char* line, char* reply, size_t n) {
  char buf[96];
  strncpy(buf, line, sizeof buf - 1);
  buf[sizeof buf - 1] = 0;
  char* argv[6] = {0};
  int argc = 0;
  for (char* tok = strtok(buf, " "); tok && argc < 6; tok = strtok(nullptr, " ")) argv[argc++] = tok;
  return handlePidConsole(argc, argv, pm, &kv, reply, n);
}

void test_console_set_print_reset_mode_and_nvs_roundtrip() {
  PidMode pm;
  MockKeyValueStore kv;
  char r[256];
  TEST_ASSERT_TRUE(con(pm, kv, "pid 0.5 0.0004 12 0.7", r, sizeof r));
  TEST_ASSERT_EQUAL_STRING("OK pid kp=0.5 ki=0.0004 kd=12 alpha=0.7", r);
  TEST_ASSERT_TRUE(con(pm, kv, "pid 0.4 0.001 0", r, sizeof r));  // alpha kept
  TEST_ASSERT_EQUAL_STRING("OK pid kp=0.4 ki=0.001 kd=0 alpha=0.7", r);
  TEST_ASSERT_TRUE(con(pm, kv, "mode pid", r, sizeof r));
  TEST_ASSERT_EQUAL_STRING("OK mode pid", r);
  TEST_ASSERT_TRUE(pm.selected() == ControlSel::Pid);

  PidMode fresh;  // "reboot"
  TEST_ASSERT_TRUE(fresh.load(&kv));
  TEST_ASSERT_EQUAL_FLOAT(0.4f, fresh.gains().kp);
  TEST_ASSERT_EQUAL_FLOAT(0.001f, fresh.gains().ki);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, fresh.gains().kd);
  TEST_ASSERT_EQUAL_FLOAT(0.7f, fresh.gains().alpha);
  TEST_ASSERT_TRUE(fresh.selected() == ControlSel::Pid);

  // live terms
  uint32_t t = 1000;
  for (int i = 0; i < 5; ++i, t += 1000) pm.compute(24.0f, 25.0f, t);  // P = 0.4 (unsaturated)
  TEST_ASSERT_TRUE(con(pm, kv, "pid", r, sizeof r));
  TEST_ASSERT_NOT_NULL(strstr(r, "kp=0.4"));
  TEST_ASSERT_NOT_NULL(strstr(r, "mode=pid"));
  TEST_ASSERT_NOT_NULL(strstr(r, "P=0.400"));
  TEST_ASSERT_NOT_NULL(strstr(r, "I="));
  TEST_ASSERT_NOT_NULL(strstr(r, "u="));
  TEST_ASSERT_TRUE(pm.pid().iTerm() > 0.0f);
  TEST_ASSERT_TRUE(con(pm, kv, "pid reset", r, sizeof r));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pm.pid().iTerm());

  TEST_ASSERT_TRUE(con(pm, kv, "mode prop", r, sizeof r));
  TEST_ASSERT_EQUAL_STRING("OK mode prop", r);
  TEST_ASSERT_TRUE(con(pm, kv, "mode hyst", r, sizeof r));
  TEST_ASSERT_EQUAL_STRING("OK mode hyst", r);
  TEST_ASSERT_TRUE(con(pm, kv, "mode banana", r, sizeof r));
  TEST_ASSERT_EQUAL_STRING_LEN("ERR", r, 3);
  TEST_ASSERT_TRUE(pm.selected() == ControlSel::Hysteresis);
}

void test_console_rejects_bad_values_and_ignores_other_commands() {
  PidMode pm;
  MockKeyValueStore kv;
  char r[256];
  const char* bad[] = {"pid -1 0 0", "pid 3 0 0", "pid 0.25 1 0", "pid 0.25 0 9999", "pid nan 0 0",
                       "pid 0.25 0 0 1.5", "pid abc 0 0", "pid 0.25 0", "pid 1 2 3 4 5"};
  for (const char* b : bad) {
    TEST_ASSERT_TRUE(con(pm, kv, b, r, sizeof r));
    TEST_ASSERT_EQUAL_STRING_LEN("ERR", r, 3);
  }
  TEST_ASSERT_EQUAL_FLOAT(0.25f, pm.gains().kp);  // unchanged
  TEST_ASSERT_EQUAL_UINT32(0, kv.size());          // nothing stored
  TEST_ASSERT_FALSE(con(pm, kv, "setpoint 25", r, sizeof r));
  TEST_ASSERT_FALSE(con(pm, kv, "arm peltier A", r, sizeof r));
}

void test_nvs_missing_or_corrupt_uses_defaults() {
  PidMode pm;
  MockKeyValueStore kv;
  TEST_ASSERT_FALSE(pm.load(&kv));
  TEST_ASSERT_EQUAL_FLOAT(0.25f, pm.gains().kp);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pm.gains().ki);
  TEST_ASSERT_TRUE(pm.selected() == ControlSel::Proportional);
  kv.putString("pid", "garbage");
  TEST_ASSERT_FALSE(pm.load(&kv));
  kv.putString("pid", "99 0 0 0.5 2");  // out of range
  TEST_ASSERT_FALSE(pm.load(&kv));
  TEST_ASSERT_EQUAL_FLOAT(0.25f, pm.gains().kp);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_default_gains_step_response);
  RUN_TEST(test_tuned_pi_step_response);
  RUN_TEST(test_tuned_pid_with_derivative);
  RUN_TEST(test_pwm_cap_never_exceeded);
  RUN_TEST(test_antiwindup_integral_bounded_in_saturation);
  RUN_TEST(test_antiwindup_plant_no_large_overshoot_after_drop);
  RUN_TEST(test_no_derivative_kick_on_setpoint_step);
  RUN_TEST(test_derivative_filter_smooths);
  RUN_TEST(test_bumpless_transfer);
  RUN_TEST(test_direction_change_honours_dead_time);
  RUN_TEST(test_dead_time_survives_reset_and_driver_never_double_drives);
  RUN_TEST(test_reset_clears_integral_and_state);
  RUN_TEST(test_deadband_no_chatter);
  RUN_TEST(test_fraction_within_min_max);
  RUN_TEST(test_console_set_print_reset_mode_and_nvs_roundtrip);
  RUN_TEST(test_console_rejects_bad_values_and_ignores_other_commands);
  RUN_TEST(test_nvs_missing_or_corrupt_uses_defaults);
  return UNITY_END();
}
