"""End to end over a pty pair: virtual ESP32 (simulated_devices, --transport serial) on the slave
side <-> fermentai.bridge on the master side <-> the real FastAPI app (in-process ASGI).
Never touches a real USB port."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fermentai.api.testing import TEST_TOKEN, auth, make_app
from fermentai.bridge import PortSpec
from fermentai.bridge.testing import BridgeHarness, asgi_client, close_fds, pty_pair, wait_for
from simulated_devices import chamber_A
from simulated_devices.device import VirtualChamber
from simulated_devices.network_client import ContractState
from simulated_devices.transport_serial import SerialDeviceTransport

REPO = Path(__file__).resolve().parents[1]


async def start_virtual_device(slave_path: str):
    cfg = chamber_A.config(telemetry_interval_s=0.4, poll_interval_s=1.0, images_enabled=False,
                           use_fermentation_model=False)
    tr = SerialDeviceTransport("A", cfg.device_id, ContractState("v1.1", fallback=False), port=slave_path)
    dev = VirtualChamber(cfg, tr)
    tr.attach(dev)
    await tr.start()
    stop = asyncio.Event()
    task = asyncio.create_task(dev.run(stop=stop, tick_s=0.05))
    return dev, tr, stop, task


async def stop_virtual_device(tr, stop, task):
    stop.set()
    await asyncio.wait_for(task, 5)
    await tr.close()


async def test_virtual_device_over_serial_end_to_end(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, slave = pty_pair()
    status_path = tmp_path / "data" / "telemetry" / "bridge_status.json"
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="pty-A")], operator_token=TEST_TOKEN,
                                 status_path=status_path) as h:
            os.write(s, b"ESP-ROM:esp32s3-20210327\r\nrst:0x1 (POWERON),boot:0x8\r\n{not json at all\n\xff\xfe\n")
            dev, tr, dstop, dtask = await start_virtual_device(slave)

            async def tel():
                st = (await api.get("/api/v1/chambers/A/state")).json()
                t = st["latest_telemetry"]
                return t if t and t["boot_id"] == dev.boot_id else None
            t = await wait_for(tel, what="telemetry via serial")
            assert t["source"] == "simulated" and t["device_id"] == "fermentai-esp32-a"
            assert t["sequence_number"] >= 1 and t["device_uptime_ms"] is not None
            sess = h.session()
            assert sess.counters["rx_log_text"] >= 3            # boot noise passed through as logs
            assert sess.hello and sess.simulated is True and tr.hello_acked

            # setpoint command delivered + acked
            await wait_for(lambda: dev.setpoint_c == 22.0, what="setpoint applied on device")

            # motor round trip: queued -> command line -> device runs -> completed ack -> ran
            r = await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 500})
            mid = r.json()["command_id"]

            async def motor_done():
                cmds = (await api.get("/api/v1/commands?action=animate_motor")).json()
                c = next((c for c in cmds if c["command_id"] == mid), None)
                return c if c and c["ran"] else None
            c = await wait_for(motor_done, what="motor completed ack")
            assert [a["status"] for a in c["acks"]] == ["accepted", "completed"]
            assert sess.counters["commands_sent"] >= 2 and sess.counters["ack_ok"] >= 3

            # capture_image -> base64 chunks -> reassembled upload -> completed ack
            cap = (await api.post("/api/v1/chambers/A/capture-image", headers=auth())).json()

            async def captured():
                cmds = (await api.get("/api/v1/commands?action=capture_image")).json()
                return any(x["command_id"] == cap["command_id"] and x["status"] == "completed" for x in cmds)
            await wait_for(captured, what="capture_image completed")
            imgs = (await api.get("/api/v1/images?chamber_id=A")).json()
            assert imgs and imgs[0]["source"] == "simulated" and imgs[0]["bytes"] > 1000
            assert sess.counters["images_ok"] == 1 and sess.counters["image_errors"] == 0

            # status file + API
            await wait_for(lambda: status_path.is_file(), what="status file")
            st = (await api.get("/api/v1/bridge/status")).json()
            assert st["transport"] == "usb-serial" and st["bridge_running"] is True
            assert st["chambers"]["A"]["link"] == "connected" and st["chambers"]["A"]["label"] == "USB · connected"
            assert st["chambers"]["B"]["link"] == "not_attached"

            # unplug: device goes away -> disconnected
            await stop_virtual_device(tr, dstop, dtask)
            close_fds(s)
            await wait_for(lambda: sess.state == "disconnected", what="disconnect detected")
            assert sess.counters["disconnects"] == 1
            await asyncio.sleep(0.5)
            assert h.bridge.status()["chambers"]["A"]["link"] == "disconnected"

            # replug (new boot) on the same port -> recovers
            dev2, tr2, dstop2, dtask2 = await start_virtual_device(slave)
            assert dev2.boot_id != dev.boot_id

            async def tel2():
                t = (await api.get("/api/v1/chambers/A/state")).json()["latest_telemetry"]
                return t["boot_id"] == dev2.boot_id
            await wait_for(tel2, what="telemetry after replug")
            assert sess.state == "connected" and sess.boot_id == dev2.boot_id
            await stop_virtual_device(tr2, dstop2, dtask2)
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_simulator_cli_serial_transport(tmp_path):
    """`python -m simulated_devices --transport serial --port <pty>` as a real subprocess."""
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, slave = pty_pair()
    env = {**os.environ, "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "simulated_devices", "--chambers", "A", "--transport", "serial",
         "--port", slave, "--interval", "0.5", "--duration", "6", "--no-images", "--no-fermentation-model"],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="pty-cli")], operator_token=TEST_TOKEN) as h:
            async def tel():
                t = (await api.get("/api/v1/chambers/A/state")).json()["latest_telemetry"]
                return t if t and t["source"] == "simulated" else None
            await wait_for(tel, timeout=20, what="telemetry from simulator subprocess")
            await wait_for(lambda: h.session().simulated is True, what="hello from subprocess")
            out, _ = await asyncio.to_thread(proc.communicate, timeout=20)
            assert proc.returncode == 0, out.decode()[-2000:]
            assert b"over USB-serial" in out
    finally:
        if proc.poll() is None:
            proc.kill()
        await api.aclose()
        close_fds(m, s)


def test_simulator_cli_serial_requires_port(tmp_path):
    from simulated_devices.cli import main
    assert main(["--chambers", "A", "--transport", "serial", "--duration", "0.1"]) == 2


@pytest.mark.parametrize("argv", [["--replay", "x.jsonl", "--transport", "serial", "--port", "/dev/null"]])
def test_simulator_cli_serial_rejects_http_replay(argv):
    from simulated_devices.cli import main
    assert main(["--chambers", "A", *argv]) == 2
