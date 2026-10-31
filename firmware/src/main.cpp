// FermentAI chamber firmware — ESP32-S3-CAM (Arduino-ESP32 2.0.x).
//
// Task layout
//   loopTask (core 1): cooperative Scheduler — door 20 ms, motor 20 ms, safety 200 ms,
//                      sensors, control 1 s, command execution, telemetry snapshot, console.
//                      Nothing here touches the network or the camera. Loop WDT enabled.
//   netTask  (core 0): Wi-Fi upkeep, desired-control polling, ack posting, telemetry queue,
//                      camera capture + JPEG upload. All blocking I/O lives here.
//   esp_timer        : independent motor gate cut-off.
// Shared state: one mutex-protected snapshot + clock; a FreeRTOS queue hands parsed commands
// from netTask to loopTask; AckQueue (mutex) carries acks the other way.
#include <Arduino.h>
#include <Wire.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <driver/gpio.h>
#include <soc/gpio_struct.h>

#include <mutex>
#include <new>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#include "app/ChamberStateMachine.h"
#include "board_config.h"
#include "camera/CameraManager.h"
#include "command_schema.h"
#include "device_config.h"
#include "hal/ArduinoHal.h"
#include "network/AckQueue.h"
#include "network/CommandClient.h"
#include "network/NetworkManager.h"
#include "network/TelemetryClient.h"
#include "sensors/BME680Sensor.h"
#include "sensors/ENS160AHT21Sensor.h"
#include "sensors/SHT31Sensor.h"
#include "simulation/MockActuators.h"
#include "simulation/MockSensors.h"
#include "telemetry_schema.h"
#include "util/Scheduler.h"

#ifndef FERMENTAI_WIFI_SSID
#define FERMENTAI_WIFI_SSID ""
#endif
#ifndef FERMENTAI_WIFI_PASS
#define FERMENTAI_WIFI_PASS ""
#endif
#ifndef FERMENTAI_BACKEND_URL
#define FERMENTAI_BACKEND_URL "http://192.168.8.10:8000"
#endif
#ifndef FERMENTAI_API_TOKEN
#define FERMENTAI_API_TOKEN ""
#endif

using namespace fa;

namespace {

constexpr ContractProfile kProfile = defaultContractProfile();
constexpr Provenance kProv = FERMENTAI_NOIO ? Provenance::Simulated : Provenance::Measured;

// ------------------------------------------------------------------ hardware / mock objects
NvsStore nvs;

#if FERMENTAI_NOIO
MockEnvSensor envSensor("bme680");
MockOneWireBus owBus;
MockAnalogIn phAdc;
MockDigitalIn doorIn;
MockPwmOut pwmA, pwmB;
MockDigitalOut peltierEn, motorGate;
MockOneShotTimer motorCutoff(&motorGate);
ThermalTestPlant plant(&pwmA, &pwmB, &envSensor, &owBus);
#else
#if ENV_SENSOR == ENV_SENSOR_SHT31
SHT31Sensor envSensor(&Wire);
#elif ENV_SENSOR == ENV_SENSOR_ENS160_AHT21
ENS160AHT21Sensor envSensor(&Wire);
#else
BME680Sensor envSensor(&Wire);
#endif
DallasBus owBus(pins::kOneWire);
AdcIn phAdc(pins::kPhAdc);
GpioIn doorIn(pins::kDoorReed, true);
#if FERMENTAI_ALLOW_PELTIER_OUTPUT
LedcPwmOut pwmA(pins::kPeltierPwmA, 2, cfg::kPeltierPwmHz, cfg::kPeltierPwmBits);
LedcPwmOut pwmB(pins::kPeltierPwmB, 3, cfg::kPeltierPwmHz, cfg::kPeltierPwmBits);
GpioOut peltierEn(pins::kPeltierEnablePin);
#else
// Peltier outputs compiled out: the logic drives inert objects; the pins stay LOW.
MockPwmOut pwmA, pwmB;
MockDigitalOut peltierEn;
#endif
#if MOTOR_ENABLED
GpioOut motorGate(pins::kMotorPin);
GateCutoffTimer motorCutoff(pins::kMotorPin);
#endif
#endif

PHSensor::Config phConfig() {
  PHSensor::Config c;
  c.enabled = PH_SENSOR_ENABLED;
  c.samples = cfg::kPhSamples;
  c.adc_min_mv = cfg::kPhAdcMinMv;
  c.adc_max_mv = cfg::kPhAdcMaxMv;
  c.min_span_mv = cfg::kPhMinSpanMv;
  return c;
}
SensorManager::Config smConfig() {
  SensorManager::Config c;
  c.env_period_ms = cfg::kEnvPeriodMs;
  c.env_stale_ms = cfg::kEnvStaleMs;
  c.env_warmup_ms = cfg::kBme680WarmupMs;
  c.stuck_ms = cfg::kStuckValueMs;
  c.env_temp_offset_c = cfg::kBme680TempOffsetC;
  c.prov = kProv;
  return c;
}
PeltierConfig peltierConfig() {
  PeltierConfig c;
  c.max_fraction = cfg::kPeltierMaxFraction;
  c.ramp_per_s = cfg::kRampPerSecond;
  c.dead_time_ms = limits::kHeatCoolDeadTimeMs;
  c.heat_on_channel_a = cfg::kHeatOnChannelA;
  return c;
}
TemperatureController::Config ctlConfig() {
  TemperatureController::Config c;
  c.mode = TemperatureController::Mode::Proportional;
  c.hysteresis_c = cfg::kHysteresisC;
  c.kp_per_c = cfg::kProportionalGainPerC;
  c.min_fraction = cfg::kPeltierMinFraction;
  c.max_fraction = cfg::kPeltierMaxFraction;
  return c;
}

DS18B20Manager ds(&owBus, &nvs, cfg::kDsStaleMs, cfg::kDsRediscoverMs, kProv);
PHSensor ph(&phAdc, &nvs, phConfig(), kProv);
DoorSensor door(&doorIn, cfg::kDoorOpenLevelHigh, cfg::kDoorDebounceMs);
SensorManager sensors(&envSensor, &ds, PH_SENSOR_ENABLED ? &ph : nullptr, &door, smConfig());
SafetyManager safety(SafetyManager::defaults());
TemperatureController controller(ctlConfig());
PeltierDriver peltier(&pwmA, &pwmB, &peltierEn, peltierConfig());
#if MOTOR_ENABLED
MotorController motorCtl(&motorGate, &motorCutoff, limits::kMotorMaxDurationMs);
MotorController* motorPtr = &motorCtl;
#else
MotorController* motorPtr = nullptr;
#endif
AckQueue acks(24);
ClockSync loopClock;  // control-loop copy of the shared clock

ChamberStateMachine::Config csmConfig() {
  ChamberStateMachine::Config c;
  c.chamber_id = CHAMBER_ID;
  c.heatsink_probes_required = HEATSINK_PROBES_REQUIRED;
  c.camera_available = false;  // set after camera init
  c.offline_hold_ms = limits::kOfflineHoldMs;
  return c;
}
ChamberStateMachine csm(csmConfig(), sensors, safety, controller, peltier, motorPtr, &nvs, &acks,
                        loopClock);

CameraSettings camSettings() {
  CameraSettings s;
  s.jpeg_quality = cfg::kCameraJpegQuality;
  s.interval_ms = cfg::kCameraPeriodMs;
  return s;
}
CameraManager camera(camSettings());

// ------------------------------------------------------------------ shared state (mutex)
struct Shared {
  std::mutex m;
  TelemetryRecord snap;
  char snap_exp[65] = {0};
  char snap_cmd[65] = {0};
  bool snap_valid = false;
  ClockSync clock;
  bool image_req = false;
  char image_cmd[65] = {0};
  int64_t image_seq = -1;
  bool camera_ok = false;
} g;

QueueHandle_t cmdQueue = nullptr;
char bootId[12] = {0};
esp_reset_reason_t resetReason = ESP_RST_UNKNOWN;
NetConfig netCfg;
NetworkManager* net = nullptr;
TelemetryClient* telemetry = nullptr;
CommandClient* commands = nullptr;
Scheduler sched;

uint64_t uptimeMs() { return (uint64_t)(esp_timer_get_time() / 1000); }

const char* resetName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    default: return "other";
  }
}

// ------------------------------------------------------------------ loop-side tasks
void tDoor(uint32_t now, void*) { sensors.updateDoor(now); }
void tMotor(uint32_t now, void*) { csm.motorTick(now); }
void tSafety(uint32_t now, void*) {
#if FERMENTAI_NOIO
  plant.step(cfg::kSafetyPeriodMs / 1000.0f);
#endif
  csm.safetyTick(now);
}
void tEnv(uint32_t now, void*) { sensors.updateEnv(now); }
void tDs(uint32_t now, void*) { sensors.updateDs(now); }
void tPh(uint32_t now, void*) { sensors.updatePh(now); }
void tControl(uint32_t now, void*) { csm.controlTick(now); }

void tCommands(uint32_t now, void*) {
  DesiredControlParse* p = nullptr;
  while (cmdQueue && xQueueReceive(cmdQueue, &p, 0) == pdTRUE) {
    {
      std::lock_guard<std::mutex> l(g.m);
      loopClock = g.clock;
    }
    csm.handleDesiredControl(*p, millis());
    delete p;
  }
  char id[65];
  int64_t seq;
  if (csm.takeImageRequest(id, sizeof id, seq)) {
    std::lock_guard<std::mutex> l(g.m);
    g.image_req = true;
    snprintf(g.image_cmd, sizeof g.image_cmd, "%s", id);
    g.image_seq = seq;
  }
  (void)now;
}

void tSnapshot(uint32_t now, void*) {
  TelemetryRecord r;
  csm.fillTelemetry(r, now);
  std::lock_guard<std::mutex> l(g.m);
  g.snap = r;
  snprintf(g.snap_exp, sizeof g.snap_exp, "%s", r.experiment_id ? r.experiment_id : "");
  snprintf(g.snap_cmd, sizeof g.snap_cmd, "%s", r.last_command_id ? r.last_command_id : "");
  g.snap.experiment_id = g.snap_exp[0] ? g.snap_exp : nullptr;
  g.snap.last_command_id = g.snap_cmd[0] ? g.snap_cmd : nullptr;
  g.snap_valid = true;
}

// ------------------------------------------------------------------ serial console
void printStatus(uint32_t now) {
  const ChamberObservation o = sensors.observe(now);
  auto f = [](const Reading& r) { return r.valid ? r.value : NAN; };
  Serial.printf("[%s %s] fw=%s boot=%s reset=%s state=%s mode=%s out=%.2f armed(peltier=%d motor=%d)\n",
                CHAMBER_ID, DEVICE_ID, FERMENTAI_FIRMWARE_VERSION, bootId, resetName(resetReason),
                runStateName(csm.runState()), peltierModeName(csm.reportedMode(), kProfile),
                peltier.appliedFraction(), peltier.armed(), motorPtr ? motorPtr->armed() : 0);
  Serial.printf("  chamber=%.2fC rh=%.1f%% p=%.0fPa gas=%.0fohm starter=%.2fC hs_int=%.2fC hs_ext=%.2fC\n",
                f(o.chamber_temp), f(o.rh), f(o.pressure_pa), f(o.gas_ohm), f(o.starter_temp),
                f(o.int_heatsink), f(o.ext_heatsink));
  Serial.printf("  ph=%.2f mv=%.1f raw=%ld cal=%d door=%s opens=%lu setpoint=%.2f faults=0x%lx\n",
                f(o.ph), f(o.ph_mv), (long)o.ph_raw_adc, o.ph_calibrated,
                o.door_known ? (o.door_open ? "open" : "closed") : "unknown",
                (unsigned long)o.door_open_count,
                csm.experiment().hasSetpoint() ? csm.experiment().setpoint() : NAN,
                (unsigned long)csm.faults());
#if !FERMENTAI_NOIO && ENV_SENSOR == ENV_SENSOR_ENS160_AHT21
  Serial.printf("  ens160: present=%d addr=0x%02x valid=%d warmup=%d aqi=%u tvoc=%u ppb eco2=%u ppm (indices, not CO2)\n",
                envSensor.ensPresent(), envSensor.ensAddress(), envSensor.ensDataValid(), envSensor.warmingUp(),
                (unsigned)envSensor.aqi(), (unsigned)envSensor.tvocPpb(), (unsigned)envSensor.eco2Ppm());
#endif
  Serial.print("  flags:");
  for (size_t i = 0; i < o.flags.n; ++i) Serial.printf(" %s", o.flags.items[i]);
  Serial.printf("\n  wifi=%d rssi=%ld tel_q=%u acks_q=%u heap=%lu psram=%lu\n",
                net && net->connected(), net ? (long)net->rssi() : 0L,
                telemetry ? (unsigned)telemetry->queued() : 0u, (unsigned)acks.size(),
                (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getFreePsram());
}

void printScan(uint32_t now) {
  ds.rediscover(now);
  Serial.printf("OneWire: %u probe(s)\n", (unsigned)ds.discoveredCount());
  for (size_t i = 0; i < ds.discoveredCount(); ++i) {
    char hex[17];
    DS18B20Manager::formatRom(ds.discovered(i), hex, sizeof hex);
    const char* role = "UNASSIGNED";
    for (size_t r = 0; r < kNumProbeRoles; ++r) {
      RomAddr a;
      if (ds.roleRom((ProbeRole)r, a) && a == ds.discovered(i)) role = probeRoleName((ProbeRole)r);
    }
    Serial.printf("  %s  %s\n", hex, role);
  }
  for (size_t r = 0; r < kNumProbeRoles; ++r) {
    const Reading rd = ds.reading((ProbeRole)r, now);
    Serial.printf("  role %-30s %s %.3f\n", probeRoleName((ProbeRole)r), healthName(rd.health),
                  rd.valid ? rd.value : NAN);
  }
}

void localSetpoint(float c, uint32_t now) {
  DesiredControlParse p;
  p.ok = true;
  p.n = 1;
  DeviceCommand& d = p.cmds[0];
  snprintf(d.command_id, sizeof d.command_id, "local-%lu", (unsigned long)now);
  snprintf(d.chamber_id, sizeof d.chamber_id, "%s", CHAMBER_ID);
  d.action = CmdAction::SetSetpoint;
  d.channel = CmdChannel::FlatControl;
  d.flat = true;
  d.has_setpoint = true;
  d.setpoint_c = c;
  d.has_ttl = true;
  d.ttl_ms = 90000;
  d.received_ms = now;
  csm.handleDesiredControl(p, now);  // same validation + clamp path as backend commands
}

void handleLine(char* line) {
  const uint32_t now = millis();
  char* argv[6] = {0};
  int argc = 0;
  for (char* tok = strtok(line, " \t"); tok && argc < 6; tok = strtok(nullptr, " \t")) argv[argc++] = tok;
  if (argc == 0) return;
  const char* cmd = argv[0];
  if (!strcmp(cmd, "help")) {
    Serial.println(
        "status | scan | assign <rom16hex> <starter|internal_heatsink|external_heatsink> |\n"
        "unassign <role> | phcal 4|7|clear | arm peltier <CHAMBER_ID> | disarm peltier |\n"
        "arm motor | disarm motor | motor test <ms> | setpoint <C> (local, clamped) |\n"
        "wifi <ssid> <pass> | backend <url> | token <token> | pin <gpio> | pwmdiag | owtest | owsearch | i2cscan | reboot");
  } else if (!strcmp(cmd, "status")) {
    printStatus(now);
  } else if (!strcmp(cmd, "scan")) {
    printScan(now);
  } else if (!strcmp(cmd, "assign") && argc == 3) {
    RomAddr rom;
    ProbeRole role;
    if (!DS18B20Manager::parseRom(argv[1], rom) || !DS18B20Manager::parseRole(argv[2], role)) {
      Serial.println("ERR usage: assign <16 hex> <starter|internal_heatsink|external_heatsink>");
      return;
    }
    Serial.println(ds.assignRole(rom, role) ? "OK (persisted in NVS)" : "ERR");
  } else if (!strcmp(cmd, "unassign") && argc == 2) {
    ProbeRole role;
    Serial.println(DS18B20Manager::parseRole(argv[1], role) && ds.clearRole(role) ? "OK" : "ERR");
  } else if (!strcmp(cmd, "phcal") && argc == 2) {
    if (!PH_SENSOR_ENABLED) {
      Serial.println("ERR pH disabled on this device (PH_SENSOR_ENABLED=0)");
    } else if (!strcmp(argv[1], "clear")) {
      ph.clearCalibration();
      Serial.println("OK calibration cleared");
    } else {
      char date[40];
      std::lock_guard<std::mutex> l(g.m);
      const int64_t e = g.clock.nowEpochMs(now);
      if (e >= 0)
        formatIso8601Utc(e, date, sizeof date);
      else
        snprintf(date, sizeof date, "unsynced+%lums", (unsigned long)now);  // never invented
      ph.update(now);
      const bool ok = ph.calibratePoint(atoi(argv[1]), date);
      Serial.printf("%s pH%s point mv=%.1f date=%s\n", ok ? "OK" : "ERR (voltage outside window?)",
                    argv[1], ph.voltageMv(now).valid ? ph.voltageMv(now).value : NAN, date);
    }
  } else if (!strcmp(cmd, "arm") && argc >= 2) {
    if (!strcmp(argv[1], "peltier")) {
#if FERMENTAI_ALLOW_PELTIER_OUTPUT || FERMENTAI_NOIO
      RomAddr a, b;
      if (argc < 3 || strcmp(argv[2], CHAMBER_ID) != 0) {
        Serial.printf("ERR confirm with: arm peltier %s\n", CHAMBER_ID);
      } else if (HEATSINK_PROBES_REQUIRED &&
                 (!ds.roleRom(ProbeRole::InternalHeatsink, a) || !ds.roleRom(ProbeRole::ExternalHeatsink, b))) {
        Serial.println("ERR assign both heatsink probes first");
      } else {
        nvs.putFlag("arm_peltier", true);
        peltier.setArmed(true);
        Serial.println("OK peltier ARMED (persisted). Stay with the device.");
        if (!HEATSINK_PROBES_REQUIRED && (!ds.roleRom(ProbeRole::InternalHeatsink, a) ||
                                          !ds.roleRom(ProbeRole::ExternalHeatsink, b)))
          Serial.println("WARNING no heatsink probe: firmware cannot see the hot side. Hardware thermostats "
                         "on BOTH heatsinks and a fused supply are the only over-temp protection.");
      }
#else
      Serial.println("ERR firmware built with FERMENTAI_ALLOW_PELTIER_OUTPUT=0 (see BRINGUP step 9)");
#endif
    } else if (!strcmp(argv[1], "motor")) {
      if (!motorPtr) {
        Serial.println("ERR MOTOR_ENABLED=0");
      } else {
        nvs.putFlag("arm_motor", true);
        motorPtr->setArmed(true);
        Serial.println("OK motor ARMED (persisted)");
      }
    }
  } else if (!strcmp(cmd, "disarm") && argc >= 2) {
    if (!strcmp(argv[1], "peltier")) {
      nvs.putFlag("arm_peltier", false);
      peltier.setArmed(false);
      Serial.println("OK peltier disarmed");
    } else if (!strcmp(argv[1], "motor") && motorPtr) {
      nvs.putFlag("arm_motor", false);
      motorPtr->setArmed(false);
      Serial.println("OK motor disarmed");
    }
  } else if (!strcmp(cmd, "motor") && argc == 3 && !strcmp(argv[1], "test")) {
    char why[64] = {0};
    uint32_t granted = 0;
    const bool ok = csm.startMotor((uint32_t)atol(argv[2]), "spin", "console-test", -1, now, &granted, why,
                                   sizeof why);
    if (ok)
      Serial.printf("OK running %lu ms\n", (unsigned long)granted);
    else
      Serial.printf("ERR %s\n", why);
  } else if (!strcmp(cmd, "setpoint") && argc == 2) {
    localSetpoint((float)atof(argv[1]), now);
    Serial.printf("OK setpoint in force: %.2f C (local limits %.0f-%.0f)\n", csm.experiment().setpoint(),
                  limits::kSetpointMinC, limits::kSetpointMaxC);
  } else if (!strcmp(cmd, "wifi") && argc == 3) {
    nvs.putString("wifi_ssid", argv[1]);
    nvs.putString("wifi_pass", argv[2]);
    Serial.println("OK saved; reboot to apply");
  } else if (!strcmp(cmd, "backend") && argc == 2) {
    nvs.putString("backend_url", argv[1]);
    Serial.println("OK saved; reboot to apply");
  } else if (!strcmp(cmd, "token") && argc == 2) {
    nvs.putString("api_token", argv[1]);
    Serial.println("OK saved; reboot to apply");
  } else if (!strcmp(cmd, "pin") && argc == 2) {
    // Read-only diagnostic: digital level of a GPIO (does not change the pin mode).
    const int p = atoi(argv[1]);
    if (p < 0 || p > 48) {
      Serial.println("ERR gpio 0-48");
    } else {
      // 2000 samples over ~20 ms: shows PWM duty, not just one instant.
      int hi = 0;
      for (int i = 0; i < 2000; ++i) {
        hi += (int)gpio_get_level((gpio_num_t)p);
        delayMicroseconds(10);
      }
      Serial.printf("GPIO%d level=%d duty_high=%.1f%%\n", p, hi > 1000 ? 1 : 0, hi / 20.0f);
    }
  } else if (!strcmp(cmd, "pwmdiag")) {
#if FERMENTAI_ALLOW_PELTIER_OUTPUT && !FERMENTAI_NOIO
    // Signal-path check for the Peltier PWM with the BTS7960 ENABLE held LOW (no load current).
    // Refused while armed so it can never fight the controller.
    if (peltier.armed()) {
      Serial.println("ERR disarm peltier first");
    } else {
      peltierEn.write(false);
      auto measure = [](int p) {
        int hi = 0;
        for (int i = 0; i < 2000; ++i) { hi += (int)gpio_get_level((gpio_num_t)p); delayMicroseconds(10); }
        return hi / 20.0f;
      };
      Serial.printf("pwmdiag EN(GPIO%d)=%d\n", pins::kPeltierEnablePin, (int)gpio_get_level((gpio_num_t)pins::kPeltierEnablePin));
      for (int k = 0; k < 2; ++k) {
        LedcPwmOut& o = k == 0 ? pwmA : pwmB;
        const int pin = k == 0 ? pins::kPeltierPwmA : pins::kPeltierPwmB;
        const int ch = k == 0 ? 2 : 3;
        o.setFraction(0.30f);
        delay(5);
        Serial.printf("  ch%d GPIO%d: set 30%% ledcRead=%lu freq=%lu Hz func_out_sel=%lu measured_high=%.1f%%\n", ch, pin,
                      (unsigned long)ledcRead(ch), (unsigned long)ledcReadFreq(ch),
                      (unsigned long)GPIO.func_out_sel_cfg[pin].func_sel, measure(pin));
        o.setFraction(0.0f);
      }
    }
#else
    Serial.println("ERR needs FERMENTAI_ALLOW_PELTIER_OUTPUT=1 hardware build");
#endif
  } else if (!strcmp(cmd, "owtest")) {
    // Read-only-ish diagnostic for the OneWire line: a 480 us reset pulse, then sample the line as
    // it recovers. No internal pull-up is enabled, so the recovery shows the EXTERNAL 4.7k.
    //   2us=1              -> external pull-up present
    //   2us=0, 460us=0     -> no pull-up (line floats / stays low)
    //   60-200us=0 with 2us=1 -> a DS18B20 presence pulse: a probe is answering
    const int p = pins::kOneWire;
    pinMode(p, INPUT);
    const int idle = digitalRead(p);
    noInterrupts();
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
    delayMicroseconds(480);
    pinMode(p, INPUT);
    delayMicroseconds(2);
    const int r2 = digitalRead(p);
    delayMicroseconds(58);
    const int r60 = digitalRead(p);
    delayMicroseconds(60);
    const int r120 = digitalRead(p);
    delayMicroseconds(80);
    const int r200 = digitalRead(p);
    delayMicroseconds(260);
    const int r460 = digitalRead(p);
    interrupts();
    Serial.printf("owtest GPIO%d idle=%d release+2us=%d +60us=%d +120us=%d +200us=%d +460us=%d\n", p, idle,
                  r2, r60, r120, r200, r460);
    if (!r2 && !r460)
      Serial.println("  -> NO PULL-UP seen: fit 4.7k between the data pin and 3V3 (or data wire is on the wrong pin)");
    else if (r2 && (!r60 || !r120 || !r200))
      Serial.println("  -> pull-up OK and a device answered (presence pulse)");
    else if (r2)
      Serial.println("  -> pull-up OK but NO device answers: check probe power (red=3V3, black=GND) and the data wire");
    else
      Serial.println("  -> ambiguous, repeat");
  } else if (!strcmp(cmd, "owsearch")) {
    // Raw OneWire ROM search on a temporary bus object: prints every address found, with its
    // CRC result and family code, so a rejected clone / corrupt ROM is visible.
    OneWire ow(pins::kOneWire);
    Serial.printf("owsearch GPIO%d reset/presence=%d\n", pins::kOneWire, (int)ow.reset());
    uint8_t a[8];
    int n = 0;
    ow.reset_search();
    delay(2);
    while (ow.search(a)) {
      const bool crc_ok = OneWire::crc8(a, 7) == a[7];
      Serial.printf("  rom %02x%02x%02x%02x%02x%02x%02x%02x crc=%s family=0x%02x%s\n", a[0], a[1], a[2], a[3],
                    a[4], a[5], a[6], a[7], crc_ok ? "ok" : "BAD",
                    a[0], a[0] == 0x28 ? " (DS18B20)" : a[0] == 0x10 ? " (DS18S20)" : a[0] == 0x22 ? " (DS1822)" : " (unknown)");
      if (++n >= 16) break;
    }
    ow.reset_search();
    Serial.printf("owsearch: %d address(es)\n", n);
  } else if (!strcmp(cmd, "i2cscan")) {
#if FERMENTAI_NOIO
    Serial.println("ERR no I2C in NOIO build");
#else
    int found = 0;
    for (uint8_t a = 0x08; a < 0x78; ++a) {
      Wire.beginTransmission(a);
      if (Wire.endTransmission() == 0) {
        Serial.printf("  i2c 0x%02x ack\n", a);
        ++found;
      }
    }
    Serial.printf("i2cscan: %d device(s) on SDA=%d SCL=%d\n", found, pins::kI2cSda, pins::kI2cScl);
#endif
  } else if (!strcmp(cmd, "reboot")) {
    ESP.restart();
  } else {
    Serial.println("ERR unknown command (try: help)");
  }
}

void tConsole(uint32_t, void*) {
  static char buf[160];
  static size_t n = 0;
  while (Serial.available()) {
    const int c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      buf[n] = 0;
      handleLine(buf);
      n = 0;
    } else if (n < sizeof buf - 1) {
      buf[n++] = (char)c;
    }
  }
}

void tStatusLog(uint32_t now, void*) { printStatus(now); }

// ------------------------------------------------------------------ network task (core 0)
void buildTelemetry(TelemetryRecord& r, uint32_t now) {
  // netTask-private copies: the snapshot buffers may be rewritten by the loop after unlock.
  static char exp[65], cmd[65];
  {
    std::lock_guard<std::mutex> l(g.m);
    r = g.snap;
    snprintf(exp, sizeof exp, "%s", g.snap_exp);
    snprintf(cmd, sizeof cmd, "%s", g.snap_cmd);
    r.experiment_id = exp[0] ? exp : nullptr;
    r.last_command_id = cmd[0] ? cmd : nullptr;
    r.timestamp_epoch_ms = g.clock.nowEpochMs(now);
    r.timestamp_quality = g.clock.quality(now, cfg::kClockEstimatedAfterMs);
  }
  r.device_id = DEVICE_ID;
  r.boot_id = bootId;
  r.firmware_version = FERMENTAI_FIRMWARE_VERSION;
  r.uptime_ms = uptimeMs();
  r.simulated = FERMENTAI_NOIO;
  r.ph_method = PH_METHOD;
  r.fan_state = -1;  // fans hard-wired on their own branch: not observable by firmware
  r.wifi_rssi_valid = net && net->connected();
  r.wifi_rssi_dbm = net ? net->rssi() : 0;
  r.free_heap_bytes = ESP.getFreeHeap();
#if CAMERA_ENABLED
  r.obs.setHealth("camera", camera.health());
#endif
  if ((resetReason == ESP_RST_BROWNOUT) && now < 600000) r.quality_flags.add("brownout_reset");
  if ((resetReason == ESP_RST_TASK_WDT || resetReason == ESP_RST_INT_WDT || resetReason == ESP_RST_WDT ||
       resetReason == ESP_RST_PANIC) && now < 600000)
    r.quality_flags.add("watchdog_or_panic_reset");
}

#if CAMERA_ENABLED && !FERMENTAI_NOIO
void uploadImage(uint32_t now, const char* cmd_id, int64_t seq) {
  const uint8_t* buf = nullptr;
  size_t len = 0;
  bool ok = false;
  char detail[96] = "capture failed";
  if (camera.capture(&buf, &len)) {
    char q[256];
    char ts[24] = {0};
    char exp[65] = {0};
    {
      std::lock_guard<std::mutex> l(g.m);
      const int64_t e = g.clock.nowEpochMs(now);
      if (e >= 0) formatIso8601Utc(e, ts, sizeof ts);
      snprintf(exp, sizeof exp, "%s", g.snap_exp);
    }
    int k = snprintf(q, sizeof q, "/api/v1/images?chamber_id=%s&source=%s", CHAMBER_ID,
                     sourceName(FERMENTAI_NOIO, kProfile));
    if (kProfile == ContractProfile::V1_1)
      k += snprintf(q + k, sizeof q - k, "&device_id=%s&device_uptime_ms=%llu", DEVICE_ID,
                    (unsigned long long)uptimeMs());
    if (ts[0]) k += snprintf(q + k, sizeof q - k, "&timestamp_utc=%s", ts);
    if (exp[0]) snprintf(q + k, sizeof q - k, "&experiment_id=%s", exp);
    std::string resp;
    const int st = (len <= 2000000) ? net->request("POST", q, "image/jpeg", buf, len, &resp,
                                                   cfg::kImageUploadTimeoutMs)
                                    : 413;
    camera.release();
    ok = httpOk(st);
    snprintf(detail, sizeof detail, ok ? "uploaded %u bytes" : "upload failed: HTTP %d", ok ? (unsigned)len : (unsigned)st);
    if (!ok) log_w("image upload: %s %s", detail, resp.c_str());
  }
  if (cmd_id && cmd_id[0]) {
    AckRecord a;
    snprintf(a.command_id, sizeof a.command_id, "%s", cmd_id);
    a.status = ok ? AckStatus::Completed : AckStatus::Failed;
    a.action = CmdAction::CaptureImage;
    a.sequence_number = seq;
    snprintf(a.message, sizeof a.message, "%s", detail);
    a.uptime_ms = millis();
    {
      std::lock_guard<std::mutex> l(g.m);
      a.epoch_ms = g.clock.nowEpochMs(a.uptime_ms);
    }
    acks.pushAck(a);
  }
}
#endif

void netTask(void*) {
  uint32_t last_poll = 0, last_tel = 0, last_cam = 0;
  for (;;) {
    const uint32_t now = millis();
    net->maintain(now);
    const bool up = net->connected();

    if (up && (uint32_t)(now - last_poll) >= cfg::kCommandPollPeriodMs) {
      last_poll = now;
      DesiredControlParse* p = new (std::nothrow) DesiredControlParse();
      if (p && commands->poll(millis(), *p)) {
        if (xQueueSend(cmdQueue, &p, 0) != pdTRUE) delete p;  // loop busy: next poll re-serves
      } else {
        delete p;
      }
    }
    if (up) commands->drainAcks(acks, millis());

    if ((uint32_t)(now - last_tel) >= cfg::kTelemetryPeriodMs) {
      last_tel = now;
      bool have;
      {
        std::lock_guard<std::mutex> l(g.m);
        have = g.snap_valid;
      }
      if (have) {
        TelemetryRecord r;
        buildTelemetry(r, now);
        telemetry->enqueue(r, kProfile);  // queued even while offline (bounded)
      }
    }
    if (up) telemetry->drain(millis());

#if CAMERA_ENABLED && !FERMENTAI_NOIO
    bool req = false;
    char cmd_id[65] = {0};
    int64_t seq = -1;
    {
      std::lock_guard<std::mutex> l(g.m);
      if (g.image_req) {
        req = true;
        g.image_req = false;
        snprintf(cmd_id, sizeof cmd_id, "%s", g.image_cmd);
        seq = g.image_seq;
      }
    }
    if (up && camera.available() && (req || (uint32_t)(now - last_cam) >= camera.settings().interval_ms)) {
      last_cam = now;
      uploadImage(now, req ? cmd_id : nullptr, seq);
    } else if (req) {
      AckRecord a;
      snprintf(a.command_id, sizeof a.command_id, "%s", cmd_id);
      a.status = AckStatus::Failed;
      a.action = CmdAction::CaptureImage;
      a.sequence_number = seq;
      snprintf(a.message, sizeof a.message, "%s", up ? "camera unavailable" : "offline");
      a.uptime_ms = millis();
      acks.pushAck(a);
    }
#else
    (void)last_cam;
#endif
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

void loadNetConfig() {
  snprintf(netCfg.ssid, sizeof netCfg.ssid, "%s", FERMENTAI_WIFI_SSID);
  snprintf(netCfg.pass, sizeof netCfg.pass, "%s", FERMENTAI_WIFI_PASS);
  snprintf(netCfg.base_url, sizeof netCfg.base_url, "%s", FERMENTAI_BACKEND_URL);
  snprintf(netCfg.token, sizeof netCfg.token, "%s", FERMENTAI_API_TOKEN);
  nvs.getString("wifi_ssid", netCfg.ssid, sizeof netCfg.ssid);
  nvs.getString("wifi_pass", netCfg.pass, sizeof netCfg.pass);
  nvs.getString("backend_url", netCfg.base_url, sizeof netCfg.base_url);
  nvs.getString("api_token", netCfg.token, sizeof netCfg.token);
  const size_t n = strlen(netCfg.base_url);
  if (n && netCfg.base_url[n - 1] == '/') netCfg.base_url[n - 1] = 0;
  netCfg.device_id = DEVICE_ID;
}

}  // namespace

void setup() {
  holdOutputsSafe();  // FIRST: power-control pins LOW
  resetReason = esp_reset_reason();
  Serial.begin(115200);
  delay(50);
  snprintf(bootId, sizeof bootId, "%08lx", (unsigned long)esp_random());
  Serial.printf("\nFermentAI %s chamber=%s device=%s boot=%s reset=%s noio=%d peltier_output_compiled=%d\n",
                FERMENTAI_FIRMWARE_VERSION, CHAMBER_ID, DEVICE_ID, bootId, resetName(resetReason),
                FERMENTAI_NOIO, FERMENTAI_ALLOW_PELTIER_OUTPUT);
  Serial.println("GPIO map is PROVISIONAL (docs/GPIO_MAP.md). Type 'help' for the console.");
#if !FERMENTAI_NOIO
  Serial.print("boot pin levels before drive, plain/with-pulldown (1/1 = board pull-up):");
  for (int i = 0; i < BootPinSnapshot::kN; ++i)
    Serial.printf(" GPIO%d=%u/%u", g_bootPins.gpio[i], (unsigned)g_bootPins.level[i],
                  (unsigned)g_bootPins.level_pd[i]);
  Serial.printf("\npin map: %s (peltier_en=GPIO%d motor=GPIO%d) heatsink_probes_required=%d\n",
                FERMENTAI_PINMAP_ALT ? "ALT" : "default", pins::kPeltierEnablePin, pins::kMotorPin,
                HEATSINK_PROBES_REQUIRED);
#endif

  nvs.begin();
  const uint32_t now = millis();

#if FERMENTAI_NOIO
  owBus.addProbe(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x01}}, 21.0f);
  owBus.addProbe(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x02}}, 21.0f);
  owBus.addProbe(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x03}}, 21.0f);
  envSensor.raw = EnvRaw{21.0f, 70.0f, 101325.0f, 100000.0f, true, true};
#else
  Wire.begin(pins::kI2cSda, pins::kI2cScl, 100000);
  owBus.begin();
  phAdc.begin();
  doorIn.begin();
#if FERMENTAI_ALLOW_PELTIER_OUTPUT
  peltierEn.begin();
  pwmA.begin();
  pwmB.begin();
#endif
#if MOTOR_ENABLED
  motorGate.begin();
  motorCutoff.begin();
#endif
#endif

  sensors.begin(now);
#if FERMENTAI_NOIO
  // Simulated probes: assign roles explicitly (as an operator would with `assign`).
  ds.assignRole(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x01}}, ProbeRole::Starter);
  ds.assignRole(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x02}}, ProbeRole::InternalHeatsink);
  ds.assignRole(RomAddr{{0x28, 0x51, 0x4d, 0x00, 0x00, 0x00, 0x00, 0x03}}, ProbeRole::ExternalHeatsink);
#endif
  csm.begin(now);  // Peltier off, motor off, waits for sensors + a fresh command

  // Arming: compile-time switch AND persisted commissioning flag. noio drives mocks only.
#if FERMENTAI_NOIO
  peltier.setArmed(true);
  if (motorPtr) motorPtr->setArmed(true);
#else
  peltier.setArmed(FERMENTAI_ALLOW_PELTIER_OUTPUT && nvs.getFlag("arm_peltier", false));
  if (motorPtr) motorPtr->setArmed(nvs.getFlag("arm_motor", false));
#endif

#if CAMERA_ENABLED && !FERMENTAI_NOIO
  const bool cam_ok = camera.begin();
  csm.setCameraAvailable(cam_ok);
  Serial.printf("camera: %s\n", cam_ok ? "ok" : "FAILED (control continues without it)");
#endif

  loadNetConfig();
  static NetworkManager netMgr(netCfg);
  static TelemetryClient telClient(&netMgr, psramFound() ? 50 : 10, cfg::kHttpTimeoutMs);
  static CommandClient cmdClient(&netMgr, CHAMBER_ID, DEVICE_ID, kProfile, cfg::kHttpTimeoutMs,
                                 cfg::kAckMaxAttempts);
  net = &netMgr;
  telemetry = &telClient;
  commands = &cmdClient;
  auto syncClock = [](int64_t epoch_ms) {
    std::lock_guard<std::mutex> l(g.m);
    g.clock.syncFrom(epoch_ms, millis());
  };
  telemetry->onServerTime = syncClock;
  commands->onServerTime = [](int64_t epoch_ms, uint32_t received_ms) {
    std::lock_guard<std::mutex> l(g.m);
    g.clock.syncFrom(epoch_ms, received_ms);
  };
  auto logReject = [](int st, const std::string& body) { log_w("backend rejected (HTTP %d): %s", st, body.c_str()); };
  telemetry->onRejected = logReject;
  commands->onRejected = logReject;
  if (!net->configured()) Serial.println("network: not configured (console: wifi/backend/token)");
  net->begin();

  cmdQueue = xQueueCreate(4, sizeof(DesiredControlParse*));
  xTaskCreatePinnedToCore(netTask, "net", 12288, nullptr, 1, nullptr, 0);

  sched.add("door", cfg::kDoorPeriodMs, tDoor, nullptr);
  sched.add("motor", cfg::kMotorCheckPeriodMs, tMotor, nullptr);
  sched.add("safety", cfg::kSafetyPeriodMs, tSafety, nullptr);
  sched.add("env", 100, tEnv, nullptr);  // self-paced to kEnvPeriodMs
  sched.add("ds18b20", 250, tDs, nullptr);  // self-paced to kDs18b20PeriodMs
  sched.add("ph", cfg::kPhPeriodMs, tPh, nullptr, 1000);
  sched.add("control", cfg::kControlPeriodMs, tControl, nullptr, 500);
  sched.add("commands", 50, tCommands, nullptr);
  sched.add("snapshot", cfg::kSnapshotPeriodMs, tSnapshot, nullptr);
  sched.add("console", 50, tConsole, nullptr);
  sched.add("statuslog", 30000, tStatusLog, nullptr, 5000);
  ds.setPeriod(cfg::kDs18b20PeriodMs);

  enableLoopWDT();  // a hung loop resets the chip -> boots with outputs off
}

void loop() {
  sched.tick(millis());
  delay(1);  // yield; the scheduler keeps its own timing
}
