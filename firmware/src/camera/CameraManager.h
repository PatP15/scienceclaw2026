// CameraManager — OV2640 via the Espressif esp32-camera driver (bundled with Arduino-ESP32).
// Runs ONLY in the network task (core 0): capture and upload can never delay the safety loop.
// Memory-aware: frame buffers in PSRAM, CAMERA_GRAB_LATEST, buffer returned immediately after
// upload. Error recovery: after 3 consecutive failed captures the driver is de-initialised and
// re-initialised (at most once per 60 s); a camera failure never touches temperature control.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "fermentai_types.h"

namespace fa {

struct CameraSettings {
  int frame_size = 9;     // framesize_t: 9 = FRAMESIZE_SVGA (800x600); 8 = VGA; 10 = XGA
  int jpeg_quality = 12;  // 0-63, lower = better/larger (contract: aim <= 200 KB)
  int ae_level = 0;       // -2..2 exposure compensation
  int brightness = 0;     // -2..2
  bool auto_exposure = true;
  bool auto_gain = true;
  bool auto_white_balance = true;
  uint32_t interval_ms = 60000;
};

class CameraManager {
 public:
  explicit CameraManager(const CameraSettings& s) : s_(s) {}
  bool begin();
  bool available() const { return ok_; }
  SensorHealth health() const;
  // Capture one JPEG. On success *buf/*len point into a driver frame buffer that MUST be
  // released with release() before the next capture.
  bool capture(const uint8_t** buf, size_t* len);
  void release();
  uint32_t failures() const { return consecutive_failures_; }
  uint32_t captures() const { return captures_; }
  const CameraSettings& settings() const { return s_; }

 private:
  void applySettings();
  void maybeReinit();
  CameraSettings s_;
  bool ok_ = false;
  bool ever_ok_ = false;
  void* fb_ = nullptr;
  uint32_t consecutive_failures_ = 0;
  uint32_t captures_ = 0;
  uint32_t last_reinit_ms_ = 0;
};

}  // namespace fa
