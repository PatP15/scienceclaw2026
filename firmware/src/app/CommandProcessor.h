// CommandProcessor — validates and executes backend commands, with replay and expiry protection.
//
//  * Replay: a ring buffer of (command_id, kind) hashes already handled. A repeated v1.0 flat
//    command is a silent no-op (contract: "don't re-ack, don't re-run"). A repeated v1.1 array
//    command is not re-executed; its last ack is re-sent at most every 15 s so the server can
//    retire it.
//  * Sequence: per channel (v1.0 control, v1.0 motor, v1.1 array). v1.0 channels ignore numbers
//    lower than the highest applied; the v1.1 array (one counter per chamber across all actions,
//    contracts/README.md) ignores numbers <= the highest processed. server_epoch change resets.
//  * The flat fields and commands[] carry the SAME command ids for the same command; the
//    (id, kind) ring makes whichever arrives second a no-op.
//  * Motor replay across reboot: the last executed motor command id + sequence are persisted.
//  * Expiry: deadline = receipt time + TTL on the monotonic clock (see command_schema.h). A
//    command without any usable expiry reference is rejected. Expired -> ack "expired" once.
//  * Setpoints are clamped to [kSetpointMinC, kSetpointMaxC] (device_config.h) BEFORE use,
//    whatever was received; NaN/missing setpoints are rejected.
//  * stop_motor is always executed (stopping is the safe direction), even if expired.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "command_schema.h"
#include "hal_interfaces.h"
#include "util/TimeUtil.h"

namespace fa {

class ICommandTarget {
 public:
  virtual ~ICommandTarget() = default;
  virtual void applySetpoint(float clamped_c, ControlMode mode, const char* experiment_id,
                             uint32_t now_ms) = 0;
  // Returns false with a reason if the motor cannot run (door open, disarmed, fault...).
  virtual bool startMotor(uint32_t duration_ms, const char* pattern, const char* command_id,
                          int64_t seq, uint32_t now_ms, uint32_t* granted_ms, char* why,
                          size_t why_n) = 0;
  // Stops the motor. Returns true if it was running; *stopped_id = the running command id.
  virtual bool stopMotor(uint32_t now_ms, char* stopped_id, size_t n) = 0;
  // Queue a camera capture. false + reason if the camera is disabled/unavailable.
  virtual bool requestImage(const char* command_id, int64_t seq, char* why, size_t why_n) = 0;
};

class IAckSink {
 public:
  virtual ~IAckSink() = default;
  virtual bool pushAck(const AckRecord& a) = 0;
};

class CommandProcessor {
 public:
  static constexpr size_t kRingSize = 32;
  static constexpr uint32_t kReAckIntervalMs = 15000;

  CommandProcessor(const char* chamber_id, IKeyValueStore* kv, ICommandTarget* target, IAckSink* sink)
      : chamber_id_(chamber_id), kv_(kv), target_(target), sink_(sink) {}

  void begin();
  // Process a whole desired-control response (server time is used for clock sync by caller).
  void handle(const DesiredControlParse& p, uint32_t now_ms, const ClockSync& clk);
  void process(const DeviceCommand& c, uint32_t now_ms, const ClockSync& clk);

  static float clampSetpoint(float c, bool* clamped);

  bool hasValidControl() const { return has_valid_control_; }
  uint32_t lastValidControlMs() const { return last_valid_control_ms_; }
  const char* lastCommandId() const { return last_command_id_; }
  int64_t lastSequence() const { return last_seq_; }
  const char* experimentId() const { return experiment_id_[0] ? experiment_id_ : nullptr; }

  struct Stats {
    uint32_t executed = 0, replays = 0, stale_sequence = 0, expired = 0, rejected = 0, reacks = 0;
  };
  const Stats& stats() const { return stats_; }

 private:
  struct Seen {
    uint32_t hash = 0;
    AckRecord ack;  // last ack sent for this command (for v1.1 re-ack)
    uint32_t ack_ms = 0;
    bool used = false;
  };
  static uint32_t hashOf(const char* id, char kind);
  static char kindOf(const DeviceCommand& c);
  Seen* find(uint32_t h);
  Seen& remember(uint32_t h);
  bool isExpired(const DeviceCommand& c, uint32_t now_ms, const ClockSync& clk, bool* unverifiable) const;
  void ack(const DeviceCommand& c, AckStatus st, uint32_t now_ms, const ClockSync& clk,
           const char* msg, Seen* seen, bool has_sp = false, float sp = 0.0f);
  void persistMotor(const DeviceCommand& c);
  void noteControl(const DeviceCommand& c, uint32_t now_ms);

  const char* chamber_id_;
  IKeyValueStore* kv_;
  ICommandTarget* target_;
  IAckSink* sink_;
  Seen ring_[kRingSize];
  size_t ring_next_ = 0;
  int64_t highest_[kNumChannels] = {-1, -1, -1};
  char epoch_[33] = {0};
  bool has_valid_control_ = false;
  uint32_t last_valid_control_ms_ = 0;
  char last_command_id_[65] = {0};
  int64_t last_seq_ = -1;
  char experiment_id_[65] = {0};
  Stats stats_;
};

}  // namespace fa
