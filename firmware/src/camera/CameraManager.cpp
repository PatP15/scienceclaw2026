#include "camera/CameraManager.h"
#include "device_config.h"

#if defined(ARDUINO) && !FERMENTAI_NOIO && CAMERA_ENABLED
#include <Arduino.h>
#include <esp_camera.h>

#include "board_config.h"

namespace fa {

bool CameraManager::begin() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;  // Peltier PWM uses LEDC channels 2/3 (timer 1)
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = pins::kCamY2;
  c.pin_d1 = pins::kCamY3;
  c.pin_d2 = pins::kCamY4;
  c.pin_d3 = pins::kCamY5;
  c.pin_d4 = pins::kCamY6;
  c.pin_d5 = pins::kCamY7;
  c.pin_d6 = pins::kCamY8;
  c.pin_d7 = pins::kCamY9;
  c.pin_xclk = pins::kCamXclk;
  c.pin_pclk = pins::kCamPclk;
  c.pin_vsync = pins::kCamVsync;
  c.pin_href = pins::kCamHref;
  c.pin_sccb_sda = pins::kCamSiod;
  c.pin_sccb_scl = pins::kCamSioc;
  c.pin_pwdn = pins::kCamPwdn;
  c.pin_reset = pins::kCamReset;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = (framesize_t)s_.frame_size;
  c.jpeg_quality = s_.jpeg_quality;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (psramFound()) {
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.fb_count = 2;
  } else {
    c.fb_location = CAMERA_FB_IN_DRAM;
    c.fb_count = 1;
    if (c.frame_size > FRAMESIZE_VGA) c.frame_size = FRAMESIZE_VGA;
  }
  const esp_err_t err = esp_camera_init(&c);
  ok_ = (err == ESP_OK);
  if (!ok_) {
    log_e("esp_camera_init failed: 0x%x", (unsigned)err);
    return false;
  }
  ever_ok_ = true;
  applySettings();
  consecutive_failures_ = 0;
  return true;
}

void CameraManager::applySettings() {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return;
  s->set_exposure_ctrl(s, s_.auto_exposure ? 1 : 0);
  s->set_aec2(s, s_.auto_exposure ? 1 : 0);
  s->set_ae_level(s, s_.ae_level);
  s->set_gain_ctrl(s, s_.auto_gain ? 1 : 0);
  s->set_whitebal(s, s_.auto_white_balance ? 1 : 0);
  s->set_awb_gain(s, s_.auto_white_balance ? 1 : 0);
  s->set_brightness(s, s_.brightness);
}

SensorHealth CameraManager::health() const {
  if (!ever_ok_) return SensorHealth::Missing;
  if (!ok_ || consecutive_failures_ >= 3) return SensorHealth::Invalid;
  return SensorHealth::Ok;
}

void CameraManager::maybeReinit() {
  const uint32_t now = millis();
  if (consecutive_failures_ < 3 || (now - last_reinit_ms_) < 60000) return;
  last_reinit_ms_ = now;
  log_w("camera: %lu consecutive failures, re-initialising", (unsigned long)consecutive_failures_);
  esp_camera_deinit();
  ok_ = false;
  begin();
}

bool CameraManager::capture(const uint8_t** buf, size_t* len) {
  if (fb_) release();
  if (!ok_) {
    ++consecutive_failures_;
    maybeReinit();
    return false;
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb || fb->format != PIXFORMAT_JPEG || fb->len < 4 || fb->buf[0] != 0xFF || fb->buf[1] != 0xD8) {
    if (fb) esp_camera_fb_return(fb);
    ++consecutive_failures_;
    maybeReinit();
    return false;
  }
  fb_ = fb;
  *buf = fb->buf;
  *len = fb->len;
  consecutive_failures_ = 0;
  ++captures_;
  return true;
}

void CameraManager::release() {
  if (fb_) esp_camera_fb_return(static_cast<camera_fb_t*>(fb_));
  fb_ = nullptr;
}

}  // namespace fa

#else  // camera disabled / noio / native: inert stub

namespace fa {
bool CameraManager::begin() {
  ever_ok_ = false;
  fb_ = nullptr;
  last_reinit_ms_ = 0;
  return ok_ = false;
}
SensorHealth CameraManager::health() const { return SensorHealth::Disabled; }
bool CameraManager::capture(const uint8_t**, size_t*) { return false; }
void CameraManager::release() {}
void CameraManager::applySettings() {}
void CameraManager::maybeReinit() {}
}  // namespace fa

#endif
