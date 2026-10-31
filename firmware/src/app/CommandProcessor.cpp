#include "app/CommandProcessor.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "device_config.h"

namespace fa {

uint32_t CommandProcessor::hashOf(const char* id, char kind) {
  uint32_t h = 2166136261u;  // FNV-1a
  h = (h ^ (uint8_t)kind) * 16777619u;
  for (const char* p = id; *p; ++p) h = (h ^ (uint8_t)*p) * 16777619u;
  return h ? h : 1u;
}

char CommandProcessor::kindOf(const DeviceCommand& c) {
  switch (c.action) {
    case CmdAction::SetSetpoint: return 'C';
    case CmdAction::AnimateMotor: return 'R';
    case CmdAction::StopMotor: return c.flat_stop ? 'S' : 's';
    case CmdAction::CaptureImage: return 'I';
    case CmdAction::Noop: return 'N';
    default: return 'U';
  }
}

float CommandProcessor::clampSetpoint(float c, bool* clamped) {
  const float v = clampf(c, limits::kSetpointMinC, limits::kSetpointMaxC);
  if (clamped) *clamped = (v != c);
  return v;
}

void CommandProcessor::begin() {
  for (auto& s : ring_) s = Seen();
  ring_next_ = 0;
  for (auto& h : highest_) h = -1;
  epoch_[0] = 0;
  if (!kv_) return;
  // Motor replay guard survives reboot: never re-run the last motor command after a restart.
  kv_->getString("cmd_epoch", epoch_, sizeof epoch_);
  int64_t v;
  if (kv_->getI64("mot_seq_flat", v)) highest_[(size_t)CmdChannel::FlatMotor] = v;
  char id[65];
  if (kv_->getString("mot_last_r", id, sizeof id) && id[0]) remember(hashOf(id, 'R'));
}

CommandProcessor::Seen* CommandProcessor::find(uint32_t h) {
  for (auto& s : ring_)
    if (s.used && s.hash == h) return &s;
  return nullptr;
}

CommandProcessor::Seen& CommandProcessor::remember(uint32_t h) {
  Seen& s = ring_[ring_next_];
  ring_next_ = (ring_next_ + 1) % kRingSize;
  s = Seen();
  s.used = true;
  s.hash = h;
  return s;
}

bool CommandProcessor::isExpired(const DeviceCommand& c, uint32_t now_ms, const ClockSync& clk,
                                 bool* unverifiable) const {
  *unverifiable = false;
  if (c.has_ttl) return c.ttl_ms <= 0 || (int64_t)(uint32_t)(now_ms - c.received_ms) >= c.ttl_ms;
  if (c.expires_epoch_ms >= 0 && clk.hasSync()) return clk.nowEpochMs(now_ms) >= c.expires_epoch_ms;
  *unverifiable = true;
  return true;
}

void CommandProcessor::ack(const DeviceCommand& c, AckStatus st, uint32_t now_ms, const ClockSync& clk,
                           const char* msg, Seen* seen, bool has_sp, float sp) {
  AckRecord a;
  strncpy(a.command_id, c.command_id, sizeof a.command_id - 1);
  a.status = st;
  a.action = c.action;
  a.sequence_number = c.sequence_number;
  a.has_applied_setpoint = has_sp;
  a.applied_setpoint_c = sp;
  if (msg) strncpy(a.message, msg, sizeof a.message - 1);
  a.uptime_ms = now_ms;
  a.epoch_ms = clk.nowEpochMs(now_ms);
  if (seen) {
    seen->ack = a;
    seen->ack_ms = now_ms;
  }
  if (sink_) sink_->pushAck(a);
}

void CommandProcessor::persistMotor(const DeviceCommand& c) {
  if (!kv_) return;
  if (c.action == CmdAction::AnimateMotor) kv_->putString("mot_last_r", c.command_id);
  if (c.channel == CmdChannel::FlatMotor && c.sequence_number >= 0)
    kv_->putI64("mot_seq_flat", c.sequence_number);
}

void CommandProcessor::noteControl(const DeviceCommand& c, uint32_t now_ms) {
  strncpy(last_command_id_, c.command_id, sizeof last_command_id_ - 1);
  if (c.sequence_number >= 0) last_seq_ = c.sequence_number;
  (void)now_ms;
}

void CommandProcessor::handle(const DesiredControlParse& p, uint32_t now_ms, const ClockSync& clk) {
  if (!p.ok) return;
  if (p.server_epoch[0] && strcmp(p.server_epoch, epoch_) != 0) {
    // Server database was reset: old sequence high-water marks are meaningless.
    for (auto& h : highest_) h = -1;
    strncpy(epoch_, p.server_epoch, sizeof epoch_ - 1);
    if (kv_) {
      kv_->putString("cmd_epoch", epoch_);
      kv_->remove("mot_seq_flat");
    }
  }
  for (size_t i = 0; i < p.n; ++i) process(p.cmds[i], now_ms, clk);
}

void CommandProcessor::process(const DeviceCommand& c, uint32_t now_ms, const ClockSync& clk) {
  const char kind = kindOf(c);
  const uint32_t h = hashOf(c.command_id, kind);
  bool unverifiable = false;
  const bool expired = isExpired(c, now_ms, clk, &unverifiable);

  // ---- replay
  if (Seen* s = find(h)) {
    ++stats_.replays;
    // Re-delivery of a still-valid control command proves the link + command are alive.
    if (c.action == CmdAction::SetSetpoint && !expired && s->ack.status == AckStatus::Accepted) {
      has_valid_control_ = true;
      last_valid_control_ms_ = now_ms;
    }
    if (!c.flat && (uint32_t)(now_ms - s->ack_ms) >= kReAckIntervalMs && s->ack.command_id[0]) {
      s->ack_ms = now_ms;
      s->ack.uptime_ms = now_ms;
      ++stats_.reacks;
      if (sink_) sink_->pushAck(s->ack);
    }
    return;
  }

  Seen& seen = remember(h);

  // ---- chamber binding
  if (c.chamber_id[0] && strcmp(c.chamber_id, chamber_id_) != 0) {
    ++stats_.rejected;
    ack(c, AckStatus::Rejected, now_ms, clk, "wrong chamber", &seen);
    return;
  }

  // ---- stop is always honoured (safe direction)
  if (c.action == CmdAction::StopMotor) {
    char stopped[65] = {0};
    const bool was = target_ && target_->stopMotor(now_ms, stopped, sizeof stopped);
    ++stats_.executed;
    if (c.flat_stop) {
      // v1.0: ack the cancelled run command id with "cancelled" (once).
      ack(c, AckStatus::Cancelled, now_ms, clk, was ? "stopped" : "not running", &seen);
    } else {
      ack(c, AckStatus::Completed, now_ms, clk, was ? "motor stopped" : "motor was not running", &seen);
      if (was && stopped[0] && strcmp(stopped, c.command_id) != 0) {
        DeviceCommand cc;
        strncpy(cc.command_id, stopped, sizeof cc.command_id - 1);
        cc.action = CmdAction::AnimateMotor;
        ack(cc, AckStatus::Cancelled, now_ms, clk, "cancelled by stop_motor", nullptr);
      }
    }
    return;
  }

  // ---- sequence (replay of an older command)
  const size_t ch = (size_t)c.channel;
  // v1.0 flat channels: ignore numbers LOWER than the highest applied (re-delivery keeps the
  // same number). v1.1 array: one counter per chamber, ignore numbers <= the highest processed.
  const bool stale_seq = c.sequence_number >= 0 && highest_[ch] >= 0 &&
                         (c.channel == CmdChannel::Array ? c.sequence_number <= highest_[ch]
                                                         : c.sequence_number < highest_[ch]);
  if (stale_seq) {
    ++stats_.stale_sequence;
    if (!c.flat) ack(c, AckStatus::Rejected, now_ms, clk, "stale sequence_number", &seen);
    return;
  }

  // ---- expiry
  if (expired) {
    ++stats_.expired;
    if (unverifiable)
      ack(c, AckStatus::Rejected, now_ms, clk, "no usable expiry (ttl_s/expires_at_utc)", &seen);
    else
      ack(c, AckStatus::Expired, now_ms, clk, "expired before execution", &seen);
    return;
  }

  if (c.sequence_number >= 0 && c.sequence_number > highest_[ch]) highest_[ch] = c.sequence_number;
  if (c.experiment_id[0]) strncpy(experiment_id_, c.experiment_id, sizeof experiment_id_ - 1);

  switch (c.action) {
    case CmdAction::SetSetpoint: {
      if (!c.has_setpoint || !isfinite(c.setpoint_c)) {
        ++stats_.rejected;
        ack(c, AckStatus::Rejected, now_ms, clk, "missing or invalid setpoint", &seen);
        return;
      }
      bool clamped = false;
      const float sp = clampSetpoint(c.setpoint_c, &clamped);
      if (target_) target_->applySetpoint(sp, c.control_mode, experimentId(), now_ms);
      has_valid_control_ = true;
      last_valid_control_ms_ = now_ms;
      noteControl(c, now_ms);
      ++stats_.executed;
      char msg[96] = {0};
      if (clamped) snprintf(msg, sizeof msg, "clamped locally from %.2f to %.2f C", (double)c.setpoint_c, (double)sp);
      ack(c, AckStatus::Accepted, now_ms, clk, clamped ? msg : nullptr, &seen, true, sp);
      return;
    }
    case CmdAction::AnimateMotor: {
      // Persist BEFORE running so a crash/reboot mid-run can never replay it.
      persistMotor(c);
      noteControl(c, now_ms);
      char why[64] = {0};
      uint32_t granted = 0;
      if (c.duration_ms == 0) {
        ++stats_.rejected;
        ack(c, AckStatus::Rejected, now_ms, clk, "duration must be > 0", &seen);
        return;
      }
      if (!target_ || !target_->startMotor(c.duration_ms, c.pattern, c.command_id, c.sequence_number,
                                           now_ms, &granted, why, sizeof why)) {
        ++stats_.rejected;
        ack(c, AckStatus::Rejected, now_ms, clk, why[0] ? why : "motor unavailable", &seen);
        return;
      }
      ++stats_.executed;
      char msg[96];
      if (granted < c.duration_ms)
        snprintf(msg, sizeof msg, "running %lu ms (clamped from %lu ms)", (unsigned long)granted,
                 (unsigned long)c.duration_ms);
      else
        snprintf(msg, sizeof msg, "running %lu ms", (unsigned long)granted);
      ack(c, AckStatus::Accepted, now_ms, clk, msg, &seen);
      return;
    }
    case CmdAction::CaptureImage: {
      char why[64] = {0};
      if (!target_ || !target_->requestImage(c.command_id, c.sequence_number, why, sizeof why)) {
        ++stats_.rejected;
        ack(c, AckStatus::Rejected, now_ms, clk, why[0] ? why : "camera unavailable", &seen);
        return;
      }
      ++stats_.executed;
      ack(c, AckStatus::Accepted, now_ms, clk, "capture queued", &seen);
      return;
    }
    case CmdAction::Noop:
      ++stats_.executed;
      ack(c, AckStatus::Completed, now_ms, clk, nullptr, &seen);
      return;
    default:
      ++stats_.rejected;
      ack(c, AckStatus::Rejected, now_ms, clk, "unsupported action", &seen);
      return;
  }
}

}  // namespace fa
