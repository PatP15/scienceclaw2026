// SerialLink — byte-stream port abstraction for SerialTransport (USB CDC / UART on the device,
// an in-memory stub in native tests). Header-only.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace fa {

class ISerialPort {
 public:
  virtual ~ISerialPort() = default;
  virtual int available() = 0;                               // bytes ready to read (non-blocking)
  virtual int read() = 0;                                    // one byte, or -1
  virtual size_t write(const uint8_t* data, size_t len) = 0; // whole line in ONE call (atomic per call)
};

}  // namespace fa

#if defined(ARDUINO)
#include <Arduino.h>

namespace fa {

// Wraps any Arduino Stream: `Serial` (UART0 via the board's USB-UART bridge when
// ARDUINO_USB_CDC_ON_BOOT=0, or native USB CDC when =1), `Serial1` on spare pins, `USBSerial`.
// Arduino-ESP32 2.x serialises each write() call under the UART lock, so a full line written in
// one call is never interleaved with other Serial.print output (it can only land between lines).
class ArduinoStreamPort : public ISerialPort {
 public:
  explicit ArduinoStreamPort(Stream& s) : s_(s) {}
  int available() override { return s_.available(); }
  int read() override { return s_.read(); }
  size_t write(const uint8_t* data, size_t len) override { return s_.write(data, len); }

 private:
  Stream& s_;
};

}  // namespace fa
#endif
