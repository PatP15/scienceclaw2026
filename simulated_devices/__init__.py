"""FermentAI virtual ESP32 chamber devices (SIMULATED).

Software stand-ins for the two chamber controllers (A and B) so the backend, dashboard and agents
can be exercised before physical hardware exists. Everything they send is labelled
``source="simulated"`` (or ``"replay"`` in replay mode) with quality flag ``simulated_device``.

IMPORTANT: the thermal plant in :mod:`simulated_devices.virtual_thermal_plant` is a
*controller-test model* with hand-picked time constants. It is NOT a validated physical
simulation of the real chambers and its numbers must never be presented as measurements.

Run:  uv run python -m simulated_devices --chambers A B --base-url http://127.0.0.1:8000
"""

FIRMWARE_VERSION = "virtual-0.1"
DEVICE_IDS = {"A": "fermentai-esp32-a", "B": "fermentai-esp32-b"}

__all__ = ["DEVICE_IDS", "FIRMWARE_VERSION"]
