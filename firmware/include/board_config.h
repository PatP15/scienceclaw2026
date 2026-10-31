// board_config.h — THE ONLY FILE THAT NAMES GPIO NUMBERS.
//
//  ██████  PROVISIONAL  ██████
//  None of these assignments has been verified on the physical Aideepen ESP32-S3-CAM board.
//  Compile success is NOT proof of electrical compatibility. See docs/GPIO_MAP.md for the
//  conflict analysis and the list of checks that must pass before any pin drives hardware.
//
// Board assumption: ESP32-S3-WROOM-1 N8R8/N16R8 (octal PSRAM) camera board in the Freenove
// "ESP32-S3 WROOM CAM" layout, OV2640 wired with the Espressif ESP32-S3-EYE pin set
// (Freenove camera_pins.h CAMERA_MODEL_ESP32S3_EYE). Verified only against vendor documentation,
// not against the physical board.
#pragma once

#include <stdint.h>

namespace fa {
namespace pins {

constexpr int kUnused = -1;

// ------------------------------------------------------------------ PROVISIONAL peripheral pins
// Proposed map from PLAN_MASTER §5.3. Status of each pin: docs/GPIO_MAP.md table 3.
constexpr int kI2cSda = 41;            // BME680 / SHT31 (JTAG MTDI after reset; free if USB-JTAG used)
constexpr int kI2cScl = 42;            // (JTAG MTMS)
constexpr int kOneWire = 47;           // DS18B20 bus, external 4.7k pull-up to 3.3 V
constexpr int kDoorReed = 39;          // reed switch to GND, internal pull-up (+ optional 10k ext)
                                       // CONFLICT: SD-card CLK on Freenove layout — leave SD slot empty
constexpr int kPhAdc = 1;              // ADC1_CH0 (ADC1 works with Wi-Fi on). Max 3.1 V at 11 dB atten.
constexpr int kPeltierPwmA = 14;       // BTS7960 RPWM
constexpr int kPeltierPwmB = 21;       // BTS7960 LPWM
constexpr int kPeltierEnable = 40;     // BTS7960 R_EN + L_EN, EXTERNAL 10k PULL-DOWN REQUIRED
                                       // CONFLICT: SD-card D0 on Freenove layout (board pull-up?)
constexpr int kMotorGate = 38;         // N20 logic-level MOSFET gate, EXTERNAL 100k PULL-DOWN REQUIRED
                                       // CONFLICT: SD-card CMD on Freenove layout (board pull-up?)
constexpr int kFanEnable = kUnused;    // fans hard-wired to their own protected 12 V branch
constexpr int kStatusLed = kUnused;    // GPIO2 (Freenove LED) — unverified, unused

// If the bring-up check finds board pull-ups on GPIO38/GPIO40 (docs/BRINGUP.md step 2), build with
// -DFERMENTAI_PINMAP_ALT=1 which moves the two power-control outputs off the SD-card lines.
#ifndef FERMENTAI_PINMAP_ALT
#define FERMENTAI_PINMAP_ALT 0
#endif
constexpr int kPeltierEnableAlt = 2;   // verify: Freenove LED on GPIO2 acts as a weak pull-down
constexpr int kMotorGateAlt = 48;      // verify: WS2812 data-in on some boards; no strap function
constexpr int kPeltierEnablePin = FERMENTAI_PINMAP_ALT ? kPeltierEnableAlt : kPeltierEnable;
constexpr int kMotorPin = FERMENTAI_PINMAP_ALT ? kMotorGateAlt : kMotorGate;

// ------------------------------------------------------------------ camera (OV2640, ESP32-S3-EYE set)
// Source: Freenove_ESP32_S3_WROOM_Board camera_pins.h, CAMERA_MODEL_ESP32S3_EYE.
constexpr int kCamPwdn = -1;
constexpr int kCamReset = -1;
constexpr int kCamXclk = 15;
constexpr int kCamSiod = 4;
constexpr int kCamSioc = 5;
constexpr int kCamY9 = 16;
constexpr int kCamY8 = 17;
constexpr int kCamY7 = 18;
constexpr int kCamY6 = 12;
constexpr int kCamY5 = 10;
constexpr int kCamY4 = 8;
constexpr int kCamY3 = 9;
constexpr int kCamY2 = 11;
constexpr int kCamVsync = 6;
constexpr int kCamHref = 7;
constexpr int kCamPclk = 13;

// ------------------------------------------------------------------ reserved / do-not-use
// SPI flash + octal PSRAM: 26-32 and 33-37 (35-37 = OPI PSRAM). USB D-/D+: 19/20.
// UART0: 43/44. Strapping: 0, 3, 45, 46. SD card (Freenove): 38 CMD, 39 CLK, 40 D0.

// ------------------------------------------------------------------ compile-time conflict checks
constexpr int kCameraPins[] = {kCamXclk, kCamSiod, kCamSioc, kCamY9, kCamY8, kCamY7, kCamY6,
                               kCamY5,   kCamY4,   kCamY3,   kCamY2, kCamVsync, kCamHref, kCamPclk};
constexpr int kReservedPins[] = {0, 3, 19, 20, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
                                 43, 44, 45, 46};

constexpr bool inList(int p, const int* list, int n) {
  return n == 0 ? false : (list[0] == p ? true : inList(p, list + 1, n - 1));
}
constexpr int kNumCam = sizeof(kCameraPins) / sizeof(kCameraPins[0]);
constexpr int kNumRes = sizeof(kReservedPins) / sizeof(kReservedPins[0]);
constexpr bool pinOk(int p) {
  return p == kUnused || (p >= 0 && p <= 48 && !inList(p, kCameraPins, kNumCam) &&
                          !inList(p, kReservedPins, kNumRes));
}
}  // namespace pins
}  // namespace fa

static_assert(fa::pins::pinOk(fa::pins::kI2cSda), "I2C SDA collides with camera/flash/strap pin");
static_assert(fa::pins::pinOk(fa::pins::kI2cScl), "I2C SCL collides with camera/flash/strap pin");
static_assert(fa::pins::pinOk(fa::pins::kOneWire), "OneWire collides with camera/flash/strap pin");
static_assert(fa::pins::pinOk(fa::pins::kDoorReed), "Reed collides with camera/flash/strap pin");
static_assert(fa::pins::pinOk(fa::pins::kPhAdc), "pH ADC collides with camera/flash/strap pin");
static_assert(fa::pins::pinOk(fa::pins::kPeltierPwmA), "Peltier A collides");
static_assert(fa::pins::pinOk(fa::pins::kPeltierPwmB), "Peltier B collides");
static_assert(fa::pins::pinOk(fa::pins::kPeltierEnablePin), "Peltier EN collides");
static_assert(fa::pins::pinOk(fa::pins::kMotorPin), "Motor collides");
static_assert(fa::pins::kPhAdc >= 1 && fa::pins::kPhAdc <= 10,
              "pH must be on ADC1 (GPIO1-10): ADC2 is unusable while Wi-Fi is on");
