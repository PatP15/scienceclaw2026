"""Bridge behaviour against a scripted 'real firmware' (exact serial_v1 lines on a pty)."""

import asyncio
import base64
import hashlib
import io
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fermentai.api.testing import TEST_TOKEN, auth, make_app
from fermentai.bridge import PortSpec
from fermentai.bridge.cli import build_parser, config_from_args
from fermentai.bridge.testing import (
    BridgeHarness,
    RawDevice,
    asgi_client,
    close_fds,
    hello,
    pty_pair,
    telemetry_line,
    wait_for,
)

TOK_A = "device-token-a"


async def latest(api, chamber="A"):
    return (await api.get(f"/api/v1/chambers/{chamber}/state")).json()["latest_telemetry"]


async def test_real_device_measured_tokens_nan_and_handshake(tmp_path):
    app = make_app(tmp_path, device_tokens={"A": TOK_A})
    seen_auth = []

    async def hook(req):
        seen_auth.append((req.url.path, req.headers.get("authorization"), req.headers.get("x-device-id")))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", event_hooks={"request": [hook]})
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="usb-A")], operator_token=TEST_TOKEN, http=http,
                                 device_tokens={"A": TOK_A}) as h:
            dev = RawDevice(s)
            await dev.send("ESP-ROM:esp32s3-20210327")
            await dev.send(hello("A"))
            await wait_for(lambda: dev.of_type("hello_ack"), what="hello_ack")
            ack = dev.of_type("hello_ack")[0]
            assert ack["chamber_id"] == "A" and ack["device_id"] == "fermentai-esp32-a" and ack["v"] == "1.0"
            assert dev.of_type("time"), "host must send time on connect / after hello"
            line = telemetry_line("A", 1, source="real")
            await dev.send(P_nan(line))
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 1, what="telemetry 1")
            t = await latest(api)
            assert t["source"] == "measured" and t["payload"]["source_as_sent"] == "measured"
            assert "bridge_nan_replaced_with_null" in t["quality_flags"] and t["ph"] is None
            missing = telemetry_line("A", 2)
            del missing["payload"]["source"]
            await dev.send(missing)
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 2, what="telemetry 2")
            assert (await latest(api))["source"] == "measured"
            posts = [a for a in seen_auth if a[0] == "/api/v1/telemetry"]
            assert posts and all(a[1] == f"Bearer {TOK_A}" and a[2] == "fermentai-esp32-a" for a in posts)
            # device ping -> pong
            await dev.send({"type": "ping", "seq": 42})
            await wait_for(lambda: any(p.get("seq") == 42 for p in dev.of_type("pong")), what="pong")
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


def P_nan(line: dict) -> str:
    """telemetry line with a NaN literal (as a buggy firmware might print)."""
    return json.dumps({"type": line["type"], "v": "1.0", "payload": {**line["payload"], "ph": float("nan")}})


async def test_commands_written_once_acked_and_resent_after_reboot(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="usb-A")], operator_token=TEST_TOKEN) as h:
            dev = RawDevice(s)
            await dev.send(hello("A", boot_id="boot1"))
            await wait_for(lambda: dev.of_type("command"), what="set_setpoint command")
            sp = dev.of_type("command")[0]
            assert sp["payload"]["action"] == "set_setpoint" and sp["server_time_utc"]
            r = await api.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_ms": 3000})
            mid = r.json()["command_id"]
            motor_lines = lambda: [c for c in dev.of_type("command") if c["payload"]["command_id"] == mid]
            await wait_for(motor_lines, what="animate_motor line")
            await asyncio.sleep(1.2)                       # several polls: still written only once
            assert len(motor_lines()) == 1
            cmd = motor_lines()[0]
            assert cmd["payload"]["parameters"] == {"duration_ms": 3000, "pattern": "wiggle"}
            seqs = [c["payload"]["sequence_number"] for c in dev.of_type("command")]
            assert seqs == sorted(seqs)
            # device reboots before acking -> pending commands are re-sent to the new boot
            await dev.send(hello("A", boot_id="boot2"))
            await wait_for(lambda: len(motor_lines()) == 2, what="re-send after reboot")
            base = {"command_id": mid, "device_id": "fermentai-esp32-a", "chamber_id": "A"}
            await dev.send({"type": "ack", "payload": {**base, "status": "accepted", "device_uptime_ms": 5}})
            await dev.send({"type": "ack", "payload": {**base, "status": "completed", "device_uptime_ms": 3010,
                                                       "result": {"ran_ms": 3000}}})

            async def ran():
                c = next(c for c in (await api.get("/api/v1/commands?action=animate_motor")).json()
                         if c["command_id"] == mid)
                return c["ran"]
            await wait_for(ran, what="motor ran")
            assert h.session().counters["ack_ok"] == 2
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_mapping_conflict_mismatch_and_logs_only(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m1, s1, _ = pty_pair()
    m2, s2, _ = pty_pair()
    m3, s3, _ = pty_pair()
    try:
        ports = [PortSpec(fd=m1, label="p1", chamber_id="A"), PortSpec(fd=m2, label="p2"), PortSpec(fd=m3, label="p3")]
        async with BridgeHarness(app, ports, operator_token=TEST_TOKEN) as h:
            d1, d2, d3 = RawDevice(s1), RawDevice(s2), RawDevice(s3)
            # p1 is pinned to A but the board says it is B -> blocked, nothing forwarded
            await d1.send(hello("B"))
            await d1.send(telemetry_line("B", 1))
            await wait_for(lambda: h.session(0).state == "conflict", what="conflict state")
            assert not d1.of_type("hello_ack")
            # p2 is chamber A; a payload for B on it is dropped
            await d2.send(hello("A"))
            await d2.send(telemetry_line("B", 1))
            await d2.send(telemetry_line("A", 1))
            await wait_for(lambda: h.session(1).counters["telemetry_ok"] == 1, what="A telemetry")
            assert h.session(1).counters["chamber_mismatch"] == 1
            # p3 prints only text
            for i in range(3):
                await d3.send(f"[boot] waiting for sensors {i}")
            await wait_for(lambda: h.session(2).state == "logs_only", what="logs_only")
            assert (await latest(api, "B")) is None              # nothing reached chamber B
            st = h.bridge.status()
            assert st["chambers"]["A"]["link"] == "connected" and st["chambers"]["A"]["port"] == "p2"
            assert st["ports"][2]["recent_logs"][-1] == "[boot] waiting for sensors 2"
            # a second port claiming A while p2 is live -> conflict
            await d3.send(hello("A", device_id="fermentai-esp32-a"))
            await wait_for(lambda: h.session(2).blocked_reason, what="ownership conflict")
            for d in (d1, d2, d3):
                await d.close()
    finally:
        await api.aclose()
        close_fds(m1, s1, m2, s2, m3, s3)


class Flaky(httpx.AsyncBaseTransport):
    def __init__(self, inner, fail_n):
        self.inner, self.fail_n = inner, fail_n

    async def handle_async_request(self, request):
        if request.url.path == "/api/v1/telemetry" and self.fail_n > 0:
            self.fail_n -= 1
            return httpx.Response(503, json={"detail": "backend restarting"})
        return await self.inner.handle_async_request(request)


async def test_backend_outage_outbox_preserves_order(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    http = httpx.AsyncClient(transport=Flaky(httpx.ASGITransport(app=app), 3), base_url="http://t")
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="usb-A")], operator_token=TEST_TOKEN, http=http) as h:
            dev = RawDevice(s)
            await dev.send(hello("A"))
            for seq in (1, 2, 3):
                await dev.send(telemetry_line("A", seq))
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 3, timeout=15, what="outbox drained")
            rows = (await api.get("/api/v1/chambers/A/telemetry")).json()
            assert [r["sequence_number"] for r in rows] == [1, 2, 3]
            assert h.session().counters["telemetry_retry"] >= 3
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_discovery_and_path_reconnect_after_unplug(tmp_path):
    """Bridge opens a device *path* found by discovery; unplug kills the pty, replug recreates it
    under the same name (like macOS re-enumerating /dev/cu.usbmodemXXXX)."""
    app = make_app(tmp_path)
    api = asgi_client(app)
    devdir = tmp_path / "dev"
    devdir.mkdir()
    link = devdir / "cu.usbmodemTEST1"
    m, s, slave = pty_pair()
    os.symlink(slave, link)
    try:
        async with BridgeHarness(app, [], operator_token=TEST_TOKEN, discover=True,
                                 patterns=(str(devdir / "cu.usbmodem*"),), discover_interval_s=0.2,
                                 exclusive=False) as h:
            await wait_for(lambda: h.bridge.sessions, what="port discovered")
            dev = RawDevice(m)
            await dev.send(hello("A", boot_id="bootP1"))
            await dev.send(telemetry_line("A", 1, boot_id="bootP1"))
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 1, what="telemetry via path")
            assert h.session().name == str(link)
            # unplug
            await dev.close()
            close_fds(m, s)
            await wait_for(lambda: h.session().state == "disconnected", what="unplug detected")
            # replug: new device node behind the same name
            os.unlink(link)
            m, s, slave = pty_pair()
            os.symlink(slave, link)
            dev = RawDevice(m)

            async def back():
                if h.session().channel is None:
                    return False
                await dev.send(hello("A", boot_id="bootP2"))
                await dev.send(telemetry_line("A", 1, boot_id="bootP2"))
                await asyncio.sleep(0.3)
                t = await latest(api)
                return t and t["boot_id"] == "bootP2"
            await wait_for(back, timeout=15, interval=0.3, what="recovered after replug")
            assert len(h.bridge.sessions) == 1                   # same session, not a duplicate port
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_image_chunks_reassembly_and_rejection(tmp_path):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 170, 120)).save(buf, "JPEG")
    jpeg = buf.getvalue()
    sha = hashlib.sha256(jpeg).hexdigest()
    parts = [jpeg[i:i + 300] for i in range(0, len(jpeg), 300)]
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="usb-B")], operator_token=TEST_TOKEN) as h:
            dev = RawDevice(s)
            await dev.send(hello("B"))
            for i, part in reversed(list(enumerate(parts, 1))):        # out of order is fine
                await dev.send({"type": "image", "chamber_id": "B", "device_id": "fermentai-esp32-b",
                                "uptime_ms": 1, "bytes": len(jpeg), "sha256": sha, "encoding": "base64-chunks",
                                "chunk": f"{i}/{len(parts)}", "data": base64.b64encode(part).decode()})
            await wait_for(lambda: h.session().counters["images_ok"] == 1, what="image uploaded")
            img = (await api.get("/api/v1/images?chamber_id=B")).json()[0]
            assert img["sha256"] == sha and img["source"] == "measured"
            await dev.send({"type": "image", "chamber_id": "B", "bytes": 4, "sha256": "0" * 64,
                            "encoding": "base64-chunks", "chunk": "1/1", "data": base64.b64encode(b"abcd").decode()})
            await dev.send({"type": "image", "chamber_id": "B", "bytes": 4, "sha256": sha,
                            "encoding": "base64-chunks", "chunk": "1/1", "data": "%%%not-base64"})
            await wait_for(lambda: h.session().counters["image_errors"] == 2, what="bad images rejected")
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_record_then_replay_labels_replay_and_sends_no_commands(tmp_path):
    rec = tmp_path / "serial_A.jsonl"
    live_app = make_app(tmp_path / "live")
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(live_app, [PortSpec(fd=m, label="usb-A")], operator_token=TEST_TOKEN,
                                 record_path=rec) as h:
            dev = RawDevice(s)
            await dev.send("boot banner")
            await dev.send(hello("A", boot_id="bootR"))
            for seq in (1, 2):
                await dev.send(telemetry_line("A", seq, boot_id="bootR"))
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 2, what="live telemetry")
            await dev.close()
    finally:
        close_fds(m, s)
    recorded = [json.loads(x) for x in rec.read_text().splitlines()]
    assert {r["dir"] for r in recorded} == {"rx", "tx"}
    replay_app = make_app(tmp_path / "replay")
    api = asgi_client(replay_app)
    try:
        async with BridgeHarness(replay_app, [], operator_token=TEST_TOKEN, replay=(rec, 0.0)) as h:
            await wait_for(lambda: h.bridge.sessions and h.session()._finished, what="replay finished")
            rows = (await api.get("/api/v1/chambers/A/telemetry")).json()
            assert [r["source"] for r in rows] == ["replay", "replay"]
            assert {r["boot_id"] for r in rows} == {"replay-bootR"}
            assert h.session().replay.tx_lines == []             # never commands anything
            assert h.bridge.status()["mode"] == "replay"
        # plain raw-line logs replay too
        raw = tmp_path / "raw.log"
        raw.write_text("ESP-ROM boot\n" + json.dumps({**telemetry_line("B", 9, boot_id="bootQ"), "v": "1.0"}) + "\n")
        async with BridgeHarness(replay_app, [], operator_token=TEST_TOKEN, replay=(raw, 0.0)) as h:
            await wait_for(lambda: h.bridge.sessions and h.session()._finished, what="raw replay finished")
        t = await latest(api, "B")
        assert t["source"] == "replay" and t["sequence_number"] == 9
    finally:
        await api.aclose()


def test_bridge_status_api(tmp_path):
    from fastapi.testclient import TestClient
    c = TestClient(make_app(tmp_path))
    assert c.get("/api/v1/bridge/status").json()["available"] is False
    p = tmp_path / "data" / "telemetry" / "bridge_status.json"
    p.parent.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps({"transport": "usb-serial", "updated_at_utc": old,
                             "chambers": {"A": {"link": "connected", "label": "USB · connected"}}}))
    st = c.get("/api/v1/bridge/status").json()
    assert st["available"] and st["bridge_running"] is False
    assert st["chambers"]["A"]["link"] == "bridge_offline"


def test_cli_config_parsing_never_opens_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("FERMENTAI_DEVICE_TOKEN_B", "tok-b")
    monkeypatch.setenv("FERMENTAI_API_TOKEN", "op")
    cfg = config_from_args(build_parser().parse_args(
        ["--port", "/dev/cu.usbmodemFAKE1=a", "--port", "/dev/cu.usbmodemFAKE2", "--map", "/dev/cu.usbmodemFAKE2=B",
         "--status-file", str(tmp_path / "s.json")]))
    assert [(p.path, p.chamber_id) for p in cfg.ports] == [("/dev/cu.usbmodemFAKE1", "A"), ("/dev/cu.usbmodemFAKE2", "B")]
    assert cfg.discover is False and cfg.device_tokens == {"B": "tok-b"} and cfg.operator_token == "op"
    assert cfg.chambers == {"A": "fermentai-esp32-a", "B": "fermentai-esp32-b"}
    cfg = config_from_args(build_parser().parse_args([]))
    assert cfg.discover is True and "/dev/cu.usbmodem*" in cfg.patterns
    with pytest.raises(SystemExit):
        config_from_args(build_parser().parse_args(["--port", "/dev/x=Z"]))
