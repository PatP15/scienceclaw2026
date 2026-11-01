"""Structured observation adapter (PLAN_MASTER §9.1-§9.2).

Raw telemetry never goes straight to an agent or an LLM. Each record passes through:

    validity/unit checks -> timestamp handling -> per-sensor quality assessment
    -> normalized ChamberObservation -> (compare_to_prediction) -> agent interpretation

Every field carries ``{value, unit, source, quality, note, role, usable_as_evidence}`` where
``quality`` is one of ``ok | stale | missing | invalid | uncalibrated | low_confidence |
wrong_chamber | unsynced_time``. Scientific roles (what a value can and cannot be used for):

* ``starter_temperature``  controlled fermentation variable (is the dough following the schedule?)
* ``chamber_temperature``  environment around the jar; thermal lag vs. the starter
* heatsink temperatures    equipment safety ONLY - never evidence about microbes
* ``relative_humidity``    environment (drying/condensation), not acidity
* ``pressure``             environment
* ``gas_resistance``       BME680 broad headspace response; uncalibrated, NOT CO2, not a rate
* ``ph``                   acidity, valid only with a method AND calibration evidence
* ``starter_height``       camera expansion estimate with a confidence; not cumulative CO2
* ``door_open``            experimental disturbance
* peltier mode/output, setpoint   control inputs, compared with the actual temperature response
* ``motor_running``        decorative automaton; never a measurement

Missing physical observations are reported as ``missing`` and are never filled with simulated
values. Telemetry v1.0 (API schema) and v1.1 (PLAN_MASTER §4: device_id, boot_id,
sequence_number, nullable timestamp_utc + timestamp_quality, device_uptime_ms, heatsink
temperatures, pressure_pa, peltier_output_fraction, diagnostics, field_provenance,
source measured|simulated|replay) are both accepted.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

OBS_SCHEMA_VERSION = "obs-1.0"
Quality = Literal["ok", "stale", "missing", "invalid", "uncalibrated", "low_confidence", "wrong_chamber",
                  "unsynced_time"]
QUALITIES: tuple[str, ...] = Quality.__args__  # type: ignore[attr-defined]
USABLE_QUALITIES = {"ok"}
DOWNWEIGHT_QUALITIES = {"low_confidence", "uncalibrated"}
REJECT_QUALITIES = {"stale", "missing", "invalid", "wrong_chamber", "unsynced_time"}

DEFAULT_LIMITS: dict[str, Any] = {
    "stale_after_s": 600.0,  # latest reading older than this cannot describe the current state
    "setpoint_min_c": 4.0,
    "setpoint_max_c": 35.0,
    "starter_temp_range_c": (-5.0, 50.0),
    "chamber_temp_range_c": (-10.0, 60.0),
    "heatsink_range_c": (-30.0, 120.0),
    "external_heatsink_warn_c": 60.0,
    "external_heatsink_critical_c": 70.0,
    "internal_heatsink_icing_c": 0.0,
    "jar_height_mm": 150.0,
    "height_min_confidence": 0.6,
    "ph_plausible_range": (3.0, 7.5),
    "tracking_warn_c": 2.0,
    "thermal_lag_warn_c": 4.0,
}

# name -> (raw keys in priority order, unit, role, scientific meaning)
FIELD_SPECS: dict[str, tuple[tuple[str, ...], str | None, str, str]] = {
    "starter_temperature": (("starter_temperature_c",), "°C", "controlled_variable",
                            "Primary controlled fermentation temperature (dough/starter)."),
    "chamber_temperature": (("chamber_temperature_c",), "°C", "environment",
                            "Air around the jar; used to detect thermal lag vs. the starter."),
    "internal_heatsink_temperature": (("internal_heatsink_temperature_c",), "°C", "equipment_safety",
                                      "Peltier cold-side/internal heatsink: equipment safety only."),
    "external_heatsink_temperature": (("external_heatsink_temperature_c",), "°C", "equipment_safety",
                                      "Peltier hot-side/external heatsink: equipment safety only."),
    "heatsink_temperature": (("heatsink_temperature_c",), "°C", "equipment_safety",
                             "v1.0 single heatsink reading (placement not specified): equipment safety only."),
    "relative_humidity": (("relative_humidity_percent",), "%", "environment",
                          "Chamber humidity: drying/condensation context, not acidity."),
    "pressure": (("pressure_pa",), "Pa", "environment", "Ambient pressure (environment)."),
    "gas_resistance": (("gas_resistance_ohms",), "ohm", "headspace_response_uncalibrated",
                       "BME680 broad gas response; relative trend only, NOT CO2 and not a fermentation rate."),
    "ph": (("ph",), "pH", "acidity", "Acidity; valid only with a stated method and valid calibration."),
    "starter_height": (("starter_height_mm",), "mm", "expansion_estimate",
                       "Camera/distance estimate of visible expansion; not cumulative CO2."),
    "door_open": (("door_open",), None, "disturbance", "Door event: experimental disturbance."),
    "peltier_mode": (("peltier_mode",), None, "control_input", "Peltier mode (control input)."),
    "peltier_output_fraction": (("peltier_output_fraction", "peltier_duty_percent"), "fraction", "control_input",
                                "Peltier drive level 0..1 (control input)."),
    "temperature_setpoint": (("temperature_setpoint_c",), "°C", "control_input",
                             "Setpoint the device is applying (control input)."),
    "motor_running": (("motor_running",), None, "decorative", "Decorative chef-hat automaton; not a measurement."),
}
EVIDENCE_ROLES = {"controlled_variable", "environment", "acidity", "expansion_estimate",
                  "headspace_response_uncalibrated", "disturbance", "control_input"}
SCIENTIFIC_EVIDENCE_ROLES = {"controlled_variable", "environment", "acidity", "expansion_estimate"}
RECEIPT_KEYS = ("received_at_utc", "server_received_at_utc", "server_received_utc", "received_utc", "server_time_utc")


class FieldReading(BaseModel):
    name: str
    value: float | bool | str | None = None
    unit: str | None = None
    source: str = "unknown"
    quality: Quality = "missing"
    note: str = ""
    role: str = ""
    meaning: str = ""
    confidence: float | None = None
    usable_as_evidence: bool = False


class Anomaly(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    category: Literal["equipment_safety", "data_quality", "fermentation", "disturbance", "control"]
    message: str
    fields: list[str] = Field(default_factory=list)


class ChamberObservation(BaseModel):
    schema_version: str = OBS_SCHEMA_VERSION
    chamber_id: str
    record_chamber_id: str | None = None
    telemetry_version: str | None = None
    device_id: str | None = None
    boot_id: str | None = None
    sequence_number: int | None = None
    experiment_id: str | None = None
    timestamp_utc: str | None = None  # device wall clock, only if reported
    timestamp_quality: str = "missing"  # synced | unsynced | missing | invalid
    received_at_utc: str | None = None  # backend receipt time, if supplied
    effective_time_utc: str | None = None  # device time if synced, else receipt time
    time_basis: str = "none"  # device_utc | server_receipt | uptime_only | none
    device_uptime_ms: int | None = None
    age_s: float | None = None
    record_source: str = "unknown"  # measured | simulated | replay | unknown
    fields: dict[str, FieldReading] = Field(default_factory=dict)
    derived: dict[str, Any] = Field(default_factory=dict)
    anomalies: list[Anomaly] = Field(default_factory=list)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    record_quality_flags: list[str] = Field(default_factory=list)
    fault_codes: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def value(self, name: str, *, usable_only: bool = True) -> Any:
        f = self.fields.get(name)
        if f is None or f.value is None:
            return None
        if usable_only and f.quality != "ok":
            return None
        return f.value

    def compact(self) -> dict[str, Any]:
        """Small, LLM/dashboard-friendly view (no raw diagnostics)."""
        return {
            "chamber_id": self.chamber_id, "effective_time_utc": self.effective_time_utc, "time_basis": self.time_basis,
            "record_source": self.record_source, "age_s": self.age_s,
            "fields": {k: {"value": f.value, "unit": f.unit, "quality": f.quality, "role": f.role,
                           **({"note": f.note} if f.note else {})} for k, f in self.fields.items()},
            "derived": self.derived, "anomalies": [a.model_dump() for a in self.anomalies],
            "quality_summary": self.quality_summary,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_utc(x: Any) -> datetime | None:
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=UTC)
    if not isinstance(x, str) or not x.strip():
        return None
    try:
        d = datetime.fromisoformat(x.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(d: datetime | None) -> str | None:
    return None if d is None else d.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _norm_source(s: Any) -> str:
    s = str(s or "").strip().lower()
    if s in ("real", "measured", "measurement", "hardware", "sensor"):
        return "measured"
    if s in ("simulated", "simulation", "synthetic", "virtual", "model"):
        return "simulated"
    if s in ("replay", "replayed"):
        return "replay"
    if s in ("manual", "imported_human", "human", "operator"):
        return "manual"
    if s in ("estimated", "estimate"):
        return "estimated"
    return "unknown"


def _field_prov(record: dict[str, Any], raw_key: str, name: str) -> dict[str, Any]:
    fp = record.get("field_provenance")
    if not isinstance(fp, dict):
        return {}
    v = fp.get(raw_key, fp.get(name))
    if isinstance(v, str):
        return {"source": v}
    return v if isinstance(v, dict) else {}


def _diag(record: dict[str, Any]) -> dict[str, Any]:
    d = record.get("diagnostics")
    return d if isinstance(d, dict) else {}


def _flagged(flags: list[str], *needles: str) -> bool:
    low = [f.lower() for f in flags]
    return any(any(n in f for n in needles) for f in low)


# ---------------------------------------------------------------------------
# main adapter
# ---------------------------------------------------------------------------
def normalize_observation(
    telemetry_record: dict[str, Any],
    chamber_id: str | None = None,
    now_utc: str | datetime | None = None,
    hardware_limits: dict[str, Any] | None = None,
    *,
    evaluate_staleness: bool = True,
) -> ChamberObservation:
    """Normalize one telemetry record (v1.0 or v1.1) into a quality-annotated ChamberObservation."""
    lim = {**DEFAULT_LIMITS, **(hardware_limits or {})}
    rec = telemetry_record if isinstance(telemetry_record, dict) else {}
    rec_ch = rec.get("chamber_id")
    rec_ch = str(rec_ch) if rec_ch is not None else None
    expected = chamber_id or rec_ch or "unknown"
    flags = [str(f) for f in (rec.get("quality_flags") or []) if isinstance(f, (str, int, float))]
    faults = [str(f) for f in (rec.get("fault_codes") or []) if isinstance(f, (str, int, float))]
    diag = _diag(rec)
    obs = ChamberObservation(chamber_id=expected, record_chamber_id=rec_ch, record_quality_flags=flags,
                             fault_codes=faults, diagnostics=diag)
    obs.telemetry_version = str(rec.get("schema_version")) if rec.get("schema_version") is not None else None
    obs.device_id = rec.get("device_id") if isinstance(rec.get("device_id"), str) else None
    obs.boot_id = rec.get("boot_id") if isinstance(rec.get("boot_id"), str) else None
    seq = rec.get("sequence_number", rec.get("last_sequence_number"))
    obs.sequence_number = int(seq) if isinstance(seq, (int, float)) and not isinstance(seq, bool) else None
    obs.experiment_id = rec.get("experiment_id") if isinstance(rec.get("experiment_id"), str) else None
    up = _num(rec.get("device_uptime_ms"))
    if up is None and _num(rec.get("uptime_s")) is not None:
        up = _num(rec.get("uptime_s")) * 1000.0
    obs.device_uptime_ms = int(up) if up is not None and math.isfinite(up) and up >= 0 else None
    obs.record_source = _norm_source(rec.get("source"))
    if obs.record_source == "unknown":
        obs.notes.append("record source not stated; treated as unverified")

    # --- timestamps (timestamp stays null when unsynced) ---
    dev_t = _parse_utc(rec.get("timestamp_utc"))
    tq = str(rec.get("timestamp_quality") or "").lower()
    if rec.get("timestamp_utc") not in (None, "") and dev_t is None:
        obs.timestamp_quality = "invalid"
    elif dev_t is not None and tq in ("", "synced", "ntp", "gps", "rtc", "ok", "valid"):
        obs.timestamp_quality = "synced"
    elif dev_t is not None:
        obs.timestamp_quality = "unsynced"
    else:
        obs.timestamp_quality = "unsynced" if tq == "unsynced" else "missing"
    obs.timestamp_utc = _iso(dev_t)
    recv = next((_parse_utc(rec.get(k)) for k in RECEIPT_KEYS if _parse_utc(rec.get(k)) is not None), None)
    obs.received_at_utc = _iso(recv)
    eff = None
    if obs.timestamp_quality == "synced":
        eff, obs.time_basis = dev_t, "device_utc"
    elif recv is not None:
        eff, obs.time_basis = recv, "server_receipt"
        obs.notes.append("device clock not synced; timed by backend receipt")
    elif obs.device_uptime_ms is not None:
        obs.time_basis = "uptime_only"
        obs.notes.append("no wall-clock time; only relative timing within this boot (uptime) is available")
    obs.effective_time_utc = _iso(eff)
    now = _parse_utc(now_utc) if not isinstance(now_utc, datetime) else now_utc
    if now is not None and eff is not None:
        obs.age_s = round((now - eff).total_seconds(), 1)
    stale = bool(evaluate_staleness and obs.age_s is not None and obs.age_s > float(lim["stale_after_s"]))
    # no wall-clock at all, or (when judging the current state against now) no way to know the age
    no_time = obs.time_basis == "none" or (evaluate_staleness and now is not None and eff is None)

    # --- simulated data labeled as measured? ---
    dev_id = (obs.device_id or "").lower()
    sim_flagged_record = obs.record_source == "measured" and (
        _flagged(flags, "simulated", "synthetic", "virtual") or dev_id.startswith(("virtual", "sim-", "sim_", "fake")))
    if sim_flagged_record:
        obs.anomalies.append(Anomaly(code="simulated_labelled_as_measured", severity="critical", category="data_quality",
                                     message="Record says 'measured' but carries simulation markers; all values rejected."))
    wrong_chamber = rec_ch is not None and chamber_id is not None and rec_ch != chamber_id
    if wrong_chamber:
        obs.anomalies.append(Anomaly(code="wrong_chamber", severity="critical", category="data_quality",
                                     message=f"Record is from chamber {rec_ch}, not {chamber_id}; values rejected.",
                                     fields=[]))

    door_open = rec.get("door_open") is True or _flagged(flags, "door_open")

    for name, (keys, unit, role, meaning) in FIELD_SPECS.items():
        raw_key = next((k for k in keys if k in rec), keys[0])
        raw = rec.get(raw_key)
        prov = _field_prov(rec, raw_key, name)
        src = _norm_source(prov.get("source")) if prov.get("source") else obs.record_source
        fr = FieldReading(name=name, unit=unit, role=role, meaning=meaning, source=src)
        notes: list[str] = []
        q = "ok"
        val: Any = raw
        # ---- presence / type / range ----
        if raw is None:
            q = "missing"
            if name in ("heatsink_temperature",) and ("internal_heatsink_temperature_c" in rec or "external_heatsink_temperature_c" in rec):
                notes.append("v1.1 split heatsink fields used instead")
        elif name in ("door_open", "motor_running"):
            if not isinstance(raw, bool):
                q, notes = "invalid", [f"expected boolean, got {raw!r}"]
        elif name == "peltier_mode":
            if not isinstance(raw, str) or raw.lower() not in ("idle", "heat", "heating", "cool", "cooling", "switching",
                                                                "fault", "off"):
                q, notes = "invalid", [f"unknown peltier mode {raw!r}"]
            else:
                val = {"heating": "heat", "cooling": "cool"}.get(raw.lower(), raw.lower())
        else:
            v = _num(raw)
            if v is None or not math.isfinite(v):
                q, notes, val = "invalid", [f"non-numeric or non-finite value {raw!r}"], None
            else:
                val = v
                if name == "peltier_output_fraction" and raw_key == "peltier_duty_percent":
                    val = v / 100.0
                    notes.append("converted from peltier_duty_percent")
                q, rn = _range_check(name, val, lim)
                notes += rn
        # ---- sensor-specific quality rules (only if the value itself is sane) ----
        if q == "ok":
            if name in ("starter_temperature", "chamber_temperature", "internal_heatsink_temperature",
                        "external_heatsink_temperature", "heatsink_temperature"):
                if val in (85.0, -127.0):
                    q = "invalid"
                    notes.append("DS18B20 power-on/disconnected sentinel value")
                elif name == "starter_temperature" and _flagged(flags, "ds18b20_disconnected", "starter_probe"):
                    q, notes = "invalid", notes + ["probe reported disconnected"]
                elif name == "chamber_temperature" and _flagged(flags, "bme680_read_failed", "sht31_read_failed"):
                    q, notes = "invalid", notes + ["environmental sensor read failed"]
                if name == "heatsink_temperature" and q == "ok":
                    q = "low_confidence"
                    notes.append("single v1.0 heatsink reading; placement (hot/cold side) not identified")
            if name in ("relative_humidity", "pressure", "gas_resistance") and _flagged(flags, "bme680_read_failed"):
                q, notes = "invalid", notes + ["BME680 read failed"]
            if name == "gas_resistance" and q == "ok":
                q = "uncalibrated"
                notes.append("relative headspace response only; NOT a CO2 concentration or fermentation rate")
                if _flagged(flags, "warming_up", "warm_up", "warmup"):
                    q = "low_confidence"
                    notes.append("BME680 heater warming up")
            if name == "ph":
                q, pn = _ph_quality(rec, prov, flags, diag)
                notes += pn
            if name == "starter_height":
                conf = _height_confidence(rec, prov, diag)
                fr.confidence = conf
                if _flagged(flags, "height_estimate_low_confidence", "camera_moved", "low_light", "camera_fail"):
                    q, notes = "low_confidence", notes + ["camera/height quality flag set"]
                elif conf is not None and conf < float(lim["height_min_confidence"]):
                    q, notes = "low_confidence", notes + [f"height confidence {conf:.2f} < {lim['height_min_confidence']}"]
                elif door_open:
                    q, notes = "low_confidence", notes + ["door open during reading (lighting/geometry disturbed)"]
                elif conf is None:
                    notes.append("no confidence reported for the height estimate")
            if name in ("starter_temperature", "chamber_temperature", "relative_humidity") and door_open and q == "ok":
                notes.append("door open: transient disturbance")
            if name == "starter_temperature" and _flagged(flags, "sensor_warming_up") and q == "ok":
                q, notes = "low_confidence", notes + ["sensor warming up"]
        # ---- record-level gates ----
        if q not in ("missing", "invalid"):
            if wrong_chamber:
                q, notes = "wrong_chamber", notes + [f"record belongs to chamber {rec_ch}"]
            elif prov.get("source") and _norm_source(prov.get("source")) == "simulated" and obs.record_source == "measured":
                q, notes = "invalid", notes + ["field provenance says simulated inside a 'measured' record"]
                obs.anomalies.append(Anomaly(code="simulated_field_in_measured_record", severity="critical",
                                             category="data_quality", fields=[name],
                                             message=f"{name} is simulated but the record is labeled measured; rejected."))
            elif sim_flagged_record:
                q, notes = "invalid", notes + ["record flagged as simulated data labeled measured"]
            elif stale and role not in ("decorative",):
                q, notes = "stale", notes + [f"reading is {obs.age_s:.0f} s old (> {lim['stale_after_s']:.0f} s)"]
            elif no_time and role in SCIENTIFIC_EVIDENCE_ROLES:
                q, notes = "unsynced_time", notes + [
                    "no device UTC, receipt time or uptime: cannot place in time" if obs.time_basis == "none"
                    else "no synced device clock or receipt time: age of this reading cannot be verified"]
        fr.value = val if q != "invalid" or isinstance(val, (int, float, bool, str)) else None
        fr.quality = q  # type: ignore[assignment]
        fr.note = "; ".join(dict.fromkeys(n for n in notes if n))
        fr.usable_as_evidence = q == "ok" and role in EVIDENCE_ROLES
        obs.fields[name] = fr

    _derive(obs, lim, door_open, faults, flags)
    counts: dict[str, int] = {}
    for f in obs.fields.values():
        counts[f.quality] = counts.get(f.quality, 0) + 1
    obs.quality_summary = {
        "counts": counts,
        "usable": sorted(k for k, f in obs.fields.items() if f.quality == "ok" and f.role in EVIDENCE_ROLES),
        "downweighted": sorted(k for k, f in obs.fields.items() if f.quality in DOWNWEIGHT_QUALITIES),
        "rejected": sorted(k for k, f in obs.fields.items() if f.quality in REJECT_QUALITIES - {"missing"}),
        "missing": sorted(k for k, f in obs.fields.items() if f.quality == "missing"),
        "ph_valid": obs.fields["ph"].quality == "ok",
        "time_basis": obs.time_basis,
    }
    return obs


def _range_check(name: str, v: float, lim: dict[str, Any]) -> tuple[str, list[str]]:
    rng = {
        "starter_temperature": lim["starter_temp_range_c"], "chamber_temperature": lim["chamber_temp_range_c"],
        "internal_heatsink_temperature": lim["heatsink_range_c"], "external_heatsink_temperature": lim["heatsink_range_c"],
        "heatsink_temperature": lim["heatsink_range_c"], "relative_humidity": (0.0, 100.0),
        "pressure": (30000.0, 120000.0), "gas_resistance": (1.0, 1e9), "ph": (0.0, 14.0),
        "starter_height": (0.0, float(lim["jar_height_mm"])), "peltier_output_fraction": (0.0, 1.0),
        "temperature_setpoint": (-40.0, 100.0),
    }.get(name)
    if rng is None:
        return "ok", []
    lo, hi = rng
    if not (lo <= v <= hi) or (name in ("starter_height", "gas_resistance") and v <= 0):
        return "invalid", [f"{v:g} outside physical range [{lo:g}, {hi:g}]"]
    return "ok", []


def _truthy(x: Any) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in ("true", "valid", "yes", "ok", "calibrated")
    return None


def _ph_quality(rec, prov, flags, diag) -> tuple[str, list[str]]:
    method = rec.get("ph_method") or prov.get("method")
    if _flagged(flags, "ph_drift", "ph_contaminat", "ph_fouled", "ph_unstable", "ph_electrode_fault"):
        return "invalid", ["electrode flagged drifting/contaminated: not reliable evidence"]
    v = _num(rec.get("ph"))
    if v is not None and not (DEFAULT_LIMITS["ph_plausible_range"][0] <= v <= DEFAULT_LIMITS["ph_plausible_range"][1]):
        return "invalid", [f"pH {v:g} implausible for a sourdough starter"]
    if not method:
        return "uncalibrated", ["no ph_method stated; reading cannot be interpreted"]
    if _flagged(flags, "ph_uncalibrated"):
        return "uncalibrated", ["device flagged ph_uncalibrated"]
    cal = None
    for src in (rec, prov, diag, diag.get("ph") if isinstance(diag.get("ph"), dict) else {}):
        for key in ("ph_calibration_valid", "calibration_valid", "calibrated", "ph_calibrated"):
            if key in src:
                cal = _truthy(src[key])
                break
        if cal is not None:
            break
    if cal is None and (prov.get("calibration_id") or diag.get("ph_calibration_date") or diag.get("ph_calibrated_at_utc")):
        cal = True
    if method in ("strip", "manual") and cal is None:
        return "low_confidence", [f"method '{method}' without calibration record (manual/strip reading)"]
    if cal is not True:
        return "uncalibrated", ["no valid calibration record (pH 4/7) for the electrode"]
    return "ok", [f"method {method}; calibration valid"]


def _height_confidence(rec, prov, diag) -> float | None:
    for src, key in ((rec, "starter_height_confidence"), (prov, "confidence"), (diag, "height_confidence"),
                     (diag, "starter_height_confidence")):
        v = _num(src.get(key)) if isinstance(src, dict) else None
        if v is not None and math.isfinite(v):
            return max(0.0, min(1.0, v))
    return None


def _derive(obs: ChamberObservation, lim: dict[str, Any], door_open: bool, faults: list[str], flags: list[str]) -> None:
    f = obs.fields
    st, ch, sp = obs.value("starter_temperature"), obs.value("chamber_temperature"), obs.value("temperature_setpoint")
    if st is not None and ch is not None:
        obs.derived["thermal_lag_c"] = round(ch - st, 2)
        if abs(ch - st) > lim["thermal_lag_warn_c"]:
            obs.anomalies.append(Anomaly(code="large_thermal_lag", severity="info", category="control",
                                         fields=["chamber_temperature", "starter_temperature"],
                                         message=f"Air and starter differ by {ch - st:+.1f} °C (thermal lag of the jar)."))
    if st is not None and sp is not None:
        err = round(st - sp, 2)
        obs.derived["tracking_error_c"] = err
        if abs(err) > lim["tracking_warn_c"]:
            obs.anomalies.append(Anomaly(code="setpoint_tracking_error", severity="warning", category="control",
                                         fields=["starter_temperature", "temperature_setpoint"],
                                         message=f"Starter is {err:+.1f} °C from the setpoint; schedule not being followed."))
        mode = obs.value("peltier_mode")
        if mode == "heat" and st > sp + 1.0 or mode == "cool" and st < sp - 1.0:
            obs.anomalies.append(Anomaly(code="control_direction_mismatch", severity="warning", category="control",
                                         fields=["peltier_mode", "starter_temperature", "temperature_setpoint"],
                                         message=f"Peltier is in '{mode}' although the starter is on the other side of the setpoint."))
    spv = f["temperature_setpoint"].value
    if isinstance(spv, float) and f["temperature_setpoint"].quality not in ("invalid",) and not (
            lim["setpoint_min_c"] <= spv <= lim["setpoint_max_c"]):
        obs.anomalies.append(Anomaly(code="setpoint_out_of_bounds", severity="critical", category="control",
                                     fields=["temperature_setpoint"],
                                     message=f"Applied setpoint {spv:g} °C outside {lim['setpoint_min_c']:g}-{lim['setpoint_max_c']:g} °C."))
    for name in ("external_heatsink_temperature", "heatsink_temperature"):
        hv = f[name].value
        if isinstance(hv, float) and f[name].quality not in ("invalid", "missing", "wrong_chamber"):
            if hv >= lim["external_heatsink_critical_c"]:
                obs.anomalies.append(Anomaly(code="heatsink_overtemperature", severity="critical", category="equipment_safety",
                                             fields=[name], message=f"{name} {hv:.1f} °C: equipment safety action "
                                             "(check fan/airflow, reduce Peltier drive). Not evidence about fermentation."))
            elif hv >= lim["external_heatsink_warn_c"]:
                obs.anomalies.append(Anomaly(code="heatsink_hot", severity="warning", category="equipment_safety",
                                             fields=[name], message=f"{name} {hv:.1f} °C: run equipment diagnostics. "
                                             "Not evidence about fermentation."))
    iv = f["internal_heatsink_temperature"].value
    if isinstance(iv, float) and f["internal_heatsink_temperature"].quality == "ok" and iv <= lim["internal_heatsink_icing_c"]:
        obs.anomalies.append(Anomaly(code="cold_side_icing_risk", severity="warning", category="equipment_safety",
                                     fields=["internal_heatsink_temperature"],
                                     message=f"Cold-side heatsink {iv:.1f} °C: icing/condensation risk (equipment)."))
    if faults:
        obs.anomalies.append(Anomaly(code="device_fault", severity="critical", category="equipment_safety",
                                     message=f"Device reports local safety faults: {', '.join(faults)}."))
    if door_open:
        obs.anomalies.append(Anomaly(code="door_open", severity="info", category="disturbance", fields=["door_open"],
                                     message="Door open: temperature, humidity and camera readings are disturbed."))
    rh = obs.value("relative_humidity")
    if isinstance(rh, float) and rh >= 95:
        obs.anomalies.append(Anomaly(code="condensation_risk", severity="info", category="disturbance",
                                     fields=["relative_humidity"], message=f"RH {rh:.0f}%: condensation possible "
                                     "(may fog the camera window); not an acidity signal."))
    for name, fr in f.items():
        if fr.quality == "invalid" and fr.role in SCIENTIFIC_EVIDENCE_ROLES | {"control_input"}:
            obs.anomalies.append(Anomaly(code=f"invalid_{name}", severity="warning", category="data_quality",
                                         fields=[name], message=f"{name} rejected: {fr.note}"))
    if obs.time_basis == "none":
        obs.anomalies.append(Anomaly(code="no_timestamp", severity="warning", category="data_quality",
                                     message="No device time, receipt time or uptime: reading cannot be placed in time."))


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
def _dedupe_key(r: dict[str, Any]) -> tuple | None:
    if r.get("boot_id") is not None and r.get("sequence_number") is not None:
        return (r.get("device_id"), r.get("boot_id"), r.get("sequence_number"))
    return None


def normalize_history(
    records: list[dict[str, Any]] | None,
    chamber_id: str | None = None,
    now_utc: str | datetime | None = None,
    hardware_limits: dict[str, Any] | None = None,
) -> list[ChamberObservation]:
    """Normalize, de-duplicate (device_id, boot_id, sequence_number) and order a telemetry history.

    Staleness is a property of the *current* state: only the latest observation is checked
    against ``now_utc``; older history points stay usable as a time series. Each observation
    gets ``derived["elapsed_h"]`` on the best available common time basis (UTC, else uptime
    within one boot) or None.
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    if chamber_id is None:
        ids = [r.get("chamber_id") for r in recs if r.get("chamber_id")]
        chamber_id = max(set(ids), key=ids.count) if ids else None
    seen: set = set()
    uniq = []
    for r in recs:
        k = _dedupe_key(r)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        uniq.append(r)
    pairs = [(r, normalize_observation(r, chamber_id, now_utc, hardware_limits, evaluate_staleness=False))
             for r in uniq]
    boot_order: dict[Any, int] = {}
    for _, o in pairs:
        boot_order.setdefault(o.boot_id, len(boot_order))
    timed = all(o.effective_time_utc for _, o in pairs)
    if timed:
        pairs.sort(key=lambda p: (_parse_utc(p[1].effective_time_utc), p[1].sequence_number or 0))
    else:  # arrival order within boots, then uptime / sequence number (wall-clock unset)
        pairs.sort(key=lambda p: (boot_order[p[1].boot_id], p[1].device_uptime_ms or 0, p[1].sequence_number or 0))
    obs = [o for _, o in pairs]
    if obs and timed:
        t0 = _parse_utc(obs[0].effective_time_utc)
        for o in obs:
            o.derived["elapsed_h"] = round((_parse_utc(o.effective_time_utc) - t0).total_seconds() / 3600.0, 4)
            o.derived["elapsed_basis"] = "utc"
    elif obs and len({o.boot_id for o in obs}) == 1 and all(o.device_uptime_ms is not None for o in obs):
        u0 = obs[0].device_uptime_ms
        for o in obs:
            o.derived["elapsed_h"] = round((o.device_uptime_ms - u0) / 3.6e6, 4)
            o.derived["elapsed_basis"] = "uptime"
    else:
        for o in obs:
            o.derived["elapsed_h"] = None
            o.derived["elapsed_basis"] = "none"
    # staleness describes the *current* state: re-check only the latest record against now
    if obs and now_utc is not None:
        latest = normalize_observation(pairs[-1][0], chamber_id, now_utc, hardware_limits, evaluate_staleness=True)
        latest.derived.update({k: v for k, v in obs[-1].derived.items() if k.startswith("elapsed")})
        obs[-1] = latest
    return obs


def summarize_history(obs: list[ChamberObservation]) -> dict[str, Any]:
    """Quality summary of a history for agents, prompts and the dashboard."""
    if not obs:
        return {"n": 0, "note": "no telemetry", "ph_valid": False, "ph_reason": "no telemetry this cycle"}
    latest = obs[-1]
    counts: dict[str, dict[str, int]] = {}
    for o in obs:
        for k, f in o.fields.items():
            counts.setdefault(k, {}).setdefault(f.quality, 0)
            counts[k][f.quality] += 1
    phs = [o for o in obs if o.fields["ph"].quality == "ok"]
    ph_any = [o.fields["ph"] for o in obs if o.fields["ph"].value is not None]
    if phs:
        ph_reason = None
    elif ph_any:
        ph_reason = f"pH readings present but not usable ({ph_any[-1].quality}: {ph_any[-1].note})"
    else:
        ph_reason = "no pH reading for this chamber (single shared electrode)"
    anomalies: dict[str, dict[str, Any]] = {}
    for o in obs:
        for a in o.anomalies:
            anomalies[a.code] = a.model_dump() | {"count": anomalies.get(a.code, {}).get("count", 0) + 1}
    return {
        "n": len(obs),
        "sources": sorted({o.record_source for o in obs}),
        "time_basis": sorted({o.derived.get("elapsed_basis", "none") for o in obs}),
        "latest_effective_time_utc": latest.effective_time_utc,
        "latest_age_s": latest.age_s,
        "latest_stale": any(f.quality == "stale" for f in latest.fields.values()),
        "latest_usable": latest.quality_summary.get("usable", []),
        "field_quality_counts": counts,
        "ph_valid": bool(phs),
        "ph_reason": ph_reason,
        "latest_valid_ph": phs[-1].fields["ph"].value if phs else None,
        "anomalies": list(anomalies.values()),
    }


# ---------------------------------------------------------------------------
# comparison with a prediction
# ---------------------------------------------------------------------------
def _series(predicted: Any) -> dict[str, list[float]] | None:
    if predicted is None:
        return None
    if hasattr(predicted, "columns") and hasattr(predicted, "__getitem__"):
        return {c: [float(x) for x in predicted[c]] for c in predicted.columns
                if c in ("time_h", "temperature_c", "ph", "expansion_ratio")}
    if isinstance(predicted, dict) and "time_h" in predicted:
        return {k: [float(x) for x in v] for k, v in predicted.items()
                if k in ("time_h", "temperature_c", "ph", "expansion_ratio") and isinstance(v, (list, tuple))}
    if isinstance(predicted, list) and predicted and isinstance(predicted[0], dict):
        keys = [k for k in ("time_h", "temperature_c", "ph", "expansion_ratio") if k in predicted[0]]
        return {k: [float(r[k]) for r in predicted] for k in keys}
    return None


def _interp(ts: list[float], ys: list[float], t: float) -> float | None:
    if not ts or t < ts[0] - 1e-9 or t > ts[-1] + 1e-9:
        return None
    for (t0, y0), (t1, y1) in zip(zip(ts, ys), zip(ts[1:], ys[1:])):
        if t0 <= t <= t1:
            return y0 if t1 == t0 else y0 + (y1 - y0) * (t - t0) / (t1 - t0)
    return ys[-1]


def compare_to_prediction(observation_history: list[ChamberObservation], predicted_df: Any,
                          start_offset_h: float = 0.0) -> dict[str, Any]:
    """Deviations of quality-approved observations from a prediction (simulator DataFrame/dict/rows).

    Only fields with quality ``ok`` are compared; nothing is imputed. Elapsed time comes from
    the history's common time basis (UTC or same-boot uptime) plus ``start_offset_h``.
    Returns temperature tracking error (starter vs. predicted/scheduled temperature and vs.
    setpoint), pH residuals and relative-expansion residuals where comparable, plus flags.
    """
    out: dict[str, Any] = {"comparable": False, "flags": [], "notes": [], "provenance":
                           "observations (quality-gated) vs model prediction (simulated)"}
    pred = _series(predicted_df)
    obs = [o for o in observation_history or [] if isinstance(o, ChamberObservation)]
    if not obs:
        out["notes"].append("no observations")
        return out
    if not pred or "time_h" not in pred:
        out["notes"].append("no usable prediction series")
        return out
    if any(o.derived.get("elapsed_h") is None for o in obs):
        out["notes"].append("observations lack a common time basis (no synced UTC and no single-boot uptime)")
        return out
    out["comparable"] = True
    out["time_basis"] = obs[0].derived.get("elapsed_basis")
    ts = pred["time_h"]
    temp_res, sp_res, ph_res, exp_res = [], [], [], []
    h0 = next((o.value("starter_height") for o in obs if o.value("starter_height") is not None), None)
    excluded = {"temperature": 0, "ph": 0, "height": 0}
    for o in obs:
        t = o.derived["elapsed_h"] + start_offset_h
        st = o.value("starter_temperature")
        if st is not None and "temperature_c" in pred:
            p = _interp(ts, pred["temperature_c"], t)
            if p is not None:
                temp_res.append((t, st - p))
        elif o.fields["starter_temperature"].value is not None:
            excluded["temperature"] += 1
        sp = o.value("temperature_setpoint")
        if st is not None and sp is not None:
            sp_res.append((t, st - sp))
        ph = o.value("ph")
        if ph is not None and "ph" in pred:
            p = _interp(ts, pred["ph"], t)
            if p is not None:
                ph_res.append((t, ph - p))
        elif o.fields["ph"].value is not None:
            excluded["ph"] += 1
        hv = o.value("starter_height")
        if hv is not None and h0 and "expansion_ratio" in pred:
            p = _interp(ts, pred["expansion_ratio"], t)
            if p is not None:
                exp_res.append((t, hv / h0 - p))
        elif o.fields["starter_height"].value is not None:
            excluded["height"] += 1

    def stats(res, unit):
        if not res:
            return {"n": 0}
        vals = [r for _, r in res]
        return {"n": len(vals), "bias": round(sum(vals) / len(vals), 3),
                "mean_abs": round(sum(abs(v) for v in vals) / len(vals), 3),
                "max_abs": round(max(abs(v) for v in vals), 3), "latest": round(vals[-1], 3),
                "latest_t_h": round(res[-1][0], 3), "unit": unit}

    out["temperature_vs_prediction"] = stats(temp_res, "°C (observed starter - predicted)")
    out["setpoint_tracking"] = stats(sp_res, "°C (starter - setpoint)")
    out["ph"] = stats(ph_res, "pH (observed - predicted)")
    out["expansion"] = stats(exp_res, "relative height h/h0 - predicted expansion ratio")
    out["excluded_by_quality"] = excluded
    if not ph_res:
        out["ph"]["note"] = "no valid pH observation to compare; model pH is unverified"
    if not exp_res:
        out["expansion"]["note"] = ("height not comparable (missing, low-confidence or no baseline)"
                                    if not h0 else "no overlap with prediction window")
    else:
        out["expansion"]["note"] = "relative visible expansion vs model expansion ratio; not CO2"
    tv = out["temperature_vs_prediction"]
    if tv.get("n") and abs(tv["bias"]) > 1.0:
        out["flags"].append("starter_not_following_planned_temperature")
    stv = out["setpoint_tracking"]
    if stv.get("n") and abs(stv["latest"]) > DEFAULT_LIMITS["tracking_warn_c"]:
        out["flags"].append("setpoint_tracking_error")
    if out["ph"].get("n"):
        if out["ph"]["latest"] > 0.2:
            out["flags"].append("acidifying_slower_than_predicted")
        elif out["ph"]["latest"] < -0.2:
            out["flags"].append("acidifying_faster_than_predicted")
    if out["expansion"].get("n"):
        if out["expansion"]["latest"] < -0.25:
            out["flags"].append("rising_slower_than_predicted")
        elif out["expansion"]["latest"] > 0.25:
            out["flags"].append("rising_faster_than_predicted")
    return out
