"""(f) contract check, (h) replay mode via the real CLI, and the LAN-bound offline run
(server on 0.0.0.0, reached over the Mac's LAN address, audited for outbound traffic)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from . import net_audit
from .conftest import JUDGE_SENTENCE, REPO_ROOT, auth, make_stack, wait_for

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures" / "replay_small.jsonl"


# ------------------------------------------------------------------ (f) contract
def test_contract_check_passes():
    r = subprocess.run([sys.executable, "-m", "fermentai.api.contract", "--check"], cwd=REPO_ROOT,
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "up to date" in r.stdout


# ------------------------------------------------------------------ (h) replay
def test_replay_cli_labels_records_replay(tmp_path):
    stack = make_stack(tmp_path)
    try:
        stack.start_server()
        p = stack.spawn("replay", ["-m", "simulated_devices", "--chambers", "A", "B",
                                   "--base-url", stack.base_url, "--replay", str(FIXTURE),
                                   "--replay-speed", "0"])
        assert p.wait(timeout=60) == 0, stack.tail("replay")
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
        with stack.client() as c:
            for cid in "AB":
                hist = c.get(f"/api/v1/chambers/{cid}/telemetry").json()
                expect = [r for r in rows if r["chamber_id"] == cid]
                assert len(hist) == len(expect)
                assert [h["source"] for h in hist] == ["replay"] * len(expect)
                assert [h["timestamp_utc"] for h in hist] == [r["timestamp_utc"] for r in expect]
                assert all("replay" in h["quality_flags"] for h in hist)
                st = c.get(f"/api/v1/chambers/{cid}/state").json()
                assert st["provenance"] == "replay"
                reads = c.get(f"/api/v1/chambers/{cid}/readings").json()
                assert reads and {r["provenance"] for r in reads} == {"replay"}
    finally:
        stack.stop_all()


# ------------------------------------------------------------------ LAN / offline (PLAN §8)
def lan_ip() -> str | None:
    """The address other LAN devices would use to reach this Mac (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.255.255", 9))   # private range; UDP connect sends nothing
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def test_lan_bound_server_full_loop_without_external_traffic(tmp_path):
    """Bind 0.0.0.0 like scripts/start_demo.sh, reach the server via the LAN address (a stand-in
    for the iPad), run the judge loop with the virtual devices, and assert the server process
    made no DNS lookups or connections outside loopback/private ranges."""
    stack = make_stack(tmp_path)
    try:
        stack.start_server(host="0.0.0.0")
        stack.start_devices("--interval", "1", "--poll-interval", "1", "--no-images")
        ip = lan_ip()
        urls = [stack.base_url] + ([f"http://{ip}:{stack.port}"] if ip else [])
        for base in urls:
            r = httpx.get(f"{base}/api/v1/health", timeout=5)
            assert r.status_code == 200 and r.json()["llm_mode"] == "offline", base
            assert httpx.get(f"{base}/", timeout=5).status_code == 200
        with stack.client() as c:
            wait_for(lambda: all(c.get(f"/api/v1/chambers/{x}/state").json()["latest_telemetry"]
                                 for x in "AB"), 45, what="telemetry\n" + stack.tail("devices"))
            r = c.post("/api/v1/taste-feedback", headers=auth(), json={"text": JUDGE_SENTENCE},
                       timeout=120)
            assert r.status_code == 200, r.text
            ids = [m["command_id"] for m in r.json()["motor_commands"]]

            def ran():
                log = {x["command_id"]: x for x in c.get("/api/v1/commands?command_type=motor").json()}
                return all(log[i]["ran"] for i in ids)

            wait_for(ran, 30, what="motor completion\n" + stack.tail("devices"))
            for path in ("/api/v1/molecular/summary", "/api/v1/predictions", "/api/v1/stl",
                         "/api/v1/demos", "/api/v1/agents/A/state"):
                assert c.get(path).status_code == 200, path
    finally:
        stack.stop_all()
    recs = net_audit.read_log(stack.audit_log)
    ext = net_audit.violations(stack.audit_log)
    assert not ext, f"outbound non-LAN traffic from the server: {ext}"
    # sanity: the hook was live (uvicorn resolves its bind address through getaddrinfo or the
    # log is empty because nothing at all was looked up -- both are fine; external is not)
    assert stack.audit_log.is_file()
    print(f"net audit: {len(recs)} socket events, 0 external; LAN url: {urls[-1]}")


def test_net_audit_positive_control(tmp_path):
    """The audit hook really records DNS lookups and connects, and flags public addresses
    (UDP connect sends no packets, so this needs no internet)."""
    log = tmp_path / "audit.jsonl"
    code = (
        "import socket, sys\n"
        "from tests.integration import net_audit\n"
        f"net_audit.install({str(log)!r})\n"
        "socket.getaddrinfo('localhost', 80)\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 53)); s.close()\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('192.168.1.10', 9)); s.close()\n"
        "try:\n    socket.getaddrinfo('example.invalid', 443)\nexcept OSError:\n    pass\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                       timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    recs = net_audit.read_log(log)
    events = {(x["event"], x["host"]) for x in recs}
    assert ("socket.getaddrinfo", "localhost") in events
    assert ("socket.connect", "8.8.8.8") in events and ("socket.connect", "192.168.1.10") in events
    bad = {x["host"] for x in net_audit.violations(log)}
    assert bad == {"8.8.8.8", "example.invalid"}, bad
