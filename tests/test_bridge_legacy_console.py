"""Legacy fw 0.3.x console mode: animate_motor -> `motor test <ms>`, console replies -> acks.

Console strings are the firmware's own (firmware/src/main.cpp handleLine + ChamberStateMachine::
startMotor): "OK running <ms> ms" | "ERR motor not armed (commissioning)" | "ERR motor already running"
| "ERR door open" | "ERR unknown command (try: help)". The console has no motor-stop command and does
not print when the motor finishes.
"""

import asyncio
import re

from fermentai.api.testing import TEST_TOKEN, auth, make_app
from fermentai.bridge import PortSpec
from fermentai.bridge.serial_link import AsyncLineChannel, SerialLink
from fermentai.bridge.testing import BridgeHarness, asgi_client, close_fds, pty_pair, wait_for

STATUS_BLOCK = [
    "[A fermentai-esp32-a] fw=0.3.0 boot=1185100b reset=other state=waiting_for_command mode=idle out=0.00 armed(peltier=0 motor=1)",
    "  chamber=28.60C rh=13.2% p=nanPa gas=nanohm starter=23.44C hs_int=nanC hs_ext=nanC",
    "  ph=nan mv=0.0 raw=0 cal=0 door=closed opens=0 setpoint=nan faults=0x0",
    "  ens160: present=1 addr=0x53 valid=1 warmup=0 aqi=0 tvoc=0 ppb eco2=0 ppm (indices, not CO2)",
    "  flags: ds18b20_role_unassigned ph_out_of_range",
    "  wifi=0 rssi=0 tel_q=0 acks_q=0 heap=239416 psram=8179891",
]


class StubLegacyConsole:
    """fw 0.3.0 console stand-in: prints status blocks, answers `motor test <ms>`."""

    def __init__(self, fd, reply=None):
        self.ch = AsyncLineChannel(SerialLink.from_fd(fd, owns_fd=False)).start()
        self.rx: list[str] = []
        self.busy_until = 0.0
        self.reply = reply or self.default_reply
        self.task = asyncio.create_task(self._run())

    def default_reply(self, ms: int) -> str:
        loop = asyncio.get_running_loop()
        if loop.time() < self.busy_until:
            return "ERR motor already running"
        self.busy_until = loop.time() + ms / 1000
        return f"OK running {ms} ms"

    async def _run(self):
        while True:
            line = await self.ch.readline()
            if line is None:
                return
            text = line.decode(errors="replace")
            self.rx.append(text)
            m = re.fullmatch(r"motor test (\d+)", text.strip())
            sp = re.fullmatch(r"setpoint ([-+\d.]+)", text.strip())
            if m:
                out = self.reply(int(m.group(1)))
            elif sp:   # firmware: localSetpoint() then this exact line (clamped to 4-35)
                v = min(max(float(sp.group(1)), 4.0), 35.0)
                out = f"OK setpoint in force: {v:.2f} C (local limits 4-35)"
            else:
                out = "ERR unknown command (try: help)"
            if out:
                await self.ch.write_line((out + "\r\n").encode())

    def motor_lines(self):
        return [x for x in self.rx if x.startswith("motor test")]

    async def status_block(self, armed_peltier: int = 0):
        for line in STATUS_BLOCK:
            line = line.replace("armed(peltier=0", f"armed(peltier={armed_peltier}")
            await self.ch.write_line((line + "\r\n").encode())

    async def close(self):
        self.task.cancel()
        await self.ch.close()


async def motor_row(api, cid):
    rows = (await api.get("/api/v1/commands?command_type=motor")).json()
    return next(r for r in rows if r["command_id"] == cid)


async def setup(tmp_path, **cfg):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    h = BridgeHarness(app, [PortSpec(fd=m, label="usbmodem-A", chamber_id="A")], operator_token=TEST_TOKEN, **cfg)
    await h.__aenter__()
    dev = StubLegacyConsole(s)
    await dev.status_block()
    await wait_for(lambda: h.session().protocol == "legacy_status_log", what="legacy mode")
    return app, api, h, dev, (m, s)


async def teardown(api, h, dev, fds):
    await dev.close()
    await h.__aexit__(None, None, None)
    await api.aclose()
    close_fds(*fds)


async def test_motor_test_accepted_then_completed(tmp_path):
    app, api, h, dev, fds = await setup(tmp_path)
    try:
        cid = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 400})).json()["command_id"]

        async def done():
            r = await motor_row(api, cid)
            return r if r["status"] == "completed" else None
        r = await wait_for(done, what="completed ack")
        assert [a["status"] for a in r["acks"]] == ["accepted", "completed"] and r["ran"] is True
        acc, comp = r["acks"][0]["payload"], r["acks"][1]["payload"]
        assert acc["detail"] == "OK running 400 ms" and acc["result"] == {"granted_ms": 400}
        assert comp["result"]["completion"] == "inferred_from_granted_duration" and comp["result"]["ran_ms"] == 400
        assert acc["device_id"] == "fermentai-esp32-a"
        # only console commands are ever written: `motor test <ms>` and `setpoint <C>`
        assert dev.motor_lines() == ["motor test 400"]
        await asyncio.sleep(0.8)                       # more polls: the rejected setpoint is not re-sent
        assert dev.motor_lines() == ["motor test 400"]
        assert [x for x in dev.rx if x.startswith("setpoint")] == ["setpoint 22.00"]
        assert all(re.fullmatch(r"motor test \d+|setpoint [\d.]+", x) for x in dev.rx)
    finally:
        await teardown(api, h, dev, fds)


async def test_refusals_become_rejected_with_console_text(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    replies = iter(["ERR motor not armed (commissioning)", "ERR door open"])
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="u", chamber_id="A")], operator_token=TEST_TOKEN) as h:
            dev = StubLegacyConsole(s, reply=lambda ms: next(replies))
            await dev.status_block()
            await wait_for(lambda: h.session().protocol == "legacy_status_log", what="legacy")
            for expected in ("ERR motor not armed (commissioning)", "ERR door open"):
                cid = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 3000})).json()["command_id"]

                async def rejected():
                    r = await motor_row(api, cid)
                    return r if r["status"] == "rejected" else None
                r = await wait_for(rejected, what="rejected ack")
                assert r["acks"][-1]["payload"]["detail"] == expected and r["ran"] is False
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_no_reply_is_failed(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="u", chamber_id="A")], operator_token=TEST_TOKEN,
                                 console_reply_timeout_s=0.4) as h:
            dev = StubLegacyConsole(s, reply=lambda ms: None)          # console says nothing
            await dev.status_block()
            await wait_for(lambda: h.session().protocol == "legacy_status_log", what="legacy")
            cid = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 3000})).json()["command_id"]

            async def failed():
                return (await motor_row(api, cid))["status"] == "failed"
            await wait_for(failed, what="failed ack")
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_busy_motor_queues_and_stop_waits_for_run_end(tmp_path):
    app, api, h, dev, fds = await setup(tmp_path)
    try:
        first = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 900})).json()["command_id"]

        async def accepted(cid):
            return (await motor_row(api, cid))["status"] == "accepted"
        await wait_for(lambda: accepted(first), what="first running")
        second = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 300})).json()["command_id"]

        async def completed(cid):
            return (await motor_row(api, cid))["status"] == "completed"
        await wait_for(lambda: completed(second), timeout=10, what="second after first")
        assert dev.motor_lines() == ["motor test 900", "motor test 300"]   # never "already running"
        assert (await motor_row(api, first))["status"] == "completed"

        # stop_motor: console has no stop -> no write, ack completed when the bounded run ends
        third = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 700})).json()["command_id"]
        await wait_for(lambda: accepted(third), what="third running")
        stop_id = (await api.delete(f"/api/v1/commands/{third}", headers=auth())).json()["stop_command_id"]

        async def stop_done():
            rows = (await api.get("/api/v1/commands?action=stop_motor")).json()
            return next(r for r in rows if r["command_id"] == stop_id)["status"] == "completed"
        await wait_for(stop_done, what="stop completed")
        assert dev.motor_lines() == ["motor test 900", "motor test 300", "motor test 700"]
    finally:
        await teardown(api, h, dev, fds)


async def test_duration_capped_and_disable_flag(tmp_path):
    app, api, h, dev, fds = await setup(tmp_path)
    try:
        sess = h.session()
        await sess._console_command({"command_id": "manual-1", "action": "animate_motor",
                                     "parameters": {"duration_ms": 25000}, "expires_at_utc": "2999-01-01T00:00:00Z"},
                                    "2026-10-31T12:00:00Z", asyncio.get_running_loop().time())
        await wait_for(dev.motor_lines, what="console write")
        assert dev.motor_lines() == ["motor test 10000"]
    finally:
        await teardown(api, h, dev, fds)

    app, api, h, dev, fds = await setup(tmp_path / "off", legacy_console=False)
    try:
        cid = (await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 300})).json()["command_id"]
        await asyncio.sleep(1.0)
        assert dev.rx == [] and (await motor_row(api, cid))["status"] == "issued"   # nothing written
    finally:
        await teardown(api, h, dev, fds)


async def test_unidentified_port_receives_nothing(tmp_path):
    """Before a device identifies itself, the bridge writes nothing (no time/ping), so a legacy
    console never prints "ERR unknown command" because of the bridge."""
    app = make_app(tmp_path)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="u", chamber_id="A")], operator_token=TEST_TOKEN,
                                 ping_interval_s=0.1, time_interval_s=0.1):
            dev = StubLegacyConsole(s)
            await dev.ch.write_line(b"ESP-ROM:esp32s3-20210327\r\n")
            await asyncio.sleep(1.0)
            assert dev.rx == []
            await dev.status_block()                    # now legacy: only console commands allowed
            await asyncio.sleep(0.6)
            assert all(re.fullmatch(r"motor test \d+|setpoint [\d.]+", x) for x in dev.rx)
            await dev.close()
    finally:
        close_fds(m, s)


async def setpoint_rows(api):
    return (await api.get("/api/v1/commands?command_type=control")).json()


async def test_set_setpoint_maps_to_console_and_respects_disarmed_peltier(tmp_path):
    # status shows armed(peltier=0): setpoint is stored locally but surfaced as rejected
    app, api, h, dev, fds = await setup(tmp_path)
    try:
        async def first_acked():
            rows = [r for r in await setpoint_rows(api) if r["acks"]]
            return rows[-1] if rows else None
        r = await wait_for(first_acked, what="setpoint ack")
        a = r["acks"][0]["payload"]
        assert r["status"] == "rejected" and "Peltier disarmed" in a["detail"]
        assert a["result"]["applied_setpoint_c"] == 22.0 and a["result"]["peltier_armed"] is False
        assert "setpoint 22.00" in dev.rx and not any(x.startswith(("arm", "disarm")) for x in dev.rx)
    finally:
        await teardown(api, h, dev, fds)


async def test_set_setpoint_accepted_when_armed_and_idle_rejected(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="u", chamber_id="A")], operator_token=TEST_TOKEN) as h:
            dev = StubLegacyConsole(s)
            await dev.status_block(armed_peltier=1)
            await wait_for(lambda: h.session().protocol == "legacy_status_log", what="legacy")

            async def acked(status):
                rows = [r for r in await setpoint_rows(api) if r["status"] == status]
                return rows[0] if rows else None
            r = await wait_for(lambda: acked("accepted"), what="accepted setpoint")
            assert r["acks"][0]["payload"]["result"] == {"applied_setpoint_c": 22.0, "stored_locally": True,
                                                         "console": "OK setpoint in force: 22.00 C (local limits 4-35)",
                                                         "peltier_armed": True}
            # control_mode idle has no console equivalent -> rejected, nothing written
            n_before = len(dev.rx)
            await api.post("/api/v1/chambers/A/setpoint", headers=auth(),
                           json={"setpoint_c": 22.0, "control_mode": "idle", "source": "operator"})
            r = await wait_for(lambda: acked("rejected"), what="idle rejected")
            assert "cannot set control_mode 'idle'" in r["acks"][0]["payload"]["detail"]
            assert len(dev.rx) == n_before
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)
