"""Bearer-token auth (contract v1.1).

Credentials (documented in contracts/README.md, firmware_contract/README_FOR_ESP32_AGENT.md and
docs/network_setup.md):

* Operator token: env FERMENTAI_API_TOKEN. Used by the dashboard, agents and Mac-local
  simulators. Valid on every write endpoint and may act as any device.
* Device tokens (optional): env FERMENTAI_DEVICE_TOKEN_A, FERMENTAI_DEVICE_TOKEN_B (one per
  chamber in configs/hardware.yaml). Valid ONLY on device endpoints (telemetry, images,
  desired-control, control-ack) and ONLY for their own chamber. If a chamber has no device
  token configured, its device uses the operator token (v1.0 behaviour).
* Header `X-Device-Id` (optional, recommended): identifies the device; must match the body's
  `device_id` when both are given and must be bound to the chamber (see devices.py).

Read-only GETs (state, history, events, data files) stay unauthenticated for the LAN dashboard.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class Principal:
    kind: str                     # "operator" | "device"
    chamber_id: str | None = None  # device tokens: the only chamber this caller may touch
    device_id: str | None = None   # from X-Device-Id


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token: send 'Authorization: Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return supplied.strip()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _header_device_id(request: Request) -> str | None:
    d = request.headers.get("x-device-id")
    return d.strip()[:64] if d and d.strip() else None


def require_operator(request: Request) -> Principal:
    """Operator token only (dashboard / agents / tools)."""
    token = _bearer(request)
    s = request.app.state.settings
    if _eq(token, s.api_token):
        return Principal("operator", None, _header_device_id(request))
    if any(_eq(token, t) for t in s.device_tokens.values()):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "device tokens may only call device endpoints")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bearer token",
                        headers={"WWW-Authenticate": "Bearer"})


def require_device(request: Request) -> Principal:
    """Device endpoints: a chamber's device token, or the operator token."""
    token = _bearer(request)
    s = request.app.state.settings
    for cid, t in s.device_tokens.items():
        if _eq(token, t):
            return Principal("device", cid, _header_device_id(request))
    if _eq(token, s.api_token):
        return Principal("operator", None, _header_device_id(request))
    raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bearer token",
                        headers={"WWW-Authenticate": "Bearer"})


# v1.0 name kept for compatibility.
require_token = require_operator
