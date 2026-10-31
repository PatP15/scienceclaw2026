// device_config.h — per-device identity, feature switches and LOCAL SAFETY LIMITS.
//
// Per-device values (CHAMBER_ID, DEVICE_ID, ENV_SENSOR, PH_SENSOR_ENABLED, CAMERA_ENABLED,
// MOTOR_ENABLED) come from platformio.ini build flags; the defaults below are only fallbacks.
//
// The limits in namespace fa::limits MUST match configs/hardware.yaml: firmware_local_limits.
// They are compiled in and are enforced whatever the backend, an agent or a judge sends.
#pragma once

#include <stdint.h>

// ------------------------------------------------------------------ identity
#ifndef CHAMBER_ID
#define CHAMBER_ID "A"
#endif
#ifndef DEVICE_ID
#define DEVICE_ID "fermentai-esp32-a"
#endif
#ifndef FERMENTAI_FIRMWARE_VERSION
#define FERMENTAI_FIRMWARE_VERSION "0.0.0-dev"
#endif

// ------------------------------------------------------------------ feature switches
#define ENV_SENSOR_NONE 0
#define ENV_SENSOR_BME680 1
#define ENV_SENSOR_SHT31 2
#define ENV_SENSOR_ENS160_AHT21 3   // combined breakout: AHT21 T/RH (0x38) + ENS160 TVOC/eCO2 (0x52/0x53)
#ifndef ENV_SENSOR
#define ENV_SENSOR ENV_SENSOR_BME680
#endif
#ifndef PH_SENSOR_ENABLED
#define PH_SENSOR_ENABLED 0
#endif
#ifndef CAMERA_ENABLED
#define CAMERA_ENABLED 0
#endif
#ifndef MOTOR_ENABLED
#define MOTOR_ENABLED 0
#endif
// 1 = no physical sensor/actuator I/O; mock drivers + test plant; source="simulated".
#ifndef FERMENTAI_NOIO
#define FERMENTAI_NOIO 0
#endif
// Compile-time master switch for the BTS7960 Peltier outputs. 0 = the PWM/enable pins are held
// LOW whatever the controller wants. Commissioning sets it to 1 (docs/BRINGUP.md step 9).
#ifndef FERMENTAI_ALLOW_PELTIER_OUTPUT
#define FERMENTAI_ALLOW_PELTIER_OUTPUT 0
#endif
// Contract profile (see telemetry_schema.h). 1 = v1.1 superset, 0 = strict v1.0.
#ifndef FERMENTAI_CONTRACT_V11
#define FERMENTAI_CONTRACT_V11 1
#endif
// Heatsink probes must be present and valid before the Peltier may be energised (default 1).
// Both chamber envs set 0 (operator decision 2026-10-31, no heatsink probes fitted): arming is then
// allowed without them and the heatsink-sensor fault is not raised. The 60 C over-temp trip still
// acts on any heatsink probe that IS assigned. Hardware thermostats are then the only protection.
#ifndef HEATSINK_PROBES_REQUIRED
#define HEATSINK_PROBES_REQUIRED 1
#endif
// pH method string reported with a valid probe reading (contract: probe_immersed|probe_sample|...)
#ifndef PH_METHOD
#define PH_METHOD "probe_sample"
#endif

namespace fa {
namespace limits {
// configs/hardware.yaml: firmware_local_limits  (keep in sync; test_commands checks 4/35)
constexpr float kSetpointMinC = 4.0f;
constexpr float kSetpointMaxC = 35.0f;
constexpr float kChamberCutoffHighC = 38.0f;
constexpr float kChamberCutoffLowC = 2.0f;
constexpr float kHeatsinkMaxC = 60.0f;
constexpr uint32_t kHeatCoolDeadTimeMs = 60UL * 1000UL;
constexpr uint32_t kFanOverrunMs = 30UL * 1000UL;
constexpr uint32_t kSensorStaleMs = 30UL * 1000UL;
constexpr uint32_t kOfflineHoldMs = 1800UL * 1000UL;
constexpr uint32_t kMotorMaxDurationMs = 10UL * 1000UL;
// Contract §5.2: over/under-temp fault latches until back in range this long.
constexpr uint32_t kFaultClearHoldMs = 60UL * 1000UL;
// Heatsink fault clears only below this (hysteresis) for kFaultClearHoldMs.
constexpr float kHeatsinkClearC = 50.0f;
// Plausibility window for any temperature reading (contract §5.3).
constexpr float kPlausibleMinC = -20.0f;
constexpr float kPlausibleMaxC = 80.0f;
}  // namespace limits

namespace cfg {
// Thermal control (engineering assumptions, tune during commissioning)
constexpr float kHysteresisC = 0.5f;          // contract minimum is 0.3 C
constexpr float kPeltierMaxFraction = 0.80f;  // PWM limit (fraction of full duty)
constexpr float kPeltierMinFraction = 0.15f;  // below this the module does little useful work
constexpr float kProportionalGainPerC = 0.25f;// fraction per degC of error (proportional mode)
constexpr float kRampPerSecond = 0.10f;       // max duty increase per second (soft start)
constexpr uint32_t kPeltierPwmHz = 20000;     // BTS7960 max ~25 kHz
constexpr uint8_t kPeltierPwmBits = 10;
// Which BTS7960 side heats the chamber interior. Verify with the polarity test (BRINGUP step 9).
constexpr bool kHeatOnChannelA = true;

// Scheduler periods (PLAN_MASTER §5.7)
constexpr uint32_t kSafetyPeriodMs = 200;
constexpr uint32_t kControlPeriodMs = 1000;
constexpr uint32_t kDs18b20PeriodMs = 2000;
constexpr uint32_t kEnvPeriodMs = 5000;
constexpr uint32_t kDoorPeriodMs = 20;
constexpr uint32_t kPhPeriodMs = 5000;
constexpr uint32_t kMotorCheckPeriodMs = 20;
constexpr uint32_t kTelemetryPeriodMs = 10000;
constexpr uint32_t kCommandPollPeriodMs = 2000;
constexpr uint32_t kCameraPeriodMs = 60000;
constexpr uint32_t kSnapshotPeriodMs = 500;

// Sensors
constexpr uint32_t kDoorDebounceMs = 60;
constexpr bool kDoorOpenLevelHigh = true;     // NO reed + pull-up: magnet present (closed) = LOW
constexpr uint32_t kDsStaleMs = 15000;
constexpr uint32_t kDsRediscoverMs = 30000;
constexpr uint32_t kEnvStaleMs = 20000;
constexpr uint32_t kBme680WarmupMs = 5UL * 60UL * 1000UL;
constexpr float kBme680TempOffsetC = 0.0f;    // heater self-heating correction; calibrate vs DS18B20
constexpr uint32_t kStuckValueMs = 15UL * 60UL * 1000UL;  // identical value this long -> stale
constexpr uint16_t kPhSamples = 16;
constexpr float kPhAdcMinMv = 50.0f;          // below/above: rail or saturation -> invalid
constexpr float kPhAdcMaxMv = 3000.0f;
constexpr float kPhMinSpanMv = 100.0f;        // |mV(pH7) - mV(pH4)| must exceed this

// Network
constexpr uint32_t kHttpTimeoutMs = 4000;
constexpr uint32_t kImageUploadTimeoutMs = 8000;
constexpr uint32_t kBackoffMaxMs = 30000;
constexpr uint32_t kClockEstimatedAfterMs = 60UL * 60UL * 1000UL;  // sync older than 1 h -> "estimated"
constexpr uint8_t kAckMaxAttempts = 6;

// Camera
constexpr int kCameraJpegQuality = 12;        // 0-63, lower = better (contract: ~12)
}  // namespace cfg
}  // namespace fa
