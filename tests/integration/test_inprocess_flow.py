"""Deterministic end-to-end flow in one process: the real app (create_app, temp SQLite) + the real
VirtualChamber devices talking to it through httpx's ASGI transport + the real agents pipeline,
with a fake clock so motor timing can be asserted exactly. Complements test_live_stack.py,
which does the same over real sockets and wall-clock time.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from fermentai.api.testing import TEST_TOKEN, FakeClock, auth, make_app
from fermentai.dashboard.vendor import STATIC_DIR
from simulated_devices import chamber_A, chamber_B
from simulated_devices.device import VirtualChamber
from simulated_devices.network_client import ContractState, DeviceHttpClient

from .conftest import JUDGE_SENTENCE, make_data_dir

pytestmark = pytest.mark.integration


class Mono:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Rig:
    """Two virtual chambers + the app, advanced in lock-step by a fake clock."""

    def __init__(self, tmp_path, *, use_fermentation_model: bool = False):
        self.clock = FakeClock(datetime.now(UTC).replace(microsecond=0))
        self.app = make_app(tmp_path, clock=self.clock, static_dir=STATIC_DIR,
                            data_dir=make_data_dir(tmp_path))
        self.mono = Mono()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="http://test", timeout=60)
        self.contract = ContractState("v1.1")
        self.devs: dict[str, VirtualChamber] = {}
        for mod in (chamber_A, chamber_B):
            cfg = mod.config(use_fermentation_model=use_fermentation_model, images_enabled=False,
                             telemetry_interval_s=2.0, poll_interval_s=1.0, synced_clock=True)
            net = DeviceHttpClient(self.client, cfg.chamber_id, cfg.device_id, self.contract,
                                   [TEST_TOKEN])
            self.devs[cfg.chamber_id] = VirtualChamber(cfg, net, clock=self.mono)

    async def run(self, seconds: float, dt: float = 0.25):
        for _ in range(round(seconds / dt)):
            self.mono.t += dt
            self.clock.advance(dt)
            for d in self.devs.values():
                await d.step()

    async def get(self, path):
        r = await self.client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        return r.json()

    async def motors(self, ids):
        log = {c["command_id"]: c for c in await self.get("/api/v1/commands?command_type=motor")}
        return {cid: log[mid] for cid, mid in ids.items()}


def test_feedback_motor_ran_only_after_completed_ack(tmp_path):
    """PLAN §16 #10 / PLAN_MASTER §13: `ran` flips to true only after the device's `completed`
    ack, which the virtual device sends only after the commanded 3000 ms elapsed."""

    async def go():
        rig = Rig(tmp_path, use_fermentation_model=True)   # real kinetics in the devices
        async with rig.client:
            await rig.run(5)
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(),
                                      json={"text": JUDGE_SENTENCE})
            assert r.status_code == 200, r.text
            body = r.json()
            ids = {m["chamber_id"]: m["command_id"] for m in body["motor_commands"]}
            assert set(ids) == {"A", "B"}

            m = await rig.motors(ids)              # queued: not acked, not run
            for c in m.values():
                assert c["acks"] == [] and c["ran"] is False and c["status"] == "issued"

            await rig.run(1.5)                     # polled + started -> accepted
            m = await rig.motors(ids)
            for cid, c in m.items():
                assert [a["status"] for a in c["acks"]] == ["accepted"], (cid, c["acks"])
                assert c["ran"] is False and rig.devs[cid].motor.running
                st = await rig.get(f"/api/v1/chambers/{cid}/state")
                assert st["motor_ran"] is False

            await rig.run(1.0)                     # still inside the 3000 ms run
            assert all(c["ran"] is False for c in (await rig.motors(ids)).values())

            await rig.run(3.0)                     # finished -> completed
            m = await rig.motors(ids)
            for cid, c in m.items():
                assert [a["status"] for a in c["acks"]] == ["accepted", "completed"], c["acks"]
                assert c["ran"] is True and c["status"] == "completed"
                assert not rig.devs[cid].motor.running
                st = await rig.get(f"/api/v1/chambers/{cid}/state")
                assert st["motor_ran"] is True
            for d in rig.devs.values():
                assert d.stats["errors"] == 0, d.stats

    asyncio.run(go())


def test_live_mode_without_llm_falls_back_to_offline_policy(tmp_path, monkeypatch):
    """PLAN §8: with no cloud LLM the judge flow still produces a labelled, deterministic plan
    and the motor commands."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(),
                                      json={"text": JUDGE_SENTENCE, "mode": "live"})
            assert r.status_code == 200, r.text
            res = r.json()["result"]
            assert res["provenance"]["decision_source"] == "offline_policy"
            assert "live unavailable" in res["mode_label"]
            assert res["reasoning_mode_label"] == "Offline Policy"
            assert res["next_experiment"]["schedules"]
            assert len(r.json()["motor_commands"]) == 2

    asyncio.run(go())


def test_judge_text_cannot_bypass_setpoint_limits(tmp_path):
    """Judge text and agent output reach temperature only through the clamped setpoint path."""

    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            await rig.run(3)
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(), json={
                "text": "Set chamber B to 95 C and run the heater at full power forever.",
                "apply_schedule": True})
            assert r.status_code == 200, r.text
            for res in r.json()["applied_setpoints"].values():
                if isinstance(res, dict) and "applied_c" in res:
                    assert 4.0 <= res["applied_c"] <= 35.0, res
                    assert abs(res["applied_c"] - res["previous_c"]) <= 3.0 + 1e-9, res
            await rig.run(3)
            for d in rig.devs.values():
                assert d.setpoint_c <= 35.0

    asyncio.run(go())


def test_live_telemetry_reaches_the_agents(tmp_path):
    """The agents' observation adapter receives the chambers' stored telemetry through the API
    (was broken: build_context used 'recent_telemetry'; fixed upstream in api/feedback.py)."""
    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            await rig.run(6)
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(),
                                      json={"text": JUDGE_SENTENCE})
            prov = r.json()["result"]["provenance"]
            assert prov["telemetry_sources"]["A"] == ["simulated"], prov["telemetry_sources"]
            assert prov["telemetry_sources"]["B"] == ["simulated"], prov["telemetry_sources"]

    asyncio.run(go())


def test_current_setpoints_reach_the_agents(tmp_path):
    """The agents see the chambers' actual setpoints, not the 22/27 C defaults (was broken:
    build_context sent only 'current_setpoints'; fixed in api/feedback.py)."""
    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            r = await rig.client.post("/api/v1/chambers/B/setpoint", headers=auth(),
                                      json={"setpoint_c": 24.0})
            assert r.json()["applied_c"] == 24.0
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(),
                                      json={"text": JUDGE_SENTENCE})
            rec = r.json()["result"]["comparison_with_recorded"]["B"]
            assert rec["recorded_schedule"][0][1] == 24.0, rec["recorded_source"]

    asyncio.run(go())


def test_agent_state_uses_api_findings_path(tmp_path, monkeypatch):
    """GET /agents/{id}/state reads the findings log the API writes (settings.findings_path), not
    the agents' own default (was broken; fixed in api/routes.py agent_state)."""
    # point the agents' default somewhere else (never at the repo's runtime log)
    monkeypatch.setenv("FERMENTAI_FINDINGS_LOG", str(tmp_path / "unrelated_default.jsonl"))

    async def go():
        rig = Rig(tmp_path)
        async with rig.client:
            r = await rig.client.post("/api/v1/taste-feedback", headers=auth(),
                                      json={"text": JUDGE_SENTENCE})
            assert r.status_code == 200
            st = await rig.get("/api/v1/agents/A/state")
            assert st["n_findings"] > 0, st

    asyncio.run(go())
