"""Validated candidate library of fermentation temperature schedules.

Schedules are lists of piecewise-linear knots ``[(time_h, temp_c), ...]``:

* the first knot is at ``t = 0``;
* temperatures are linearly interpolated between knots (a "step" is written as a
  hold knot followed by a short 0.5 h ramp, which is also closer to what a Peltier
  chamber can physically do);
* the last knot marks the planned end of the fermentation window.

Agents (offline policy or LLM) may only *select* schedules from this library by id;
they never emit raw temperatures. The API/firmware additionally clamp every setpoint,
so nothing here can bypass thermal safety (PLAN §7, §18).

Predictions
-----------
``predict_schedule`` returns *relative* indices (0..1) used to rank candidates:

* If ``fermentai.fermentation.simulation.simulate`` is importable, it is used
  (provenance ``"simulated"``).
* Otherwise a transparent degree-hour heuristic is used (provenance ``"estimated"``).
  It is NOT a calibrated kinetic model; constants below are labeled assumptions.

Heuristic kinetics reasoning (both assumptions, documented for judges):
* Warmer -> faster lactic acid bacteria (LAB) acidification and faster yeast gas
  production; LAB acidification is assumed somewhat more temperature-sensitive than
  yeast rise in the 18-30 °C range (Q10_ACID > Q10_RISE), so short warm phases buy
  more acid per unit of rise.
* Rise progress well past the peak (overproofing) collapses structure -> denser crumb.
  Too little progress (underproofing) -> tight/gummy crumb.
* Cooler and longer phases are slower overall but are assumed to shift the acid
  balance toward acetic acid, which is perceived as sharper/more "sour".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# ---------------------------------------------------------------------------
# Hard bounds (also enforced independently by the API and the ESP32 firmware)
# ---------------------------------------------------------------------------
MIN_TEMP_C = 4.0
MAX_TEMP_C = 35.0
HOT_THRESHOLD_C = 27.0
MAX_HOT_HOLD_H = 10.0  # never hold >= 27 °C longer than this without a step-down
MAX_DURATION_H = 18.0  # experiment window is 12-16 h; allow a little slack
MAX_RAMP_C_PER_H = 20.0

# ---------------------------------------------------------------------------
# Heuristic model constants (assumptions, not fitted)
# ---------------------------------------------------------------------------
REF_TEMP_C = 22.0
Q10_ACID = 3.0  # assumption: LAB acidification temperature sensitivity
Q10_RISE = 2.4  # assumption: yeast/LAB gas production temperature sensitivity
ACID_REF_H = 12.0  # assumption: hours at 22 °C for one unit of acid progress
RISE_PEAK_REF_H = 11.0  # assumption: hours at 22 °C (~9 % prefermented flour) to peak rise
OPTIMAL_RISE_PROGRESS = 1.1  # end slightly after peak (ready to bake)
ACETIC_PIVOT_C = 24.0  # below this, acetic share assumed to grow
ACETIC_BONUS = 0.25  # assumption: perceived-sourness weight of acetic-leaning (cool) time
HEURISTIC_MODEL_ID = "degree_hour_heuristic_v1"


@dataclass(frozen=True)
class CandidateSchedule:
    id: str
    name: str
    family: str  # steady | warm_short | warm_then_cool | cool_then_warm | step_down | cool_retard
    route: str  # which mechanism the schedule mainly probes
    points: tuple[tuple[float, float], ...]
    summary: str  # expected effect summary, tied to kinetics reasoning
    mechanism: str  # mechanism/hypothesis this schedule tests
    notes: str = ""

    @property
    def duration_h(self) -> float:
        return float(self.points[-1][0])

    def as_list(self) -> list[list[float]]:
        return [[float(t), float(c)] for t, c in self.points]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "route": self.route,
            "points": self.as_list(),
            "duration_h": self.duration_h,
            "summary": self.summary,
            "mechanism": self.mechanism,
            "notes": self.notes,
        }


def _steady(temp: float, hours: float) -> tuple[tuple[float, float], ...]:
    return ((0.0, temp), (hours, temp))


def _steps(*segments: tuple[float, float], ramp_h: float = 0.5) -> tuple[tuple[float, float], ...]:
    """Build knots from (temp_c, hold_h) segments with short ramps between them."""
    pts: list[tuple[float, float]] = []
    t = 0.0
    for i, (temp, hold) in enumerate(segments):
        if i == 0:
            pts.append((0.0, temp))
        else:
            t += ramp_h
            pts.append((round(t, 3), temp))
        t += hold
        pts.append((round(t, 3), temp))
    return tuple(pts)


ROUTE_TEXT = {
    "baseline": "baseline/control condition",
    "total_acid_warm": "warm phase front-loads lactic acidification (total acid route)",
    "acetic_cool": "long cool phase shifts acid balance toward acetic (sharper sourness route)",
    "overproof_protection": "step-down before peak rise protects crumb structure",
    "dose_response": "fills a gap in the temperature dose-response",
}

LIBRARY: tuple[CandidateSchedule, ...] = (
    CandidateSchedule(
        id="steady_18_16h", name="Steady 18 °C, 16 h", family="steady", route="acetic_cool",
        points=_steady(18.0, 16.0),
        summary="Slow acidification and slow rise; long cool hold may favor acetic character; "
                "risk of underproofing if the starter is weak.",
        mechanism="Cool, long fermentation increases acetic share of acids and perceived sharpness "
                  "while keeping rise slow enough to avoid overproofing.",
        notes="Requires ~4 °C of active cooling below a typical 22 °C room.",
    ),
    CandidateSchedule(
        id="steady_20_14h", name="Steady 20 °C, 14 h", family="steady", route="acetic_cool",
        points=_steady(20.0, 14.0),
        summary="Slower than 22 °C; mild-to-moderate acidity with a slightly higher acetic share; "
                "low overproof risk.",
        mechanism="Slightly cooler steady fermentation lowers total acid but raises acetic share.",
    ),
    CandidateSchedule(
        id="steady_22_14h", name="Steady 22 °C, 14 h", family="steady", route="baseline",
        points=_steady(22.0, 14.0),
        summary="Original chamber A hypothesis: moderate rise, relatively mild acidity.",
        mechanism="Baseline mild condition (control).",
    ),
    CandidateSchedule(
        id="steady_24_12h", name="Steady 24 °C, 12 h", family="steady", route="dose_response",
        points=_steady(24.0, 12.0),
        summary="Intermediate: faster acidification than 22 °C; moderate risk of passing peak rise "
                "near the end of the window.",
        mechanism="Intermediate temperature tests whether sourness rises smoothly with temperature.",
    ),
    CandidateSchedule(
        id="steady_26_10h", name="Steady 26 °C, 10 h", family="steady", route="total_acid_warm",
        points=_steady(26.0, 10.0),
        summary="Warm and shorter: noticeably faster acidification; shorter window to limit "
                "overproofing.",
        mechanism="Warm steady fermentation, shortened to end near peak rise.",
    ),
    CandidateSchedule(
        id="steady_27_10h", name="Steady 27 °C, 10 h (capped)", family="steady",
        route="total_acid_warm",
        points=_steady(27.0, 10.0),
        summary="Original chamber B setpoint, capped at the 10 h hot-hold limit: fast acidification, "
                "high rise rate; end at readiness, not by the clock.",
        mechanism="Warm steady fermentation ended before overproofing.",
    ),
    CandidateSchedule(
        id="steady_29_8h", name="Warm-short 29 °C, 8 h", family="warm_short",
        route="total_acid_warm",
        points=_steady(29.0, 8.0),
        summary="Very fast lactic acidification and rise; short window; highest sensitivity to "
                "timing errors.",
        mechanism="LAB favored over yeast at warmer temperatures -> more lactic acid per unit rise.",
    ),
    CandidateSchedule(
        id="warm_then_cool_27_19", name="Warm 27 °C 4 h -> cool 19 °C", family="warm_then_cool",
        route="overproof_protection",
        points=_steps((27.0, 4.0), (19.0, 8.0)),
        summary="Acid head start at 27 °C, then a step-down slows rise before the peak; acid keeps "
                "building slowly with some acetic contribution.",
        mechanism="Front-load acidification while warm, then step down before peak rise so structure "
                  "is protected.",
    ),
    CandidateSchedule(
        id="warm_then_cool_29_21", name="Warm 29 °C 3 h -> 21 °C", family="warm_then_cool",
        route="total_acid_warm",
        points=_steps((29.0, 3.0), (21.0, 8.0)),
        summary="Strong early lactic acidification at 29 °C, then moderate finish at 22 °C; "
                "moderate overproof risk late in the window.",
        mechanism="Warm lactic burst followed by a moderate finish.",
    ),
    CandidateSchedule(
        id="warm_then_cool_30_17", name="Warm 30 °C 3 h -> cool 17 °C", family="warm_then_cool",
        route="overproof_protection",
        points=_steps((30.0, 3.0), (17.0, 9.5), ramp_h=1.0),
        summary="Short hot burst for lactic acid, then a long cool phase that slows rise and favors "
                "acetic acid; combines both sourness routes.",
        mechanism="Hot burst then cool hold: lactic + acetic with limited rise progress.",
        notes="Requires ~4 °C of active cooling below a typical room.",
    ),
    CandidateSchedule(
        id="warm_then_cool_32_18", name="Hot 32 °C 5 h -> cool 18 °C", family="warm_then_cool",
        route="total_acid_warm",
        points=_steps((32.0, 5.0), (18.0, 10.0), ramp_h=1.0),
        summary="Hot lactic burst near the LAB optimum, then a long cool phase that nearly stalls rise; "
                "more acid than steady 22 °C with moderate overproof risk.",
        mechanism="LAB outpace yeast near 30-32 °C, so a short hot phase adds acid per unit of rise; the cool "
                  "tail protects structure.",
        notes="Upper end of the validated range; chamber must be able to cool ~14 °C within 1 h.",
    ),
    CandidateSchedule(
        id="warm_long_28_22", name="Warm 28 °C 9 h -> 22 °C (max acid in bounds)", family="step_down",
        route="total_acid_warm",
        points=_steps((28.0, 9.0), (22.0, 6.0)),
        summary="Longest warm hold the safety rules allow, then 22 °C: the most total acid in the window, but "
                "very likely past peak rise (dense crumb) unless ended early.",
        mechanism="Total acid scales with warm time; tests how much sourness is available within the 10 h hot-hold limit.",
        notes="Expect overproofing; agents must end at the plateau.",
    ),
    CandidateSchedule(
        id="cool_then_warm_20_26", name="Cool 20 °C 8 h -> warm 26 °C", family="cool_then_warm",
        route="acetic_cool",
        points=_steps((20.0, 8.0), (26.0, 4.5)),
        summary="Long cool lag (acetic-leaning), then a warm finish to reach full rise; finish "
                "timing matters.",
        mechanism="Cool phase builds acetic character; warm finish delivers rise without a long hot hold.",
    ),
    CandidateSchedule(
        id="cool_then_warm_18_27", name="Cool 18 °C 8 h -> warm 27 °C", family="cool_then_warm",
        route="acetic_cool",
        points=_steps((18.0, 8.0), (27.0, 4.5)),
        summary="Very slow cool start, then a warm push; strongest acetic lean among warm-finish "
                "options; risk of underproofing if the finish is short.",
        mechanism="Cool acetic phase plus warm finish.",
    ),
    CandidateSchedule(
        id="step_down_27_24_20", name="Step-down 27 -> 24 -> 20 °C", family="step_down",
        route="overproof_protection",
        points=_steps((27.0, 3.0), (24.0, 3.0), (20.0, 5.0)),
        summary="Graded step-down: warm start for acid, progressively slower rise to avoid "
                "overproofing a small batch.",
        mechanism="Graded cooling keeps acid production going while flattening the rise curve near peak.",
    ),
    CandidateSchedule(
        id="step_down_29_24_19", name="Step-down 29 -> 24 -> 19 °C", family="step_down",
        route="total_acid_warm",
        points=_steps((29.0, 2.5), (24.0, 3.0), (19.0, 5.5)),
        summary="Hotter graded step-down: more early lactic acid than 27-start, still protected "
                "from overproofing by the cool tail.",
        mechanism="Hot start for lactic acid, graded step-down to protect structure.",
    ),
    CandidateSchedule(
        id="cool_retard_24_16", name="24 °C 6 h -> cool retard 16 °C", family="cool_retard",
        route="acetic_cool",
        points=_steps((24.0, 6.0), (16.0, 9.5)),
        summary="Normal bulk start, then a long cool retard: rise nearly stalls while acid "
                "(assumed acetic-leaning) keeps accumulating slowly.",
        mechanism="Cool retard decouples acid development from rise.",
        notes="Requires ~6 °C of active cooling below a typical room; verify Peltier capacity.",
    ),
)

LIBRARY_BY_ID: dict[str, CandidateSchedule] = {c.id: c for c in LIBRARY}


def get_candidate(schedule_id: str) -> CandidateSchedule:
    try:
        return LIBRARY_BY_ID[schedule_id]
    except KeyError as exc:
        raise KeyError(f"unknown schedule id {schedule_id!r}") from exc


# ---------------------------------------------------------------------------
# Interpolation, validation
# ---------------------------------------------------------------------------
def normalize_points(points: Iterable[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    out = []
    for p in points:
        if p is None or len(p) < 2:
            raise ValueError(f"bad schedule point {p!r}")
        out.append((float(p[0]), float(p[1])))
    if not out:
        raise ValueError("empty schedule")
    return tuple(out)


def temp_at(points: Sequence[tuple[float, float]], t: float) -> float:
    if t <= points[0][0]:
        return points[0][1]
    for (t0, c0), (t1, c1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return c1
            return c0 + (c1 - c0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def _sample(points: Sequence[tuple[float, float]], dt: float = 0.05):
    end = points[-1][0]
    n = max(1, int(round(end / dt)))
    step = end / n
    for i in range(n):
        t_mid = (i + 0.5) * step
        yield t_mid, temp_at(points, t_mid), step


def max_contiguous_hold_at_or_above(points, threshold_c: float, dt: float = 0.05) -> float:
    best = cur = 0.0
    for _t, temp, step in _sample(points, dt):
        if temp >= threshold_c - 1e-9:
            cur += step
            best = max(best, cur)
        else:
            cur = 0.0
    return best


def validate_schedule(points: Iterable[Sequence[float]]) -> list[str]:
    """Return a list of violations (empty list == valid)."""
    try:
        pts = normalize_points(points)
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    if len(pts) < 2:
        errors.append("schedule needs at least 2 knots (start and end)")
    if pts[0][0] != 0.0:
        errors.append("first knot must be at t=0 h")
    for (t0, _), (t1, _) in zip(pts, pts[1:]):
        if t1 < t0:
            errors.append("knot times must be non-decreasing")
            break
    for t, c in pts:
        if not (math.isfinite(t) and math.isfinite(c)):
            errors.append("non-finite value in schedule")
            break
        if c < MIN_TEMP_C or c > MAX_TEMP_C:
            errors.append(f"temperature {c} °C at t={t} h outside [{MIN_TEMP_C}, {MAX_TEMP_C}] °C")
    if errors:
        return errors
    duration = pts[-1][0]
    if duration <= 0 or duration > MAX_DURATION_H:
        errors.append(f"duration {duration} h outside (0, {MAX_DURATION_H}] h")
    for (t0, c0), (t1, c1) in zip(pts, pts[1:]):
        if t1 > t0 and abs(c1 - c0) / (t1 - t0) > MAX_RAMP_C_PER_H + 1e-9:
            errors.append(f"ramp between t={t0} h and t={t1} h exceeds {MAX_RAMP_C_PER_H} °C/h")
        if t1 == t0 and c1 != c0:
            errors.append(f"instantaneous step at t={t0} h (use a ramp)")
    hot = max_contiguous_hold_at_or_above(pts, HOT_THRESHOLD_C)
    if hot > MAX_HOT_HOLD_H + 0.05:
        errors.append(
            f"holds >= {HOT_THRESHOLD_C} °C for {hot:.1f} h (> {MAX_HOT_HOLD_H} h) without a step-down"
        )
    return errors


def is_valid_schedule(points) -> bool:
    return not validate_schedule(points)


def steady_schedule(temp_c: float, hours: float = 14.0) -> tuple[tuple[float, float], ...]:
    return _steady(float(temp_c), float(hours))


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------
def heuristic_prediction(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """Degree-hour heuristic (provenance: estimated). Pure and deterministic."""
    pts = normalize_points(points)
    acid = rise = acetic_w = temp_w = 0.0
    t_peak = None
    max_t = -1e9
    for t, temp, step in _sample(pts):
        acid += step * Q10_ACID ** ((temp - REF_TEMP_C) / 10.0) / ACID_REF_H
        rise += step * Q10_RISE ** ((temp - REF_TEMP_C) / 10.0) / RISE_PEAK_REF_H
        acetic_w += step * min(1.0, max(0.0, (ACETIC_PIVOT_C - temp) / 8.0))
        temp_w += step * temp
        max_t = max(max_t, temp)
        if t_peak is None and rise >= 1.0:
            t_peak = t
    duration = pts[-1][0]
    acetic_index = acetic_w / duration
    sourness = 0.05 + 0.8 * (1.0 - math.exp(-0.8 * acid)) + ACETIC_BONUS * acetic_index * min(acid, 1.2) / 1.2
    openness = 0.15 + 0.75 * math.exp(-(((rise - OPTIMAL_RISE_PROGRESS) / 0.35) ** 2))
    overproof = 1.0 / (1.0 + math.exp(-(rise - 1.4) / 0.12))
    underproof = 1.0 / (1.0 + math.exp((rise - 0.75) / 0.08))
    return {
        "model": HEURISTIC_MODEL_ID,
        "provenance": "estimated",
        "duration_h": round(duration, 3),
        "mean_temp_c": round(temp_w / duration, 2),
        "max_temp_c": round(max_t, 2),
        "acid_progress": round(acid, 3),
        "rise_progress": round(rise, 3),
        "time_to_peak_rise_h": None if t_peak is None else round(t_peak, 2),
        "acetic_index": round(acetic_index, 3),
        "sourness_index": round(min(1.0, max(0.0, sourness)), 3),
        "openness_index": round(min(1.0, max(0.0, openness)), 3),
        "overproof_risk": round(overproof, 3),
        "underproof_risk": round(underproof, 3),
        "final_ph": None,
    }


def _load_simulator():
    try:  # another agent owns this module; tolerate absence or API drift
        from fermentai.fermentation.simulation import TemperatureSchedule, simulate  # type: ignore
    except Exception:  # noqa: BLE001 - ImportError or anything raised at import time
        return None
    return simulate, TemperatureSchedule


def simulator_available() -> bool:
    return _load_simulator() is not None


def _col(df, name):
    return [float(x) for x in df[name]] if name in getattr(df, "columns", []) else None


def _simulator_prediction(points: tuple[tuple[float, float], ...]) -> dict[str, Any]:
    """Map simulator output onto the same relative indices as the heuristic.

    Uses (when present) TTA and the fermentation quotient (lactic:acetic molar ratio; lower =
    more acetic) for sourness, and peak expansion x gluten integrity minus the simulator's own
    overproof risk for crumb openness. Falls back to pH/expansion-only proxies for older
    simulator versions.
    """
    sim = _load_simulator()
    if sim is None:
        raise RuntimeError("simulator not importable")
    simulate, TemperatureSchedule = sim
    duration = points[-1][0]
    try:
        sched = TemperatureSchedule(list(points), mode="piecewise_linear")
    except TypeError:
        sched = TemperatureSchedule(points=list(points))
    df = simulate(sched, t_end_h=duration)
    ph, exp_, times = _col(df, "ph"), _col(df, "expansion_ratio"), _col(df, "time_h")
    if not ph or not exp_ or not times or any(math.isnan(v) for v in (ph[-1], exp_[-1])):
        raise ValueError("simulator returned empty/NaN output")
    peak = max(exp_)
    i_peak = exp_.index(peak)
    t_peak = times[i_peak]
    tta, fq = _col(df, "tta_ml_per_10g"), _col(df, "fermentation_quotient")
    lactic, acetic = _col(df, "lactic_acid"), _col(df, "acetic_acid")
    if tta:
        acid_index = min(1.0, max(0.0, (tta[-1] - 4.0) / 8.0))
    else:
        acid_index = min(1.0, max(0.0, (ph[0] - ph[-1]) / 1.6))
    if fq and math.isfinite(fq[-1]):
        acetic_index = min(1.0, max(0.0, (4.5 - fq[-1]) / 2.0))
    elif lactic and acetic and (lactic[-1] + acetic[-1]) > 0:
        acetic_index = min(1.0, acetic[-1] / (lactic[-1] + acetic[-1]) / 0.4)
    else:
        acetic_index = 0.0
    sourness = 0.1 + 0.7 * acid_index + 0.2 * acetic_index
    op_col = _col(df, "overproof_risk")
    decline = max(0.0, 1.0 - exp_[-1] / peak) if peak > 0 else 0.0
    overproof = op_col[-1] if op_col else min(1.0, decline / 0.08)
    gluten = _col(df, "gluten_integrity")
    g = gluten[-1] if gluten else 1.0
    rise_score = min(1.0, max(0.0, (peak - 1.0) / 1.0))
    openness = 0.15 + 0.75 * rise_score * g - 0.3 * overproof
    rise_progress = duration / t_peak if (t_peak > 0 and t_peak < duration - 0.05) else 1.0
    h = heuristic_prediction(points)  # schema-complete defaults (mean/max temp, duration)
    h.update(
        {
            "model": "fermentai.fermentation.simulation",
            "provenance": "simulated",
            "acid_progress": round(acid_index, 3),
            "rise_progress": round(rise_progress, 3),
            "sourness_index": round(min(1.0, max(0.0, sourness)), 3),
            "openness_index": round(min(1.0, max(0.0, openness)), 3),
            "overproof_risk": round(min(1.0, max(0.0, overproof)), 3),
            "underproof_risk": round(1.0 / (1.0 + math.exp((peak - 1.5) / 0.08)), 3),
            "acetic_index": round(acetic_index, 3),
            "final_ph": round(ph[-1], 3),
            "peak_expansion_ratio": round(peak, 3),
            "time_to_peak_rise_h": round(t_peak, 2),
            "final_expansion_ratio": round(exp_[-1], 3),
            "final_tta_ml_per_10g": round(tta[-1], 2) if tta else None,
            "final_fermentation_quotient": round(fq[-1], 2) if fq else None,
            "final_gluten_integrity": round(g, 3) if gluten else None,
        }
    )
    return h


@lru_cache(maxsize=512)
def _predict_cached(points: tuple[tuple[float, float], ...], use_simulator: bool) -> tuple:
    if use_simulator and simulator_available():
        try:
            return tuple(sorted(_simulator_prediction(points).items()))
        except Exception as exc:  # noqa: BLE001
            h = heuristic_prediction(points)
            h["simulator_error"] = f"{type(exc).__name__}: {exc}"[:200]
            return tuple(sorted(h.items()))
    return tuple(sorted(heuristic_prediction(points).items()))


def predict_schedule(points, use_simulator: bool = True) -> dict[str, Any]:
    """Predict relative fermentation outcome indices for any schedule (cached)."""
    return dict(_predict_cached(normalize_points(points), bool(use_simulator)))


def library_table(use_simulator: bool = True) -> list[dict[str, Any]]:
    """Library with predictions, e.g. for the dashboard or a benchmark."""
    rows = []
    for c in LIBRARY:
        d = c.to_dict()
        d["prediction"] = predict_schedule(c.points, use_simulator)
        rows.append(d)
    return rows


@dataclass
class _LibraryCheck:
    errors: dict[str, list[str]] = field(default_factory=dict)


def _check_library() -> _LibraryCheck:
    chk = _LibraryCheck()
    for c in LIBRARY:
        errs = validate_schedule(c.points)
        if errs:
            chk.errors[c.id] = errs
    return chk


_LIB_CHECK = _check_library()
if _LIB_CHECK.errors:  # pragma: no cover - guarded by tests
    raise RuntimeError(f"invalid schedule library: {_LIB_CHECK.errors}")
