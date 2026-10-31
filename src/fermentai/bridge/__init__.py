"""USB-serial transport between the ESP32 chambers and the local FastAPI backend.

    uv run python -m fermentai.bridge --base-url http://127.0.0.1:8000 [--port /dev/cu.usbmodemXXXX=A ...]

Protocol: contracts/serial_v1.md. The API, DB, dashboard and contracts are unchanged; the bridge
is just another (trusted, Mac-local) HTTP client that speaks for the USB-attached devices.
"""

from .bridge import Bridge, BridgeConfig, PortSpec  # noqa: F401
from .protocol import PROTOCOL_NAME, PROTOCOL_VERSION  # noqa: F401
