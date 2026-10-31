"""End-to-end against the REAL FastAPI app (in-process ASGI transport, temp SQLite DB).

Works with both the v1.0 backend and the v1.1 extension: the device starts in v1.1 mode and falls
back automatically if the server rejects the additive fields.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from fermentai.api.testing import TEST_TOKEN, FakeClock, auth, make_app
from simulated_devices import chamber_A, chamber_B
from simulated_devices.device import VirtualChamber
from simulated_devices.network_client import (
    V10_TELEMETRY_FIELDS,
    V11_TELEMETRY_EXTRAS,
    ContractState,
    DeviceHttpClient,
)
from simulated_devices.replay import replay

from .conftest import Mono


def stored_payload(tel: dict) -> dict:
    """Original device payload as stored by the backend (`payload` in v1.1, `payload_json` in v1.0)."""
    return tel.get("payload") or tel.get("payload_json") or {}


class Rig:
    def __init__(self, tmp_path, contract="v1.1", **cfg_kw):
        self.clock = FakeClock()
        self.app = make_app(tmp_path, clock=self.clock)
        self.mono = Mono()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="http://test")
        self.contract = ContractState(contract)
        cfg_kw.setdefault("use_fermentation_model", False)
        self.devs = {}
        for mod in (chamber_A, chamber_B):
            cfg = mod.config(**cfg_kw)
            net = DeviceHttpClient(self.client, cfg.chamber_id, cfg.device_id, self.contract, [TEST_TOKEN])
            self.devs[cfg.chamber_id] = VirtualChamber(cfg, net, clock=self.mono)

    async def run(self, seconds: float, dt: float = 0.5):
        for _ in range(int(seconds / dt)):
            self.mono.t += dt
            self.clock.advance(dt)
            for d in self.devs.values():
                await d.step()

    async def get(self, path):
        r = await self.client.get(path)
        assert r.status_code == 200, r.text
        return r.json()


def test_two_chambers_telemetry_motor_and_images(tmp_path):
    async def go():
        rig = Rig(tmp_path, images_enabled=True, image_interval_s=60.0, telemetry_interval_s=5.0)
        async with rig.client:
            await rig.run(12)
            a, b = rig.devs["A"], rig.devs["B"]
            assert a.setpoint_c == 22.0 and b.setpoint_c == 27.0      # hardware.yaml initial
            for cid in ("A", "B"):
                st = await rig.get(f"/api/v1/chambers/{cid}/state")
                tel = st["latest_telemetry"]
                assert tel is not None and tel["source"] == "simulated", st
                assert "simulated_device" in tel["quality_flags"]
                assert tel["temperature_setpoint_c"] == {"A": 22.0, "B": 27.0}[cid]
                assert st["latest_image"] is not None and st["latest_image"]["source"] == "simulated"
            # control commands were all acked by the right device
            ctl = await rig.get("/api/v1/commands?command_type=control")
            assert ctl and all(c["acked"] for c in ctl)

            # motor round trip: accepted on start, completed only after the duration elapsed
            r = await rig.client.post("/api/v1/chambers/A/motor", headers=auth(),
                                      json={"duration_s": 3, "pattern": "wiggle"})
            assert r.status_code == 200, r.text
            mid = r.json()["command_id"]
            await rig.run(1.0)
            assert a.motor.running and a.build_telemetry()["motor_running"] is True
            assert not b.motor.running
            await rig.run(4.0)
            assert not a.motor.running
            log = {m["command_id"]: m for m in await rig.get("/api/v1/commands?chamber_id=A")}
            assert [x["status"] for x in log[mid]["acks"]] == ["accepted", "completed"], log[mid]
            assert log[mid]["status"] == "completed"
            dev_acks = [x for x in a.ack_log if x["command_id"] == mid]
            assert dev_acks[1]["device_uptime_ms"] - dev_acks[0]["device_uptime_ms"] >= 3000

            # setpoint change propagates (server clamps the step to 3 C)
            await rig.client.post("/api/v1/chambers/B/setpoint", headers=auth(), json={"setpoint_c": 25.0})
            await rig.run(4.0)
            assert b.setpoint_c == 25.0
            assert a.stats["errors"] == 0 and b.stats["errors"] == 0, (a.stats, b.stats)
            return rig.contract.mode

    mode = asyncio.run(go())
    print("contract mode negotiated with the real app:", mode)


def test_v11_extras_survive_when_backend_supports_them(tmp_path):
    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            await rig.run(6)
            tel = (await rig.get("/api/v1/chambers/A/state"))["latest_telemetry"]
            payload = stored_payload(tel)
            if rig.contract.mode == "v1.1":
                assert payload.get("boot_id") == rig.devs["A"].boot_id
                assert payload.get("sequence_number", 0) >= 1
                assert payload.get("timestamp_utc") is None  # unsynced: server must not invent it
                assert payload.get("timestamp_quality") == "unsynced"
                assert payload.get("field_provenance", {}).get("chamber_temperature_c") == "simulated"
            else:  # v1.0 server: fell back, nothing beyond the frozen field set was sent
                assert not {k for k, v in payload.items() if v not in (None, [], {})} & set(V11_TELEMETRY_EXTRAS)

    asyncio.run(go())


def test_contract_v10_mode_against_real_app(tmp_path):
    async def go():
        rig = Rig(tmp_path, contract="v1.0")
        async with rig.client:
            await rig.run(6)
            tel = (await rig.get("/api/v1/chambers/B/state"))["latest_telemetry"]
            assert tel["source"] == "simulated"
            sent = {k for k, v in stored_payload(tel).items() if v not in (None, [], {})}
            assert not sent & set(V11_TELEMETRY_EXTRAS), sent & set(V11_TELEMETRY_EXTRAS)
            assert rig.devs["B"].stats["errors"] == 0

    asyncio.run(go())


def test_fallback_to_v10_on_extra_field_422():
    """A strict v1.0-style server (extra=forbid) -> device falls back and resends in v1.0 shape."""
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/telemetry":
            body = json.loads(request.content)
            extra = [k for k in body if k not in V10_TELEMETRY_FIELDS]
            if extra:
                return httpx.Response(422, json={"detail": [
                    {"type": "extra_forbidden", "loc": ["body", k], "msg": "Extra inputs are not permitted"}
                    for k in extra]})
            received.append(body)
            return httpx.Response(200, json={"ok": True, "telemetry_id": len(received), "chamber_id": "A",
                                             "received_at_utc": "x", "server_time_utc": "x",
                                             "quality_flags": []})
        return httpx.Response(404, json={"detail": "nope"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://t") as c:
            contract = ContractState("v1.1")
            cfg = chamber_A.config(use_fermentation_model=False, images_enabled=False)
            dev = VirtualChamber(cfg, DeviceHttpClient(c, "A", cfg.device_id, contract, ["t"]), clock=Mono())
            res = await dev.net.post_telemetry(dev.build_telemetry())
            assert res.ok and contract.mode == "v1.0" and contract.switches == [("v1.1", "v1.0")]
            assert set(received[0]) == set(V10_TELEMETRY_FIELDS)

    asyncio.run(go())


def test_replay_preserves_timestamps(tmp_path):
    rec = tmp_path / "recorded.jsonl"
    rows = [{"chamber_id": "A", "timestamp_utc": f"2026-10-30T10:0{i}:00Z", "chamber_temperature_c": 22.0 + i,
             "source": "real", "device_id": "fermentai-esp32-a", "quality_flags": [],
             "unknown_column": 1} for i in range(3)]
    rec.write_text("\n".join(json.dumps(r) for r in rows))

    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            nets = {c: d.net for c, d in rig.devs.items()}
            stats = await replay(rec, nets, speed=0)
            assert stats == {"sent": 3, "failed": 0, "skipped": 0}
            hist = await rig.get("/api/v1/chambers/A/telemetry")
            assert [h["source"] for h in hist] == ["replay"] * 3
            assert [h["timestamp_utc"] for h in hist] == [r["timestamp_utc"] for r in rows]
            assert all("replay" in h["quality_flags"] for h in hist)

    asyncio.run(go())


def test_no_control_command_churn_and_cancel_and_capture(tmp_path):
    async def go():
        rig = Rig(tmp_path, images_enabled=False)
        async with rig.client:
            await rig.run(30)
            ctl = await rig.get("/api/v1/commands?command_type=control&chamber_id=A")
            assert len(ctl) == 1, [c["status"] for c in ctl]     # standing setpoint re-served
            a = rig.devs["A"]
            # cancel a running motor -> device stops it and acks "cancelled" exactly once
            r = await rig.client.post("/api/v1/chambers/A/motor", headers=auth(), json={"duration_s": 8})
            mid = r.json()["command_id"]
            await rig.run(2)
            assert a.motor.running
            await rig.client.delete(f"/api/v1/commands/{mid}", headers=auth())
            await rig.run(4)
            assert not a.motor.running
            log = {m["command_id"]: m for m in await rig.get("/api/v1/commands?chamber_id=A")}
            assert log[mid]["status"] == "cancelled"
            assert [x["status"] for x in log[mid]["acks"]] == ["accepted", "cancelled"], log[mid]["acks"]
            # capture-image (v1.1 only): device uploads a SIMULATED frame and acks completed
            r = await rig.client.post("/api/v1/chambers/B/capture-image", headers=auth(), json={})
            if r.status_code in (404, 405):
                return
            assert r.status_code == 200, r.text
            cap = r.json().get("command_id")
            await rig.run(3)
            st = await rig.get("/api/v1/chambers/B/state")
            assert st["latest_image"] and st["latest_image"]["source"] == "simulated"
            log = {m["command_id"]: m for m in await rig.get("/api/v1/commands?chamber_id=B")}
            assert [x["status"] for x in log[cap]["acks"]] == ["accepted", "completed"]
            assert rig.devs["B"].stats["errors"] == 0 and a.stats["errors"] == 0

    asyncio.run(go())
