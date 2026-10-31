"""HTTP side of a virtual ESP32: auth headers, contract-mode shaping and v1.1 -> v1.0 fallback.

Contract modes
--------------
* ``v1.1`` (default): send every v1.0 field PLUS the v1.1 additions (boot_id, sequence_number,
  timestamp_quality, device_uptime_ms, heatsink split, pressure_pa, peltier_output_fraction,
  diagnostics, field_provenance; acks add device_id, device_uptime_ms, detail, result).
  ``schema_version`` stays "1.0" on the wire (v1.1 is additive).
* ``v1.0``: only the frozen v1.0 field set (firmware_contract/telemetry_example.json).

A v1.0-only server rejects unknown fields with 422 ``extra_forbidden``. With fallback enabled
(default) such a 422 that names a v1.1 field switches the shared ContractState to v1.0, the
same payload is re-sent immediately in v1.0 shape, and the switch is logged. Every
``reprobe_s`` seconds v1.1 is tried again (so a backend upgrade is picked up without restart).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("simulated_devices.net")

DEFAULT_TOKEN = "change-me-local-device-token"

# Frozen v1.0 telemetry field set (== keys of firmware_contract/telemetry_example.json).
V10_TELEMETRY_FIELDS: tuple[str, ...] = (
    "schema_version", "chamber_id", "experiment_id", "timestamp_utc", "chamber_temperature_c",
    "starter_temperature_c", "relative_humidity_percent", "gas_resistance_ohms", "ph", "ph_method",
    "starter_height_mm", "door_open", "peltier_mode", "temperature_setpoint_c", "motor_running",
    "source", "quality_flags", "device_id", "firmware_version", "uptime_s", "wifi_rssi_dbm",
    "heatsink_temperature_c", "fan_running", "peltier_duty_percent", "last_command_id",
    "last_sequence_number", "fault_codes",
)
V11_TELEMETRY_EXTRAS: tuple[str, ...] = (
    "boot_id", "sequence_number", "timestamp_quality", "device_uptime_ms",
    "internal_heatsink_temperature_c", "external_heatsink_temperature_c", "pressure_pa",
    "peltier_output_fraction", "diagnostics", "field_provenance",
)
V10_ACK_FIELDS: tuple[str, ...] = (
    "schema_version", "command_id", "chamber_id", "status", "sequence_number", "timestamp_utc",
    "applied_setpoint_c", "message",
)
V11_ACK_EXTRAS: tuple[str, ...] = ("device_id", "device_uptime_ms", "detail", "result")
V11_FIELD_NAMES = frozenset(V11_TELEMETRY_EXTRAS + V11_ACK_EXTRAS)


class ContractState:
    """Shared (per process / base URL) contract mode with automatic fallback."""

    def __init__(self, mode: str = "v1.1", *, fallback: bool = True, reprobe_s: float = 300.0,
                 clock=time.monotonic):
        if mode not in ("v1.0", "v1.1"):
            raise ValueError("contract mode must be v1.0 or v1.1")
        self.requested = mode
        self.mode = mode
        self.fallback = fallback
        self.reprobe_s = reprobe_s
        self.clock = clock
        self._fell_back_at: float | None = None
        self.switches: list[tuple[str, str]] = []
        log.info("contract mode: %s (fallback to v1.0 on extra-field 422: %s)", mode,
                 "on" if fallback else "off")

    def current(self) -> str:
        if (self.mode == "v1.0" and self.requested == "v1.1" and self._fell_back_at is not None
                and self.reprobe_s > 0 and self.clock() - self._fell_back_at >= self.reprobe_s):
            self._switch("v1.1", "re-probing v1.1 after fallback")
            self._fell_back_at = None
        return self.mode

    def _switch(self, mode: str, why: str) -> None:
        if mode != self.mode:
            log.warning("contract mode %s -> %s (%s)", self.mode, mode, why)
            self.switches.append((self.mode, mode))
            self.mode = mode

    def handle_422(self, body: Any) -> bool:
        """Return True if the 422 was caused by v1.1-only fields and we fell back to v1.0."""
        if self.mode != "v1.1" or not self.fallback:
            return False
        extra = mentions_v11_extra(body)
        if extra:
            self._switch("v1.0", f"server rejected v1.1 field {extra!r} (422)")
            self._fell_back_at = self.clock()
            return True
        return False

    # ---------------------------------------------------------------- shaping
    def shape_telemetry(self, full: dict) -> dict:
        if self.current() == "v1.1":
            return dict(full)
        return {k: full[k] for k in V10_TELEMETRY_FIELDS if k in full}

    def shape_ack(self, full: dict) -> dict:
        out = {k: full.get(k) for k in V10_ACK_FIELDS}
        if out.get("message") is None and full.get("detail"):
            out["message"] = full["detail"]
        if out.get("message"):
            out["message"] = str(out["message"])[:300]
        if self.current() == "v1.1":
            out.update({k: full.get(k) for k in V11_ACK_EXTRAS if full.get(k) is not None})
            if out.get("detail"):
                out["detail"] = str(out["detail"])[:300]
        return out


def mentions_v11_extra(body: Any) -> str | None:
    """Find a v1.1 field named in a 422 body (pydantic error list or free text)."""
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, list):
        for err in detail:
            if not isinstance(err, dict):
                continue
            loc = [str(x) for x in err.get("loc") or []]
            is_extra = err.get("type") == "extra_forbidden" or "extra" in str(err.get("msg", "")).lower()
            hit = next((x for x in loc if x in V11_FIELD_NAMES), None)
            if hit and is_extra:
                return hit
    text = json.dumps(body) if not isinstance(body, str) else body
    if "extra" in text.lower() or "not permitted" in text.lower():
        for name in sorted(V11_FIELD_NAMES):
            if name in text:
                return name
    return None


def token_candidates(chamber_id: str, explicit: str | None = None) -> list[str]:
    """Device-specific token first, then the shared LAN token, then the .env.example default."""
    out = []
    for t in (explicit, os.environ.get(f"FERMENTAI_DEVICE_TOKEN_{chamber_id.upper()}"),
              os.environ.get("FERMENTAI_API_TOKEN"), DEFAULT_TOKEN):
        if t and t.strip() and t.strip() not in out:
            out.append(t.strip())
    return out


@dataclass
class Result:
    ok: bool
    status: int | None           # None = network error
    body: Any = None
    error: str | None = None

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status >= 500


class DeviceHttpClient:
    """One device's view of the backend. Several devices may share one httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient, chamber_id: str, device_id: str,
                 contract: ContractState, tokens: list[str] | None = None):
        self.client = client
        self.chamber_id = chamber_id
        self.device_id = device_id
        self.contract = contract
        self.tokens = tokens or token_candidates(chamber_id)
        self._token_idx = 0

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[self._token_idx]}",
                "X-Device-Id": self.device_id}

    async def _request(self, method: str, path: str, **kw) -> Result:
        for _ in range(len(self.tokens)):
            try:
                r = await self.client.request(method, path, headers=self.headers, **kw)
            except httpx.HTTPError as e:
                return Result(False, None, error=f"{type(e).__name__}: {e}")
            try:
                body = r.json()
            except ValueError:
                body = r.text
            if r.status_code in (401, 403) and self._token_idx + 1 < len(self.tokens):
                self._token_idx += 1
                log.warning("[%s] %s %s -> %s; trying next configured token", self.chamber_id,
                            method, path, r.status_code)
                continue
            return Result(r.status_code < 400, r.status_code, body)
        return Result(False, 403, error="all tokens rejected")

    async def _post_shaped(self, path: str, full: dict, shaper) -> Result:
        res = await self._request("POST", path, json=shaper(full))
        if res.status == 422 and self.contract.handle_422(res.body):
            res = await self._request("POST", path, json=shaper(full))
        if not res.ok:
            log.warning("[%s] POST %s -> %s %s", self.chamber_id, path, res.status,
                        str(res.body or res.error)[:300])
        return res

    async def post_telemetry(self, full: dict) -> Result:
        return await self._post_shaped("/api/v1/telemetry", full, self.contract.shape_telemetry)

    async def post_ack(self, full: dict) -> Result:
        return await self._post_shaped("/api/v1/control-ack", full, self.contract.shape_ack)

    async def get_desired_control(self) -> Result:
        res = await self._request("GET", f"/api/v1/chambers/{self.chamber_id}/desired-control")
        if not res.ok:
            log.warning("[%s] desired-control -> %s %s", self.chamber_id, res.status,
                        str(res.body or res.error)[:300])
        return res

    async def post_image(self, jpeg: bytes, fields: dict[str, str]) -> Result:
        res = await self._request("POST", "/api/v1/images",
                                  files={"file": (f"sim_{self.chamber_id}.jpg", jpeg, "image/jpeg")},
                                  data=fields)
        if not res.ok:
            log.warning("[%s] POST /images -> %s %s", self.chamber_id, res.status,
                        str(res.body or res.error)[:300])
        return res
