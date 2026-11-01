"""Legacy firmware 0.3.x plain-text status blocks -> measured telemetry (exact lines from the board)."""

from fermentai.api.schemas import TelemetryIn
from fermentai.api.testing import TEST_TOKEN, make_app
from fermentai.bridge import PortSpec
from fermentai.bridge.status_log import StatusLogParser
from fermentai.bridge.testing import (
    BridgeHarness,
    RawDevice,
    asgi_client,
    close_fds,
    hello,
    pty_pair,
    wait_for,
)

BLOCK = [
    "[A fermentai-esp32-a] fw=0.3.0 boot=1185100b reset=other state=waiting_for_command mode=idle out=0.00 armed(peltier=0 motor=1)",
    "  chamber=28.60C rh=13.2% p=nanPa gas=nanohm starter=23.44C hs_int=nanC hs_ext=nanC",
    "  ph=nan mv=0.0 raw=0 cal=0 door=closed opens=0 setpoint=nan faults=0x0",
    "  ens160: present=1 addr=0x53 valid=1 warmup=0 aqi=0 tvoc=0 ppb eco2=0 ppm (indices, not CO2)",
    "  flags: ds18b20_role_unassigned ph_out_of_range",
    "  wifi=0 rssi=0 tel_q=0 acks_q=0 heap=239416 psram=8179891",
]
BANNER = "FermentAI 0.3.0 chamber=A device=fermentai-esp32-a boot=1185100b reset=other build=wifi"


def feed_all(p, lines, t=0.0):
    out = []
    for i, line in enumerate(lines):
        out += p.feed(line, now=t + i * 0.01)
    return out


def test_exact_block_maps_to_telemetry():
    p = StatusLogParser(seq_base=1000)
    recs = feed_all(p, BLOCK)
    assert len(recs) == 1
    r = recs[0]
    TelemetryIn.model_validate(r)
    assert (r["chamber_id"], r["device_id"], r["boot_id"], r["firmware_version"]) == \
        ("A", "fermentai-esp32-a", "1185100b", "0.3.0")
    assert r["source"] == "measured" and r["timestamp_utc"] is None and r["timestamp_quality"] == "unsynced"
    assert "device_uptime_ms" not in r and r["sequence_number"] == 1001
    assert r["chamber_temperature_c"] == 28.6 and r["relative_humidity_percent"] == 13.2
    assert r["starter_temperature_c"] == 23.44
    for k in ("pressure_pa", "gas_resistance_ohms", "internal_heatsink_temperature_c",
              "external_heatsink_temperature_c", "ph", "ph_method", "temperature_setpoint_c"):
        assert r[k] is None, k                                  # nan -> null
    assert r["door_open"] is False and r["peltier_mode"] == "idle" and r["peltier_output_fraction"] == 0.0
    assert r["motor_running"] is False                          # armed(motor=1) is not running
    assert r["quality_flags"] == ["ds18b20_role_unassigned", "ph_out_of_range", "legacy_status_log"]
    assert r["fault_codes"] == []
    d = r["diagnostics"]
    assert d["ens160"] == {"present": 1, "valid": 1, "warmup": 0, "aqi": 0, "tvoc_ppb": 0.0, "eco2_ppm": 0.0,
                           "note": "ENS160 indices, not a CO2 measurement"}
    assert d["free_heap_bytes"] == 239416 and d["psram_free_bytes"] == 8179891
    assert d["wifi_connected"] is False and "wifi_rssi_dbm" not in d
    assert d["armed"] == {"peltier": 0, "motor": 1} and d["state"] == "waiting_for_command"
    assert d["ph_cal"] == 0 and d["ph_mv"] == 0.0 and d["reset_reason"] == "other"


def test_sequence_per_boot_and_unrelated_lines():
    p = StatusLogParser(seq_base=50)
    assert feed_all(p, ["random debug print", "I (123) wifi: init"]) == []
    a = feed_all(p, BLOCK)[0]
    b = feed_all(p, BLOCK, t=30)[0]
    assert (a["sequence_number"], b["sequence_number"]) == (51, 52)
    reboot = [BLOCK[0].replace("boot=1185100b", "boot=deadbeef"), *BLOCK[1:]]
    c = feed_all(p, reboot, t=60)[0]
    assert c["boot_id"] == "deadbeef" and c["sequence_number"] == 51


def test_missing_and_reordered_lines_and_timeout():
    p = StatusLogParser(seq_base=0, block_timeout_s=5)
    # reordered (ph before chamber), no ens160/flags line; terminated by the wifi line
    recs = feed_all(p, [BLOCK[0], BLOCK[2], BLOCK[1], BLOCK[5]])
    assert len(recs) == 1 and recs[0]["chamber_temperature_c"] == 28.6
    assert recs[0]["quality_flags"] == ["legacy_status_log"] and "ens160" not in recs[0]["diagnostics"]
    # no wifi line: emitted when the next header starts ...
    recs = feed_all(p, BLOCK[:3]) + feed_all(p, [BLOCK[0]])
    assert len(recs) == 1
    # ... or after the block timeout
    assert p.feed(BLOCK[1], now=100.0) == [] and p.flush_if_stale(now=104.0) == []
    assert len(p.flush_if_stale(now=106.0)) == 1
    # a header with no measurement lines produces nothing
    assert feed_all(p, [BLOCK[0], BLOCK[5]]) == []


def test_banner_seeds_headerless_block():
    p = StatusLogParser(seq_base=0)
    p.feed(BANNER, now=0)
    r = feed_all(p, BLOCK[1:])[0]                   # opened the port mid-block: no header line
    assert (r["chamber_id"], r["device_id"], r["boot_id"], r["firmware_version"]) == \
        ("A", "fermentai-esp32-a", "1185100b", "0.3.0")


def test_ph_only_reported_when_calibrated_and_ens160_never_gas():
    p = StatusLogParser(seq_base=0)
    unc = feed_all(p, [BLOCK[0], BLOCK[1], "  ph=4.20 mv=152.3 raw=2210 cal=0 door=open opens=3 setpoint=24.0 faults=0x4",
                       BLOCK[3].replace("tvoc=0", "tvoc=180").replace("eco2=0", "eco2=650"), BLOCK[5]])[0]
    assert unc["ph"] is None and unc["ph_method"] is None and "ph_uncalibrated" in unc["quality_flags"]
    assert unc["diagnostics"]["ph_mv"] == 152.3 and unc["door_open"] is True
    assert unc["temperature_setpoint_c"] == 24.0 and unc["fault_codes"] == ["faults=0x4"]
    assert unc["gas_resistance_ohms"] is None and unc["diagnostics"]["ens160"]["eco2_ppm"] == 650.0
    cal = feed_all(p, [BLOCK[0].replace("mode=idle out=0.00", "mode=heat out=0.35"), BLOCK[1],
                       "  ph=4.20 mv=152.3 raw=2210 cal=1 door=closed opens=3 setpoint=24.0 faults=0x0", BLOCK[5]])[0]
    assert cal["ph"] == 4.2 and cal["ph_method"] == "probe" and "ph_uncalibrated" not in cal["quality_flags"]
    assert cal["peltier_mode"] == "heat" and cal["peltier_output_fraction"] == 0.35
    TelemetryIn.model_validate(unc)
    TelemetryIn.model_validate(cal)


async def test_bridge_converts_legacy_board_output(tmp_path):
    app = make_app(tmp_path)
    api = asgi_client(app)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="usbmodem-A", chamber_id="A")],
                                 operator_token=TEST_TOKEN) as h:
            dev = RawDevice(s)
            for line in ["ESP-ROM:esp32s3-20210327", BANNER, *BLOCK]:
                await dev.send(line)
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 1, what="legacy telemetry")
            t = (await api.get("/api/v1/chambers/A/state")).json()["latest_telemetry"]
            assert t["source"] == "measured" and t["chamber_temperature_c"] == 28.6
            assert t["device_id"] == "fermentai-esp32-a" and "legacy_status_log" in t["quality_flags"]
            assert t["timestamp_utc"] is None and t["payload"]["pressure_pa"] is None
            st = h.bridge.status()["chambers"]["A"]
            assert st["link"] == "connected" and st["protocol"] == "legacy_status_log"
            assert st["boot_id"] == "1185100b" and st["firmware_version"] == "0.3.0"
            for line in BLOCK:                                   # next 30 s block
                await dev.send(line)
            await wait_for(lambda: h.session().counters["telemetry_ok"] == 2, what="second block")
            rows = (await api.get("/api/v1/chambers/A/telemetry")).json()
            assert rows[1]["sequence_number"] == rows[0]["sequence_number"] + 1
            await asyncio_sleep(0.8)                             # several poll intervals
            assert dev.of_type("command") == [] and dev.of_type("ping") == []   # console left alone
            assert len(dev.of_type("time")) <= 1                 # only the connect-time line
            # once the board speaks serial_v1, status text is no longer converted
            await dev.send(hello("A", boot_id="newfw"))
            for line in BLOCK:
                await dev.send(line)
            await asyncio_sleep(0.6)
            assert h.session().counters["telemetry_ok"] == 2 and h.session().protocol == "serial_v1"
            await dev.close()
    finally:
        await api.aclose()
        close_fds(m, s)


async def test_parse_status_log_can_be_disabled(tmp_path):
    app = make_app(tmp_path)
    m, s, _ = pty_pair()
    try:
        async with BridgeHarness(app, [PortSpec(fd=m, label="p", chamber_id="A")], operator_token=TEST_TOKEN,
                                 parse_status_log=False) as h:
            dev = RawDevice(s)
            for line in BLOCK:
                await dev.send(line)
            await wait_for(lambda: h.session().counters["rx_log_text"] == len(BLOCK), what="lines logged")
            await asyncio_sleep(0.3)
            assert h.session().counters["telemetry_ok"] == 0 and h.session().state == "logs_only"
            await dev.close()
    finally:
        close_fds(m, s)


async def asyncio_sleep(s):
    import asyncio
    await asyncio.sleep(s)
