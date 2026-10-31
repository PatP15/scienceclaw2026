"""Chef-hat "thinking" pulses while an agent is working (configs/hardware.yaml thinking_pulses).

    with ThinkingPulses(svc, ["A"], "chat") as tp:
        result = slow_agent_call()
    tp.finish(clear_for_final_wiggle=True)

A background thread issues short, bounded `animate_motor` commands (source "thinking") through
the normal motor path (same max-duration cap, command ids, acks and SSE `command` events) at most
once per (pulse_ms + min_gap_s) per station, max `max_pulses_per_cycle` per call. No pulse is
issued after the reply returns. finish(clear_for_final_wiggle=True) withdraws pulses not yet
picked up and cancels a running one, so the hand-off wiggle that follows is not refused as
"motor already running". Never touches the Peltier / setpoint path.
"""

from __future__ import annotations

import logging
import threading
import time

from .devices import CommandError, ControlService

log = logging.getLogger("fermentai.api.thinking")


class ThinkingPulses:
    def __init__(self, svc: ControlService, chambers: list[str], label: str):
        self.svc = svc
        self.cfg = svc.hw.thinking_pulses
        self.chambers = [c for c in dict.fromkeys(chambers) if c in svc.hw.chambers]
        self.label = label
        self.issued: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # per-station last pulse time, shared across calls (rate limit across turns)
        if not hasattr(svc, "_thinking_last"):
            svc._thinking_last = {}
        self.enabled = bool(self.cfg.motor_on_thinking and self.chambers and self.cfg.pulse_ms > 0
                            and self.cfg.max_pulses_per_cycle > 0)

    @property
    def pulse_s(self) -> float:
        return min(self.cfg.pulse_ms / 1000.0, self.svc.hw.motor.max_duration_s)

    @property
    def period_s(self) -> float:
        return self.pulse_s + max(0.0, self.cfg.min_gap_s)

    def __enter__(self) -> ThinkingPulses:
        if self.enabled:
            self._thread = threading.Thread(target=self._run, name=f"thinking-{self.label}", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._halt()

    def _halt(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        counts = dict.fromkeys(self.chambers, 0)
        last = self.svc._thinking_last
        while not self._stop.is_set():
            now = time.monotonic()
            waits = []
            for c in self.chambers:
                if counts[c] >= self.cfg.max_pulses_per_cycle:
                    continue
                due = last.get(c, -1e9) + self.period_s
                if now >= due:
                    try:
                        cmd = self.svc.issue_motor_command(
                            c, self.pulse_s, self.cfg.pattern, f"sous chef thinking ({self.label})",
                            "thinking", supersede=False)
                        self.issued.append(cmd)
                        counts[c] += 1
                        last[c] = now
                        due = now + self.period_s
                    except CommandError as e:      # misconfiguration: never break the agent call
                        log.warning("thinking pulse not issued: %s", e)
                        counts[c] = self.cfg.max_pulses_per_cycle
                        continue
                if counts[c] < self.cfg.max_pulses_per_cycle:
                    waits.append(max(0.01, due - now))
            if not waits:
                return
            self._stop.wait(min(waits))

    def finish(self, *, clear_for_final_wiggle: bool = False) -> list[str]:
        """Stop pulsing. Optionally withdraw unpicked pulses and cancel a running one."""
        self._halt()
        ids = [c["command_id"] for c in self.issued]
        if not clear_for_final_wiggle or not ids:
            return ids
        self.svc.withdraw_motor_commands(ids, f"reply ready ({self.label})")
        for cid in ids:
            row = self.svc.db.query_one("SELECT status FROM commands WHERE command_id=?", (cid,))
            if row and row["status"] == "accepted":           # running on the device right now
                try:
                    self.svc.cancel_command(cid)
                except CommandError:
                    pass
        return ids
