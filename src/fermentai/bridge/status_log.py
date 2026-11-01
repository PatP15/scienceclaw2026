"""Convert the legacy plain-text status block of firmware 0.3.x into telemetry_v1 records.

Firmware 0.3.0 (Wi-Fi build, serial protocol hook not applied) prints, every 30 s:

    [A fermentai-esp32-a] fw=0.3.0 boot=1185100b reset=other state=waiting_for_command mode=idle out=0.00 armed(peltier=0 motor=1)
      chamber=28.60C rh=13.2% p=nanPa gas=nanohm starter=23.44C hs_int=nanC hs_ext=nanC
      ph=nan mv=0.0 raw=0 cal=0 door=closed opens=0 setpoint=nan faults=0x0
      ens160: present=1 addr=0x53 valid=1 warmup=0 aqi=0 tvoc=0 ppb eco2=0 ppm (indices, not CO2)
      flags: ds18b20_role_unassigned ph_out_of_range
      wifi=0 rssi=0 tel_q=0 acks_q=0 heap=239416 psram=8179891

and at boot a banner `FermentAI 0.3.0 chamber=A device=fermentai-esp32-a boot=1185100b ...`.

One telemetry record is produced per block (emitted on the `wifi=` line, on the next header, or
`block_timeout_s` after the block's last line). Continuation lines are recognised by their keys, so missing or
reordered lines are tolerated. Rules (only recorded values are reported):
  * "nan" -> null; units are stripped; source "measured"; quality flag "legacy_status_log";
  * pH is reported only when cal=1 (ph_method "probe"); otherwise ph=null + "ph_uncalibrated"
    (the raw mV/ADC stay in diagnostics);
  * ENS160 AQI/TVOC/eCO2 are indices -> diagnostics.ens160 only, NEVER gas_resistance_ohms;
  * motor_running is false unless a line explicitly says the motor is running ("armed" != running);
  * timestamp_utc null + "unsynced"; device_uptime_ms omitted (not printed);
  * sequence_number: counter per boot, offset by the bridge start time (seconds) so numbers stay
    unique when the bridge restarts during the same device boot (server dedup would drop repeats).
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

HEADER_RE = re.compile(r"^\[(?P<chamber>[A-Za-z0-9_-]{1,16})\s+(?P<device>[^\]\s]+)\]\s*(?P<rest>.*)$")
BANNER_RE = re.compile(r"^FermentAI\s+(?P<fw>\S+)\s+(?P<rest>.*)$")
KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s()]+)")
ARMED_RE = re.compile(r"armed\(\s*peltier=(\d)\s+motor=(\d)\s*\)")
NUM_RE = re.compile(r"^([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[-+]?nan|[-+]?inf)", re.IGNORECASE)
MEASURE_KEYS = {"chamber", "rh", "p", "gas", "starter", "hs_int", "hs_ext"}
PH_KEYS = {"ph", "mv", "raw", "cal", "door", "opens", "setpoint", "faults"}
WIFI_KEYS = {"wifi", "rssi", "tel_q", "acks_q", "heap", "psram"}
PELTIER_MODES = {"idle": "idle", "heat": "heat", "heating": "heat", "cool": "cool", "cooling": "cool",
                 "fault": "fault", "off": "off", "switching": "switching"}


def num(v: str | None) -> float | None:
    """'28.60C' -> 28.6, 'nanPa' -> None, '13.2%' -> 13.2, junk -> None."""
    if v is None:
        return None
    m = NUM_RE.match(v.strip())
    if not m:
        return None
    try:
        f = float(m.group(1))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def intval(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v, 0)
    except ValueError:
        f = num(v)
        return int(f) if f is not None else None


class StatusLogParser:
    def __init__(self, *, block_timeout_s: float = 5.0, clock=time.monotonic, seq_base: int | None = None):
        self.block_timeout_s = block_timeout_s
        self.clock = clock
        self.seq_base = int(time.time()) if seq_base is None else seq_base
        self.seq: dict[str, int] = {}             # boot_id -> blocks emitted this bridge run
        self.banner: dict[str, str] = {}
        self.block: dict[str, Any] | None = None
        self.block_started = 0.0
        self.block_last = 0.0                      # time of the block's most recent line
        self.blocks_emitted = 0
        self.lines_parsed = 0

    # ---------------------------------------------------------------- feeding
    def feed(self, line: str, now: float | None = None) -> list[dict]:
        """Feed one plain-text line; returns completed telemetry payloads (0, 1 or 2)."""
        now = self.clock() if now is None else now
        text = line.strip()
        out: list[dict] = []
        if not text:
            return out
        b = BANNER_RE.match(text)
        if b:
            kv = dict(KV_RE.findall(b.group("rest")))
            self.banner = {"firmware_version": b.group("fw"), **{k: kv[k] for k in ("chamber", "device", "boot")
                                                                if k in kv}}
            self.lines_parsed += 1
            return out
        h = HEADER_RE.match(text)
        if h:
            out += self.flush()
            kv = dict(KV_RE.findall(h.group("rest")))
            self.block = {"chamber": h.group("chamber"), "device": h.group("device"), "header": kv,
                          "armed": ARMED_RE.search(h.group("rest"))}
            self.block_started = self.block_last = now
            self.lines_parsed += 1
            return out
        kind = self._classify(text)
        if kind is None:
            return out                             # unrelated log text
        self.block_last = now
        if self.block is None:                     # opened mid-block: start a header-less block
            self.block = {"chamber": None, "device": None, "header": {}, "armed": None}
            self.block_started = now
        self.lines_parsed += 1
        if kind == "flags":
            toks = text.split(":", 1)[1].split()
            self.block["flags"] = [t for t in toks if t.lower() not in ("none", "-", "ok")]
        elif kind == "ens160":
            self.block["ens160"] = dict(KV_RE.findall(text.split(":", 1)[1]))
        else:
            self.block.setdefault(kind, {}).update(dict(KV_RE.findall(text)))
            if "running" in text.lower() and "motor" in text.lower():
                self.block["motor_running_hint"] = True
        if kind == "wifi":                         # last line of the block
            out += self.flush()
        return out

    @staticmethod
    def _classify(text: str) -> str | None:
        low = text.lower()
        if low.startswith("flags:"):
            return "flags"
        if low.startswith("ens160:"):
            return "ens160"
        keys = {k for k, _ in KV_RE.findall(text)}
        if keys & MEASURE_KEYS and ("chamber" in keys or "starter" in keys or "rh" in keys):
            return "measure"
        if "ph" in keys or ("cal" in keys and "door" in keys):
            return "ph"
        if keys & WIFI_KEYS and ("heap" in keys or "wifi" in keys):
            return "wifi"
        return None

    def flush_if_stale(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        if self.block is not None and now - self.block_last > self.block_timeout_s:
            return self.flush()
        return []

    def flush(self) -> list[dict]:
        blk, self.block = self.block, None
        if not blk or not (blk.get("measure") or blk.get("ph")):
            return []                              # header only / nothing measured: no record
        rec = self._to_telemetry(blk)
        return [rec] if rec else []

    def alloc_seq(self, boot: str) -> int:
        """Next sequence number for a boot (used for bridge-inferred records)."""
        n = self.seq.get(boot, 0) + 1
        self.seq[boot] = n
        return self.seq_base + n

    # ---------------------------------------------------------------- mapping
    def _to_telemetry(self, blk: dict) -> dict | None:
        hdr, m, ph = blk.get("header", {}), blk.get("measure", {}), blk.get("ph", {})
        wifi, ens = blk.get("wifi", {}), blk.get("ens160")
        chamber = blk.get("chamber") or self.banner.get("chamber")
        device = blk.get("device") or self.banner.get("device")
        if not chamber:
            return None
        boot = hdr.get("boot") or self.banner.get("boot") or "unknown"
        n = self.seq.get(boot, 0) + 1
        self.seq[boot] = n
        flags = list(blk.get("flags", [])) + ["legacy_status_log"]

        cal = intval(ph.get("cal"))
        ph_val = num(ph.get("ph"))
        ph_method = None
        if ph_val is not None and cal == 1:
            ph_method = "probe"
        elif ph_val is not None:
            ph_val = None
            flags.append("ph_uncalibrated")
        mode_raw = (hdr.get("mode") or "").lower()
        mode = PELTIER_MODES.get(mode_raw)
        if mode_raw and mode is None:
            flags.append(f"unknown_peltier_mode_{mode_raw}"[:64])
        out_frac = num(hdr.get("out"))
        if out_frac is not None and not (0.0 <= out_frac <= 1.0):
            flags.append("peltier_output_out_of_range")
            out_frac = None
        door = ph.get("door")
        door_open = {"open": True, "opened": True, "closed": False, "shut": False}.get((door or "").lower())
        faults = intval(ph.get("faults"))
        armed = blk.get("armed")

        diag: dict[str, Any] = {
            "firmware_version": hdr.get("fw") or self.banner.get("firmware_version"),
            "reset_reason": hdr.get("reset"), "state": hdr.get("state"),
            "armed": {"peltier": int(armed.group(1)), "motor": int(armed.group(2))} if armed else None,
            "fault_bits": ph.get("faults"), "ph_mv": num(ph.get("mv")), "ph_raw": intval(ph.get("raw")),
            "ph_cal": cal, "door_opens": intval(ph.get("opens")),
            "wifi_connected": bool(intval(wifi.get("wifi"))) if "wifi" in wifi else None,
            "free_heap_bytes": intval(wifi.get("heap")), "psram_free_bytes": intval(wifi.get("psram")),
            "telemetry_queue": intval(wifi.get("tel_q")), "ack_queue": intval(wifi.get("acks_q")),
            "source_format": "legacy_status_log",
        }
        if diag["wifi_connected"]:
            diag["wifi_rssi_dbm"] = num(wifi.get("rssi"))
        if ens is not None:   # metal-oxide indices, NOT CO2 and NOT BME680 gas resistance
            diag["ens160"] = {"present": intval(ens.get("present")), "valid": intval(ens.get("valid")),
                              "warmup": intval(ens.get("warmup")), "aqi": intval(ens.get("aqi")),
                              "tvoc_ppb": num(ens.get("tvoc")), "eco2_ppm": num(ens.get("eco2")),
                              "note": "ENS160 indices, not a CO2 measurement"}
        diag = {k: v for k, v in diag.items() if v is not None}

        return {
            "schema_version": "1.0",
            "device_id": device,
            "chamber_id": chamber,
            "boot_id": boot,
            "sequence_number": self.seq_base + n,
            "timestamp_utc": None,
            "timestamp_quality": "unsynced",
            "chamber_temperature_c": num(m.get("chamber")),
            "starter_temperature_c": num(m.get("starter")),
            "internal_heatsink_temperature_c": num(m.get("hs_int")),
            "external_heatsink_temperature_c": num(m.get("hs_ext")),
            "relative_humidity_percent": num(m.get("rh")),
            "pressure_pa": num(m.get("p")),
            "gas_resistance_ohms": num(m.get("gas")),
            "ph": ph_val,
            "ph_method": ph_method,
            "door_open": door_open,
            "peltier_mode": mode,
            "peltier_output_fraction": out_frac,
            "temperature_setpoint_c": num(ph.get("setpoint")),
            "motor_running": bool(blk.get("motor_running_hint")),
            "source": "measured",
            "quality_flags": list(dict.fromkeys(f[:64] for f in flags)),
            "fault_codes": [f"faults={ph.get('faults')}"] if faults else [],
            "firmware_version": diag.get("firmware_version"),
            "diagnostics": diag,
        }
