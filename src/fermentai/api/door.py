"""Door -> chef-hat rule (configs/hardware.yaml door_motor).

On a telemetry transition door_open false->true the hat wiggles (bounded animate_motor, source
"door_event", through the normal motor path: cap, command ids, acks, SSE). The firmware's safety
interlock refuses the motor while the door is open ("ERR door open"); this module never tries to
get around it. If the open-triggered command is rejected for that reason (or the device is a legacy
fw 0.3.x console, which is known to refuse), ONE wiggle is deferred to the next true->false
transition. Cooldown applies to commands actually issued; a rejected one does not consume it.
Every door change emits an SSE `door` event {chamber_id, open, opens}.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from .devices import CommandError

if TYPE_CHECKING:
    from .devices import ControlService

log = logging.getLogger("fermentai.api.door")
SOURCE = "door_event"


class DoorRule:
    def __init__(self, svc: ControlService):
        self.svc = svc
        self._lock = threading.Lock()
        self.deferred: set[str] = set()
        self.last_fired: dict[str, datetime] = {}
        self._prev_fired: dict[str, datetime | None] = {}
        self.door_now: dict[str, bool] = {}

    @property
    def cfg(self):
        return self.svc.hw.door_motor

    def previous_door(self, chamber_id: str) -> bool | None:
        r = self.svc.db.query_one(
            "SELECT door_open FROM telemetry WHERE chamber_id=? ORDER BY id DESC LIMIT 1", (chamber_id,))
        return None if r is None or r["door_open"] is None else bool(r["door_open"])

    def on_telemetry(self, chamber_id: str, prev: bool | None, door_open: bool | None, opens: int | None,
                     legacy: bool) -> None:
        if door_open is None:
            return
        self.door_now[chamber_id] = door_open
        if prev is None or prev == door_open:
            return
        self.svc.db.emit("door", {"chamber_id": chamber_id, "open": door_open, "opens": opens},
                         chamber_id)
        cfg = self.cfg
        if not cfg.enabled:
            return
        with self._lock:
            if door_open:
                if legacy:
                    # legacy console + open door: the interlock will refuse it; wait for close
                    if cfg.retry_on_close_if_rejected:
                        self.deferred.add(chamber_id)
                    return
                self._fire(chamber_id, "door opened")
            elif chamber_id in self.deferred:
                self.deferred.discard(chamber_id)
                self._fire(chamber_id, "door closed (deferred wiggle)")

    def on_ack(self, chamber_id: str, source: str | None, status: str, message: str | None) -> None:
        if source != SOURCE or status != "rejected":
            return
        if "door open" not in (message or "").lower() or not self.cfg.retry_on_close_if_rejected:
            return
        with self._lock:
            prev = self._prev_fired.get(chamber_id)          # a refused command doesn't use the cooldown
            if prev is None:
                self.last_fired.pop(chamber_id, None)
            else:
                self.last_fired[chamber_id] = prev
            if self.door_now.get(chamber_id, True):
                self.deferred.add(chamber_id)
            else:                                            # already closed again: do it now
                self._fire(chamber_id, "door closed (retry after rejection)")

    def _fire(self, chamber_id: str, why: str) -> dict | None:
        now = self.svc.clock()
        last = self.last_fired.get(chamber_id)
        if last is not None and (now - last).total_seconds() < self.cfg.cooldown_s:
            log.info("door motor skipped for %s: cooldown", chamber_id)
            return None
        try:
            cmd = self.svc.issue_motor_command(chamber_id, self.cfg.duration_ms / 1000.0, self.cfg.pattern,
                                               why, SOURCE, supersede=False)
        except CommandError as e:
            log.warning("door motor not issued: %s", e)
            return None
        self._prev_fired[chamber_id] = last
        self.last_fired[chamber_id] = now
        return cmd
