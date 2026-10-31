"""Bearer-token auth for device and dashboard WRITE endpoints.

Model (documented in firmware_contract/README_FOR_ESP32_AGENT.md and docs/network_setup.md):
* One shared LAN token, env FERMENTAI_API_TOKEN (default in .env.example; change it for the demo).
* Required on every POST/DELETE and on GET /chambers/{id}/desired-control (which issues commands).
* Read-only GETs (state, telemetry history, events stream, data files) are unauthenticated so the
  iPad dashboard works on the isolated demo LAN without a login step.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


def require_token(request: Request) -> None:
    expected: str = request.app.state.settings.api_token
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token: send 'Authorization: Bearer <FERMENTAI_API_TOKEN>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(supplied.strip().encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "invalid bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
