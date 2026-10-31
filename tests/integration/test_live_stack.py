"""End-to-end over real HTTP: uvicorn server + `python -m simulated_devices` (both chambers) +
the real agents pipeline + real fermentation kinetics. PLAN §16 #5–12, PLAN_MASTER §14.

The server runs under the outbound-network audit hook (tests/integration/net_audit.py); the
last test asserts it never contacted anything outside the LAN during the whole module.
Tests run in file order and share one stack (session fixture `live_stack`).
"""

from __future__ import annotations

import csv
import io
import time

import pytest

from . import net_audit
from .conftest import JUDGE_SENTENCE, auth, wait_for
from .dashboard_scan import SCANNER, crawl, external_urls
from .provenance import audit

pytestmark = pytest.mark.integration

# Gaps the audit found and that are owned by other areas (see docs/acceptance_tests.md).
KNOWN_PROVENANCE_GAPS = {
    ("GET /api/v1/molecular/summary", "provenance"):
        "datafiles.molecular_summary returns free text, not a label (api/datafiles.py:56)",
    ("GET /api/v1/stl", "provenance"): "STL listing has no provenance field (api/routes.py stl_list)",
}


@pytest.fixture(scope="module")
def http(live_stack):
    with live_stack.client() as c:
        yield c


@pytest.fixture(scope="module")
def booted(http, live_stack):
    """Both chambers have posted at least one telemetry record."""
    wait_for(lambda: all(_state(http, c)["latest_telemetry"] for c in "AB"), 45,
             what="first telemetry from both chambers\n" + live_stack.tail("devices"))


def _state(http, cid):
    r = http.get(f"/api/v1/chambers/{cid}/state")
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ (a) boot + telemetry (#5)
def test_both_virtual_chambers_boot_and_post_v11_telemetry(http, live_stack):
    def both_live():
        sts = {c: _state(http, c) for c in "AB"}
        ok = all(s["latest_telemetry"] and not s["telemetry_stale"] for s in sts.values())
        return sts if ok else None

    sts = wait_for(both_live, 45, what="telemetry from both chambers\n" + live_stack.tail("devices"))
    device_ids = {}
    for cid, st in sts.items():
        tel = st["latest_telemetry"]
        assert tel["chamber_id"] == cid
        assert tel["source"] == "simulated" and st["provenance"] == "simulated"
        assert "simulated_device" in tel["quality_flags"]
        payload = tel["payload"]
        # contract v1.1 extras actually on the wire (no silent fallback to v1.0)
        for k in ("boot_id", "sequence_number", "device_uptime_ms", "timestamp_quality", "device_id"):
            assert payload.get(k) is not None, (cid, k, payload)
        assert payload["timestamp_quality"] == "unsynced" and tel["timestamp_utc"] is None
        assert tel["time_basis"] == "server_receipt"    # server never invented a timestamp
        device_ids[cid] = payload["device_id"]
        assert payload["firmware_version"].startswith("virtual")
        # the device that wrote is registered (and bound) to this chamber only
        seen = [d for d in st["devices"] if d.get("last_seen_at_utc")]
        assert [d["device_id"] for d in seen] == [payload["device_id"]], st["devices"]
    assert device_ids["A"] != device_ids["B"]
    devs = {d["device_id"]: d["chamber_id"] for d in http.get("/api/v1/devices").json()}
    assert devs[device_ids["A"]] == "A" and devs[device_ids["B"]] == "B"
    # telemetry histories are separate per chamber
    for cid in "AB":
        hist = http.get(f"/api/v1/chambers/{cid}/telemetry?limit=50").json()
        assert hist and {h["chamber_id"] for h in hist} == {cid}
    # real kinetics drive the starter rise (not the logistic fallback)
    boot = live_stack.tail("devices", 50000)
    assert boot.count("starter_model=fermentai.fermentation.simulation.simulate") == 2, boot[-2000:]
    assert "logistic_fallback" not in boot


# ------------------------------------------------------------------ independent setpoints (#6)
def test_independent_setpoints_round_trip(http, live_stack):
    a0, b0 = _state(http, "A"), _state(http, "B")
    assert (a0["desired_setpoint_c"], b0["desired_setpoint_c"]) == (22.0, 27.0)
    wait_for(lambda: _state(http, "A")["device_reported_setpoint_c"] == 22.0
             and _state(http, "B")["device_reported_setpoint_c"] == 27.0, 20,
             what="devices to report their initial setpoints")

    r = http.post("/api/v1/chambers/B/setpoint", headers=auth(),
                  json={"setpoint_c": 25.0, "source": "integration_test", "rationale": "independence"})
    assert r.status_code == 200, r.text
    assert r.json()["applied_c"] == 25.0 and r.json()["changed"] is True
    wait_for(lambda: _state(http, "B")["device_reported_setpoint_c"] == 25.0, 20,
             what="chamber B device to apply 25 C\n" + live_stack.tail("devices"))
    a, b = _state(http, "A"), _state(http, "B")
    assert a["desired_setpoint_c"] == 22.0 and a["device_reported_setpoint_c"] == 22.0
    assert b["desired_setpoint_c"] == 25.0
    # the B setpoint command was acked by B's device with the applied value; A got none
    cmds = http.get("/api/v1/commands?command_type=control&chamber_id=B").json()
    newest = cmds[0]
    assert newest["acks"] and newest["acks"][0]["status"] == "accepted"
    assert newest["acks"][0]["payload"]["result"]["applied_setpoint_c"] == 25.0
    # safety path: an absurd request is clamped/step-limited, never applied as-is
    time.sleep(5.2)  # min_update_interval_s
    r = http.post("/api/v1/chambers/A/setpoint", headers=auth(), json={"setpoint_c": 80.0})
    assert r.status_code == 200, r.text
    applied = r.json()["applied_c"]
    assert applied <= 25.0, r.json()   # 22 + max 3 C step, and <= 35 C limit


# ------------------------------------------------------------------ (b) judge flow (#9–11)
@pytest.fixture(scope="module")
def feedback(http):
    r = http.post("/api/v1/taste-feedback", headers=auth(), json={"text": JUDGE_SENTENCE},
                  timeout=120)
    assert r.status_code == 200, r.text
    return r.json(), time.time()


def test_feedback_structured_preference(feedback):
    resp, _ = feedback
    assert resp["pipeline_available"] is True
    res = resp["result"]
    pref = [p.lower() for p in res["sensory_interpretation"]["structured_preference"]]
    assert any("more sour" in p for p in pref), pref
    assert any("lower density" in p for p in pref), pref
    assert any("rise" in p and ("preserve" in p or "keep" in p) for p in pref), pref
    assert res["provenance"]["decision_source"] == "offline_policy"
    assert res["reasoning_mode_label"] == "Offline Policy"


def test_feedback_two_agents_exchange_structured_messages(feedback, http):
    resp, _ = feedback
    msgs = resp["stored_messages"]
    senders = {m["sender"] for m in msgs}
    assert {"scientist_A", "scientist_B"} <= senders, senders
    by_uid = {m["message_uid"]: m for m in msgs if m["message_uid"]}
    replies = [m for m in msgs if m["in_reply_to"]]
    assert replies, "no message carries in_reply_to"
    cross = [m for m in replies if m["in_reply_to"] in by_uid
             and by_uid[m["in_reply_to"]]["sender"] != m["sender"]]
    assert cross, "no agent replied to the OTHER agent's message"
    # at least two DISTINCT messages from different agents with in_reply_to chains
    assert len({m["message_uid"] for m in msgs}) == len(msgs)
    for m in cross:
        assert m["recipient"] == by_uid[m["in_reply_to"]]["sender"]
        c = m["content"]
        for k in ("id", "agent_id", "type", "content", "evidence_source", "schema_version"):
            assert k in c, (k, c)
    # the same exchange is visible through the agents endpoint
    allm = http.get("/api/v1/agents/all/messages?limit=500").json()
    assert {m["message_uid"] for m in msgs} <= {m["message_uid"] for m in allm}


def test_feedback_next_experiment(feedback, http):
    resp, _ = feedback
    nxt = resp["result"]["next_experiment"]
    assert nxt and nxt.get("id") and nxt.get("objective")
    assert set(nxt["schedules"]) == {"A", "B"}
    for cid, sch in nxt["schedules"].items():
        assert sch["points"], (cid, sch)
    assert nxt["requires_human_confirmation"] is True
    exp = resp["experiment"]
    r = http.get(f"/api/v1/experiments/{exp['experiment_id']}")
    assert r.status_code == 200 and r.json()["provenance"] == "offline_policy"
    # proposals only: setpoints are not changed by judge text unless the operator applies it
    assert resp["schedule_applied"] is False and resp["applied_setpoints"] == {}


def test_motor_commands_acked_accepted_then_completed(feedback, http, live_stack):
    resp, _ = feedback
    motors = {m["chamber_id"]: m["command_id"] for m in resp["motor_commands"]}
    assert set(motors) == {"A", "B"}
    for m in resp["motor_commands"]:
        assert m["duration_s"] == 3.0
    trace: list[tuple[str, tuple[str, ...], bool, str]] = []

    def done():
        log = {c["command_id"]: c for c in
               http.get("/api/v1/commands?command_type=motor&limit=50").json()}
        allran = True
        for cid, mid in motors.items():
            c = log[mid]
            acks = tuple(a["status"] for a in c["acks"])
            trace.append((cid, acks, c["ran"], c["status"]))
            # invariant (PLAN_MASTER §13): never "ran" before the device acked `completed`
            assert c["ran"] == ("completed" in acks), (cid, acks, c["ran"])
            allran &= c["ran"]
        return allran

    wait_for(done, 30, interval=0.1, what="both motors completed\n" + live_stack.tail("devices"))
    log = {c["command_id"]: c for c in http.get("/api/v1/commands?command_type=motor&limit=50").json()}
    for cid, mid in motors.items():
        c = log[mid]
        assert c["chamber_id"] == cid and c["status"] == "completed"
        assert [a["status"] for a in c["acks"]] == ["accepted", "completed"], c["acks"]
        up = [a["payload"]["device_uptime_ms"] for a in c["acks"]]
        assert up[1] - up[0] >= 3000 - 50, up       # ran for the commanded 3000 ms
        st = _state(http, cid)
        assert st["motor_ran"] is True and st["last_motor_command"]["command_id"] == mid
    # the trace shows a not-yet-run phase for each motor (queued/accepted, ran False)
    for cid in motors:
        assert any(t[0] == cid and not t[2] for t in trace), trace


# ------------------------------------------------------------------ (c) agent state
@pytest.mark.parametrize("aid", ["A", "B", "scientist_A", "scientist_B"])
def test_agent_state_is_real(feedback, http, aid):
    st = http.get(f"/api/v1/agents/{aid}/state").json()
    assert st.get("stub") is not True, st
    assert st["available"] is True and st["n_findings"] > 0
    ch = aid[-1]
    assert st["agent_id"] == f"scientist_{ch}" and st["chamber_id"] == ch
    assert st["current_hypothesis"] and st["current_hypothesis"]["statement"]
    assert st["mode"]["decision_source"] == "offline_policy"
    assert st["peer_findings"]["count"] > 0
    assert st["last_decision"] and st["last_decision"]["points"]


# ------------------------------------------------------------------ (d) cached artifacts
def test_molecular_summary_has_real_gpu_provenance(http):
    mol = http.get("/api/v1/molecular/summary").json()
    assert mol["available"] is True
    s = mol["summaries"]["analysis_summary.json"]
    assert s["provenance"].startswith("real:") and "ALCHEMI" in s["provenance"]
    assert s["n_converged"] == s["n_calculations"] and s["n_failed"] == 0
    desc = mol["summaries"]["molecular_descriptors.json"]["provenance"]
    assert desc["label"].startswith("real:") and desc["gpu"]
    for f in mol["files"]:
        assert http.get(f["url"]).status_code == 200, f["url"]


def test_predictions_serve_both_chamber_csvs(http):
    pred = http.get("/api/v1/predictions").json()
    assert pred["provenance"] == "simulated"
    names = {f["path"]: f for f in pred["files"]}
    for cid in "AB":
        f = names[f"chamber_{cid}_prediction.csv"]
        r = http.get(f["url"])
        assert r.status_code == 200 and "csv" in r.headers["content-type"]
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert len(rows) > 100
        assert {row["chamber_id"] for row in rows} == {cid}
        assert {row["provenance"] for row in rows} == {"simulated"}
        assert float(rows[-1]["time_h"]) >= 12.0


def test_stl_lists_both_chamber_specimens(http):
    stl = http.get("/api/v1/stl").json()
    names = {f["path"]: f for f in stl["files"]}
    for cid in "AB":
        f = names[f"crumb_chamber_{cid}.stl"]
        assert f["bytes"] > 10_000
        r = http.get(f["url"])
        assert r.status_code == 200 and len(r.content) == f["bytes"]
        v = http.get(names[f"crumb_chamber_{cid}_validation.json"]["url"]).json()
        assert v["passed"] is True and v["provenance"] == "simulated"


# ------------------------------------------------------------------ (e) dashboard offline (#7–8)
def test_dashboard_and_every_asset_resolve_without_external_urls(http):
    assets = crawl(http)
    assert "/js/app.js" in assets and "/vendor/plotly.min.js" in assets, sorted(assets)
    bad_status = {u: (r.status_code if r is not None else "absolute URL")
                  for u, r in assets.items() if r is None or r.status_code != 200}
    assert not bad_status, bad_status
    ext = {}
    for path, r in assets.items():
        if "/vendor/" in path:
            continue  # vendored plotly: see tests/test_dashboard_offline.py (no map/geo traces)
        found = external_urls(path, r.text)
        if found:
            ext[path] = found
    assert not ext, f"external URLs (scanner: {SCANNER}): {ext}"
    for path, r in assets.items():
        if path.endswith(".js"):
            assert "javascript" in r.headers["content-type"], (path, r.headers["content-type"])


# ------------------------------------------------------------------ (g) provenance audit (#12)
def test_data_provenance_audit(booted, feedback, http):
    bad, counts = audit(http)
    assert sum(counts.values()) > 50, counts
    unknown = [v for v in bad if v.key() not in KNOWN_PROVENANCE_GAPS]
    assert not unknown, "records without a provenance label:\n" + "\n".join(map(str, unknown))


@pytest.mark.xfail(reason="known gaps: molecular summary top-level provenance is free text; "
                          "STL listing has no provenance (owners: API)", strict=False)
def test_data_provenance_audit_strict(booted, feedback, http):
    bad, _ = audit(http)
    assert not bad, "\n".join(map(str, bad))


# ------------------------------------------------------------------ offline: no outbound traffic
def test_server_made_no_outbound_connections(live_stack, feedback):
    # runs last in this module: everything above (boot, telemetry, setpoints, feedback, motors,
    # dashboard crawl, provenance walk) happened under the audit hook
    assert live_stack.audit_log.is_file(), "audit hook did not start"
    ext = net_audit.violations(live_stack.audit_log)
    assert not ext, f"server contacted non-LAN hosts: {ext}"
