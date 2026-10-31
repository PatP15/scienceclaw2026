"""Top-3 distinct next-experiment suggestions for the guest's station (Head Chef's menu).

    build_suggestions(evidence, ranked, interp, station, chosen_schedule_id, risk_weight) -> list[dict]

Pure + deterministic. Candidates come from the validated library via the line-cook ranking;
rank 1 is always the Head Chef's pick for that station (consistent with ``next_experiment``);
the others are picked for diversity of route (warmer / longer-cooler / two-stage). Anything that
fails ``validate_schedule`` is dropped. Predictions are the simulator's ("simulated") or the
degree-hour heuristic's ("estimated"); nothing here touches hardware.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import schedules as S

N_SUGGESTIONS = 3
HORIZON_H = 16.0
OVERPROOF_DROP = 0.03  # same rule as the scientist: >3 % fall below peak = overproofed

_TWO_STAGE = {"warm_then_cool", "cool_then_warm", "step_down"}


def category(c: dict[str, Any]) -> str:
    fam = c["family"]
    mean = sum(t for _, t in c["points"]) / len(c["points"])
    if fam in _TWO_STAGE:
        return "two_stage"
    if fam == "cool_retard" or (fam == "steady" and mean <= 20.5):
        return "cooler"
    if fam == "warm_short" or (fam == "steady" and mean >= 24.0):
        return "warmer"
    return "baseline"


def _t(x: float) -> str:
    return f"{x:g}"


def title_for(c: dict[str, Any]) -> str:
    fam, pts = c["family"], c["points"]
    temps = [t for _, t in pts]
    if fam == "warm_then_cool":
        return "Warm start, cool finish"
    if fam == "cool_then_warm":
        return "Cool start, warm finish"
    if fam == "step_down":
        return "Warm start, gradual step-down"
    if fam == "cool_retard":
        return "Room start, cold retard"
    if fam == "warm_short":
        return f"Short, hot {_t(temps[0])} °C ferment"
    mean = temps[0]
    if mean <= 20.5:
        return f"Long, cool {_t(mean)} °C ferment"
    if mean >= 24:
        return f"Steady warm {_t(mean)} °C ferment"
    return f"Steady {_t(mean)} °C ferment"


@lru_cache(maxsize=256)
def _profile(points: tuple[tuple[float, float], ...]) -> tuple[tuple[str, Any], ...]:
    """(ph at 16 h, peak rise, hours until >3 % fall below peak) from the simulator, else Nones."""
    out: dict[str, Any] = {"ph_16h": None, "peak_rise": None, "overproof_h": None, "provenance": "estimated"}
    sim = S._load_simulator() if S.simulator_available() else None
    if sim is not None:
        try:
            simulate, TS = sim
            df = simulate(TS(list(points), mode="piecewise_linear"), t_end_h=HORIZON_H)
            t, e, ph = S._col(df, "time_h"), S._col(df, "expansion_ratio"), S._col(df, "ph")
            pk = max(e)
            ip = e.index(pk)
            ov = next((t[i] for i in range(ip, len(e)) if e[i] < pk * (1 - OVERPROOF_DROP)), None)
            out.update(ph_16h=round(ph[-1], 2), peak_rise=round(pk, 2),
                       overproof_h=None if ov is None else round(ov, 1), provenance="simulated")
        except Exception:  # noqa: BLE001 - fall back to the heuristic's numbers
            pass
    return tuple(out.items())


def _risk(model: dict[str, Any]) -> str:
    over, under = model.get("overproof_risk") or 0.0, model.get("underproof_risk") or 0.0
    if under > over and under >= 0.2:
        return f"underproof risk {round(under * 100)} %"
    return f"overproof risk {round(over * 100)} %"


_ROUTE_TARGET = {
    "total_acid_warm": ("more sour", "faster, lactic-led"),
    "acetic_cool": ("more tang", "slower, acetic-leaning"),
    "overproof_protection": ("more sour", "protect the crumb"),
    "baseline": ("reference point", "known baseline"),
    "dose_response": ("fill a temperature gap", "tests the in-between"),
}


def _target(c: dict[str, Any], interp: dict[str, Any], risk_over: float) -> str:
    desired = (interp or {}).get("desired") or {}
    lead, tail = _ROUTE_TARGET.get(c["route"], ("explore", ""))
    sour = desired.get("sourness")
    if sour and sour.get("direction") == "decrease" and c["route"] in ("total_acid_warm", "acetic_cool", "overproof_protection"):
        lead = "milder"
    parts = [lead]
    tex, den = desired.get("texture"), desired.get("density")
    wants_light = bool((tex and tex["direction"] == "increase") or (den and den["direction"] == "decrease"))
    if wants_light or risk_over <= 0.5:
        parts.append("keep airy" if risk_over <= 0.5 else "lighter crumb, watch the proof")
    elif tail:
        parts.append(tail)
    return ", ".join(parts)


def _why(c: dict[str, Any], prof: dict[str, Any], is_pick: bool) -> str:
    base = S.ROUTE_TEXT.get(c["route"], c["route"]).split(" (")[0]
    base = base[0].upper() + base[1:]
    if prof["ph_16h"] is not None:
        nums = f"; the simulator predicts pH {prof['ph_16h']:.2f} at 16 h and a peak rise of {prof['peak_rise']:.2f}x"
    else:
        nums = "; estimated from a degree-hour heuristic (no simulator numbers)"
    return f"{base}{nums}."


def build_suggestions(evidence: dict[str, Any], ranked: list[dict[str, Any]], interp: dict[str, Any],
                      station: str, pick_schedule_id: str | None) -> list[dict[str, Any]]:
    ok = [r for r in ranked if not S.validate_schedule(r["points"])]
    ordered: list[dict[str, Any]] = []
    if pick_schedule_id:
        pick = next((r for r in ok if r["id"] == pick_schedule_id), None)
        if pick is None and pick_schedule_id in S.LIBRARY_BY_ID:
            d = S.LIBRARY_BY_ID[pick_schedule_id].to_dict()
            pick = next((r for r in ranked if r["id"] == pick_schedule_id), d)
        if pick is not None:
            ordered.append(pick)
    rest = [r for r in ok if r["id"] not in {o["id"] for o in ordered}
            and (r.get("model") or {}).get("overproof_risk", 0.0) < 0.9]
    chosen = list(ordered)
    cats = {category(c) for c in chosen}
    for r in rest:  # best of each unused route category first
        if len(chosen) >= N_SUGGESTIONS:
            break
        if category(r) not in cats and category(r) != "baseline":
            chosen.append(r)
            cats.add(category(r))
    for r in rest:  # then fill with the best remaining
        if len(chosen) >= N_SUGGESTIONS:
            break
        if r not in chosen:
            chosen.append(r)
    out, titles = [], set()
    for i, c in enumerate(chosen[:N_SUGGESTIONS], 1):
        model = c.get("model") or S.predict_schedule(c["points"])
        prof = dict(_profile(tuple((float(a), float(b)) for a, b in c["points"])))
        title = title_for(c)
        if title in titles:
            title = f"{title} ({_t(c['points'][0][1])} °C)"
        titles.add(title)
        prov = prof.pop("provenance")
        out.append({
            "rank": i, "id": f"sugg_{station}_{i}_{c['id']}", "title": title, "schedule_id": c["id"],
            "points": [[float(a), float(b)] for a, b in c["points"]],
            "target": _target(c, interp, float(model.get("overproof_risk") or 0.0)),
            "predicted": prof,
            "why": _why(c, prof, i == 1),
            "risk": _risk(model),
            "head_chef_pick": i == 1,
            "station": station,
            "provenance": prov,
        })
    return out
