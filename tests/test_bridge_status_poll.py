"""Fast door sensing: `status` polls on a legacy fw 0.3.x console; change-only forwarding."""

import asyncio

from fermentai.api.testing import TEST_TOKEN, make_app
from fermentai.bridge import PortSpec
from fermentai.bridge.testing import BridgeHarness, asgi_client, close_fds, pty_pair, wait_for
from tests.test_bridge_legacy_console import STATUS_BLOCK, FakeLegacyConsole


class PollConsole(FakeLegacyConsole):
    """Answers `status` with the same block the board prints every 30 s."""

    def __init__(self, fd):
        self.door, self.opens = "closed", 0
        super().__init__(fd)

    def block(self):
        out = []
        for line in STATUS_BLOCK:
            if "door=" in line:
                line = line.replace("door=closed opens=0", f"door={self.door} opens={self.opens}")
            out.append(line)
        return out

    async def _run(self):
        while True:
            line = await self.ch.readline()
            if line is None:
                return
            text = line.decode(errors="replace").strip()
            self.rx.append(text)
            if text == "status":
                for ln in self.block():
                    await self.ch.write_line((ln + "\r\n").encode())
            elif text.startswith("motor test"):
                await self.ch.write_line(b"OK running 300 ms\r\n")
            else:
                await self.ch.write_line(b"ERR unknown command (try: help)\r\n")


async def _setup(tmp_path, poll=0.1):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    h = BridgeHarness(app, [PortSpec(fd=m, label="usbmodem-A", chamber_id="A")], operator_token=TEST_TOKEN,
                      legacy_status_poll_s=poll)
    await h.__aenter__()
    dev = PollConsole(s)
    for ln in dev.block():            # the board's own periodic block starts legacy mode
        await dev.ch.write_line((ln + "\r\n").encode())
    await wait_for(lambda: h.session().protocol == "legacy_status_log", what="legacy mode")
    return app, api, h, dev, (m, s)


async def _teardown(api, h, dev, fds):
    await dev.close()
    await h.__aexit__(None, None, None)
    await api.aclose()
    close_fds(*fds)


async def test_polls_status_and_forwards_only_changes(tmp_path):
    app, api, h, dev, fds = await _setup(tmp_path)
    try:
        sess = h.session()
        await wait_for(lambda: sum(1 for x in dev.rx if x == "status") >= 5, what="5 status polls")
        assert sess.counters["legacy_blocks_suppressed"] >= 2
        assert sess.counters["legacy_blocks"] - sess.counters["legacy_blocks_suppressed"] == 1
        dev.door, dev.opens = "open", 1
        t0 = asyncio.get_running_loop().time()
        await wait_for(lambda: sess._last_fwd and sess._last_fwd["door_open"] is True, what="door open forwarded")
        assert asyncio.get_running_loop().time() - t0 < 1.5
        dev.door = "closed"
        await wait_for(lambda: sess._last_fwd["door_open"] is False, what="door closed forwarded")
        assert all(x in ("status",) for x in dev.rx[-3:])   # only `status` was ever written
    finally:
        await _teardown(api, h, dev, fds)


async def test_counter_infers_missed_opening(tmp_path):
    app, api, h, dev, fds = await _setup(tmp_path, poll=0.1)
    try:
        sess = h.session()
        await wait_for(lambda: sess._last_fwd is not None, what="first record")
        sent = []
        orig = sess._forward_telemetry

        async def spy(payload, nan_fixed):
            sent.append(payload)
            await orig(payload, nan_fixed)
        sess._forward_telemetry = spy
        dev.opens = 2                     # opened twice between polls, door closed again
        await wait_for(lambda: len(sent) >= 3, what="inferred pair + real record")
        doors = [p["door_open"] for p in sent[:3]]
        assert doors == [True, False, False]
        assert all("door_event_inferred_from_counter" in p["quality_flags"] for p in sent[:2])
        assert "door_event_inferred_from_counter" not in sent[2]["quality_flags"]
        seqs = [p["sequence_number"] for p in sent[:3]]
        assert seqs == sorted(set(seqs))
        assert sess.counters["door_events_inferred"] == 2
    finally:
        await _teardown(api, h, dev, fds)


async def test_no_status_while_console_command_pending(tmp_path):
    app, api, h, dev, fds = await _setup(tmp_path, poll=0.05)
    try:
        sess = h.session()
        sess.console_pending.append({"kind": "motor", "command_id": "x", "sent": 1e12})
        await wait_for(lambda: any(x == "status" for x in dev.rx), what="poll")  # one may be in flight
        n = dev.rx.count("status")
        await asyncio.sleep(0.5)
        assert dev.rx.count("status") == n
        sess.console_pending.clear()
        await wait_for(lambda: dev.rx.count("status") > n, what="polling resumes")
    finally:
        await _teardown(api, h, dev, fds)


async def test_poll_disabled_by_default(tmp_path):
    app, api, h, dev, fds = await _setup(tmp_path, poll=0.0)
    try:
        await asyncio.sleep(0.5)
        assert "status" not in dev.rx
    finally:
        await _teardown(api, h, dev, fds)
