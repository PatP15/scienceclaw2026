"""Simulation-derived crumb specimens: kinetics morphology summary -> CrumbParams -> STL.

Chain (each step carries its own provenance label)::

    ALCHEMI/AIMNet2 acid-water cluster descriptors        real (remote GPU), data/alchemi/analysis
      -> kinetics.apply_molecular_descriptors             alchemi-informed parameter nudge
      -> simulation.simulate + summarize_for_morphology   simulated (CPU ODE), t_end = 16 h
      -> map_to_crumb_params  (THIS MODULE)               assumption (documented, bounded, monotone)
      -> crumb.generate_crumb -> STL                      simulated (seeded stochastic geometry)

The mapping is a *hypothesis*: sourdough kinetics (and sourness in particular) do not uniquely
determine crumb structure. Directions follow general bread-crumb knowledge (more retained gas
and rise -> more open crumb; fast rise, weakened gluten and over-proofing -> larger, more
irregular, coalesced cells); every magnitude is an assumption in ``MAPPING_CONFIG`` and is meant
to be replaced by fits to real loaf photographs (image_analysis.analyze_crumb_image +
compare_to_params). See docs/morphology_coupling.md.

Public API::

    MorphologyInputs.from_json(path, window="at_window_end")
    MorphologyInputs.from_summary(summary_dict, chamber_id="A")
    kinetics_inputs(chamber_id, morph_json_path)        -> {"literature", "alchemi_informed", ...}
    map_to_crumb_params(inputs, config=None, seed=...)  -> (CrumbParams, MappingReport)
    generate_chamber_specimen(chamber_id, morph_json_path, out_dir) -> dict of paths
    generate_chain(...)                                  -> both chambers + render + chain_summary
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .crumb import CrumbParams

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PREDICTIONS_DIR = ROOT / "data" / "predictions"
DEFAULT_OUT_DIR = ROOT / "data" / "stl"
DEFAULT_SEED = 20261031  # same seed for A and B: common random numbers, so geometric
#                          differences come from the mapped parameters, not from the dice.
PRINTABLE_MIN_WALL_MM = 0.9  # Formlabs resin: never go thinner than this
PROVENANCE = "simulated"
CHAMBER_TEMPS_C = {"A": 22.0, "B": 27.0}
# Same categorical slots as fermentation/plotting.py CHAMBER_COLORS (A blue, B orange).
CHAMBER_COLORS = {"A": "#2a78d6", "B": "#eb6834"}

DISCLAIMER = (
    "Simulation-derived crumb HYPOTHESIS: kinetics (and sourness) do not uniquely determine crumb "
    "structure. Mapping coefficients are assumptions; ALCHEMI descriptors only nudge one kinetic "
    "parameter upstream. Test against real loaf photographs."
)

INPUT_FIELDS: tuple[str, ...] = (
    "co2_retained_g_per_kg",
    "co2_retained_peak_g_per_kg",
    "expansion_ratio_peak",
    "expansion_ratio_final",
    "max_expansion_rate_per_h",
    "overproof_flag",
    "expansion_fall_from_peak_fraction",
    "final_ph",
    "gluten_weakening_index",
    "final_fermentation_quotient",
)

# Neutral, mid-range values used when a field is missing / non-numeric (source: assumption).
# They sit near the middle of each driver's reference range so a missing field neither pushes
# the crumb towards "open" nor towards "tight".
FALLBACK_INPUTS: dict[str, Any] = {
    "co2_retained_g_per_kg": 1.5,
    "co2_retained_peak_g_per_kg": 1.5,
    "expansion_ratio_peak": 1.8,
    "expansion_ratio_final": 1.8,
    "max_expansion_rate_per_h": 0.2,
    "overproof_flag": False,
    "expansion_fall_from_peak_fraction": 0.0,
    "final_ph": 4.25,
    "gluten_weakening_index": 0.5,
    "final_fermentation_quotient": 3.5,
}

_A = "assumption"
_LIT_CELLS = (
    "direction only: Scanlon & Zghal (2001) Food Res Int 34:841-864 (bread crumb structure "
    "review); Gan, Ellis & Schofield (1995) J Cereal Sci 21:215-230 (gas-cell stabilisation, "
    "coalescence, disproportionation). Magnitude: assumption"
)
_LIT_GLUTEN = (
    "direction only: Gaenzle, Loponen & Gobbetti (2008) Trends Food Sci Technol 19:513-521 "
    "(acid-activated proteolysis weakens gluten). Magnitude: assumption"
)

# Every coefficient: value, unit, source. Override any value via map_to_crumb_params(config=...)
# with {key: number}.
MAPPING_CONFIG: dict[str, dict[str, Any]] = {
    # --- normalisation of kinetics outputs to 0..1 drivers (linear, clipped) ---------------
    "gas_fraction_lo": {"value": 0.25, "unit": "-", "source": _A,
                        "note": "gas volume fraction 1-1/E that maps to driver 0"},
    "gas_fraction_hi": {"value": 0.60, "unit": "-", "source": _A,
                        "note": "gas volume fraction that maps to driver 1"},
    "co2_retained_lo": {"value": 0.5, "unit": "g CO2/kg dough", "source": _A},
    "co2_retained_hi": {"value": 2.5, "unit": "g CO2/kg dough", "source": _A,
                        "note": "~ upper end of the model's gas-holding capacity"},
    "w_gas_expansion": {"value": 0.6, "unit": "-", "source": _A,
                        "note": "weight of rise vs retained CO2 in the gas driver"},
    "rate_lo": {"value": 0.10, "unit": "(V/V0)/h", "source": _A},
    "rate_hi": {"value": 0.35, "unit": "(V/V0)/h", "source": _A},
    "fall_hi": {"value": 0.40, "unit": "fraction of peak rise", "source": _A},
    "overproof_step": {"value": 0.35, "unit": "-", "source": _A,
                       "note": "collapse driver contribution of the overproof flag"},
    "ph_hi": {"value": 5.0, "unit": "pH", "source": "configs/kinetics.yaml GWI pH onset (assumption)",
              "note": "pH at which acid weakening of walls starts (driver 0)"},
    "ph_lo": {"value": 3.5, "unit": "pH", "source": _A, "note": "driver 1"},
    # --- porosity --------------------------------------------------------------------------
    "porosity_min": {"value": 0.20, "unit": "cut-face area fraction", "source": _A,
                     "note": "bound; typical image-analysis crumb pore fractions ~0.2-0.5"},
    "porosity_max": {"value": 0.50, "unit": "cut-face area fraction", "source": _A},
    # --- pore size --------------------------------------------------------------------------
    "w_size_rate": {"value": 0.40, "unit": "-", "source": _LIT_CELLS},
    "w_size_gwi": {"value": 0.35, "unit": "-", "source": _LIT_GLUTEN},
    "w_size_collapse": {"value": 0.25, "unit": "-", "source": _LIT_CELLS},
    "pore_median_min": {"value": 0.9, "unit": "mm", "source": _A,
                        "note": "bound; equivalent-sphere diameter median"},
    "pore_median_max": {"value": 3.2, "unit": "mm", "source": _A},
    "cv_min": {"value": 0.30, "unit": "-", "source": _A, "note": "sd/mean of pore diameter"},
    "cv_span": {"value": 0.50, "unit": "-", "source": _A},
    "pore_max_base": {"value": 4.0, "unit": "mm", "source": _A},
    "pore_max_span": {"value": 8.0, "unit": "mm", "source": _A,
                      "note": "long tail of merged cells under collapse"},
    "pore_max_cap": {"value": 12.0, "unit": "mm", "source": "printable geometry (12 mm slab)"},
    # --- shape / irregularity ---------------------------------------------------------------
    "ecc_min": {"value": 0.10, "unit": "-", "source": _A},
    "ecc_span": {"value": 0.70, "unit": "-", "source": _LIT_CELLS},
    "jitter_min": {"value": 0.25, "unit": "-", "source": _A},
    "jitter_span": {"value": 0.35, "unit": "-", "source": _A},
    # --- clustering / coalescence -----------------------------------------------------------
    "clustering_min": {"value": 0.05, "unit": "-", "source": _A},
    "clustering_span": {"value": 0.70, "unit": "-", "source": _LIT_CELLS},
    "cluster_radius_min": {"value": 3.0, "unit": "mm", "source": _A},
    "cluster_radius_span": {"value": 2.0, "unit": "mm", "source": _A},
    "cluster_spacing_min": {"value": 14.0, "unit": "mm", "source": _A},
    "cluster_spacing_span": {"value": 4.0, "unit": "mm", "source": _A},
    # --- walls ------------------------------------------------------------------------------
    "wall_floor": {"value": PRINTABLE_MIN_WALL_MM, "unit": "mm",
                   "source": "Formlabs printability floor (hard lower bound)"},
    "wall_span": {"value": 0.6, "unit": "mm", "source": _LIT_GLUTEN},
}


def _cfg(config: Mapping[str, Any] | None) -> dict[str, float]:
    vals = {k: float(v["value"]) for k, v in MAPPING_CONFIG.items()}
    for k, v in (config or {}).items():
        if k not in vals:
            raise KeyError(f"unknown mapping coefficient {k!r}")
        vals[k] = float(v["value"] if isinstance(v, Mapping) else v)
    vals["wall_floor"] = max(vals["wall_floor"], PRINTABLE_MIN_WALL_MM)  # never below printable
    return vals


def _unit(x: float, lo: float, hi: float) -> float:
    """Linear map lo->0, hi->1, clipped to [0, 1] (monotone non-decreasing, bounded)."""
    if hi == lo:
        return 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


# ----------------------------------------------------------------------------- inputs


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class MorphologyInputs:
    """Kinetics scalars that drive the crumb mapping (one chamber, one end time)."""

    chamber_id: str
    co2_retained_g_per_kg: float
    co2_retained_peak_g_per_kg: float
    expansion_ratio_peak: float
    expansion_ratio_final: float
    max_expansion_rate_per_h: float
    overproof_flag: bool
    expansion_fall_from_peak_fraction: float
    final_ph: float
    gluten_weakening_index: float
    final_fermentation_quotient: float
    t_end_h: float = 16.0
    kinetics_variant: str = "literature"  # "literature" | "alchemi_informed" | "external"
    source: str = ""
    provenance: str = PROVENANCE
    fallback_fields: tuple[str, ...] = ()
    upstream: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    def values(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in INPUT_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["fallback_fields"] = list(self.fallback_fields)
        return d

    @classmethod
    def from_summary(
        cls,
        summary: Mapping[str, Any] | None,
        chamber_id: str = "?",
        **meta: Any,
    ) -> MorphologyInputs:
        """Build from a ``summarize_for_morphology`` dict. Missing / None / NaN / non-numeric
        fields fall back (derived from related fields when possible, else FALLBACK_INPUTS) and
        are listed in ``fallback_fields``. Values are clamped to physical ranges."""
        s = dict(summary or {})
        got: dict[str, float | None] = {k: _num(s.get(k)) for k in INPUT_FIELDS}
        fb: list[str] = [k for k, v in got.items() if v is None]

        # derive from related fields before using neutral fallbacks
        pk, fin, fall = got["expansion_ratio_peak"], got["expansion_ratio_final"], got[
            "expansion_fall_from_peak_fraction"]
        if fin is None and pk is not None and fall is not None:
            got["expansion_ratio_final"] = pk - fall * (pk - 1.0)
        if pk is None and got["expansion_ratio_final"] is not None:
            got["expansion_ratio_peak"] = got["expansion_ratio_final"]
        if fall is None and pk is not None and fin is not None:
            got["expansion_fall_from_peak_fraction"] = (pk - fin) / max(pk - 1.0, 1e-9)
        if got["co2_retained_peak_g_per_kg"] is None and got["co2_retained_g_per_kg"] is not None:
            got["co2_retained_peak_g_per_kg"] = got["co2_retained_g_per_kg"]
        if got["overproof_flag"] is None and got["expansion_fall_from_peak_fraction"] is not None:
            # same definition as the kinetics model: >= 5 % of the rise lost
            got["overproof_flag"] = float(got["expansion_fall_from_peak_fraction"] >= 0.05)
        for k in INPUT_FIELDS:
            if got[k] is None:
                got[k] = _num(FALLBACK_INPUTS[k])

        v = {k: float(got[k]) for k in INPUT_FIELDS}  # type: ignore[arg-type]
        v["expansion_ratio_final"] = max(1.0, v["expansion_ratio_final"])
        v["expansion_ratio_peak"] = max(v["expansion_ratio_peak"], v["expansion_ratio_final"])
        v["co2_retained_g_per_kg"] = max(0.0, v["co2_retained_g_per_kg"])
        v["co2_retained_peak_g_per_kg"] = max(v["co2_retained_peak_g_per_kg"],
                                              v["co2_retained_g_per_kg"])
        v["max_expansion_rate_per_h"] = max(0.0, v["max_expansion_rate_per_h"])
        v["expansion_fall_from_peak_fraction"] = min(1.0, max(0.0,
                                                    v["expansion_fall_from_peak_fraction"]))
        v["final_ph"] = min(8.0, max(2.5, v["final_ph"]))
        v["gluten_weakening_index"] = min(1.0, max(0.0, v["gluten_weakening_index"]))
        v["final_fermentation_quotient"] = max(0.0, v["final_fermentation_quotient"])
        t_end = _num(s.get("t_end_h"))
        return cls(
            chamber_id=str(chamber_id),
            **{k: (bool(v[k] >= 0.5) if k == "overproof_flag" else v[k]) for k in INPUT_FIELDS},
            t_end_h=t_end if t_end is not None else 16.0,
            kinetics_variant=meta.pop("kinetics_variant", "external"),
            source=meta.pop("source", ""),
            provenance=str(s.get("provenance", PROVENANCE)),
            fallback_fields=tuple(fb),
            upstream=meta.pop("upstream", {}),
        )

    @classmethod
    def from_json(cls, path: str | Path, window: str = "at_window_end") -> MorphologyInputs:
        """Read ``data/predictions/chamber_<id>_morphology.json`` (default: 16 h window end).

        These files are written by scripts/simulate_loaves.py from the *literature-parameter*
        nominal run (the ALCHEMI-informed run is only stored as a CSV), so this constructor
        labels the variant "literature"."""
        path = Path(path)
        d = json.loads(path.read_text())
        summ = d.get(window) if isinstance(d.get(window), Mapping) else None
        if summ is None and any(k in d for k in INPUT_FIELDS):
            summ = d  # a bare summary dict
        cid = d.get("chamber_id") or path.stem.replace("chamber_", "").replace("_morphology", "")
        return cls.from_summary(
            summ or {}, chamber_id=cid, kinetics_variant="literature",
            source=f"{_rel(path)}#{window}",
            upstream={"schedule": d.get("schedule"), "alchemi_applied": False,
                      "note": "morphology JSON is the literature-parameter nominal run"},
        )


def _rel(p: str | Path) -> str:
    p = Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def kinetics_inputs(
    chamber_id: str,
    morph_json_path: str | Path,
    window: str = "at_window_end",
    dt_h: float = 0.05,
) -> dict[str, Any]:
    """Re-run the kinetics model for one chamber in both variants and summarise at ``window``.

    Uses the schedule and horizon recorded in the morphology JSON, ``simulate`` and
    ``summarize_for_morphology`` from ``fermentai.fermentation`` (read-only use), and
    ``apply_molecular_descriptors`` for the ALCHEMI-informed variant. Returns
    ``{"literature": MorphologyInputs, "alchemi_informed": MorphologyInputs | None,
    "reproduces_json": {...}, "coupling": report-dict, "error": str | None}``. If the
    fermentation package cannot run, falls back to the JSON (literature) values.
    """
    path = Path(morph_json_path)
    json_inputs = MorphologyInputs.from_json(path, window)
    out: dict[str, Any] = {"literature": json_inputs, "alchemi_informed": None,
                           "reproduces_json": None, "coupling": None, "error": None}
    try:
        from fermentai.fermentation import (
            TemperatureSchedule,
            apply_molecular_descriptors,
            load_scenario,
            simulate,
            summarize_for_morphology,
        )

        d = json.loads(path.read_text())
        sched = (TemperatureSchedule.from_dict(d["schedule"]) if d.get("schedule")
                 else load_scenario(chamber_id).schedule)
        horizon = _num((d.get("at_horizon") or {}).get("t_end_h")) or 24.0
        t_end = json_inputs.t_end_h
        lit_s = summarize_for_morphology(simulate(sched, t_end_h=horizon, dt_h=dt_h), t_end)
        diffs = {k: abs(float(lit_s[k]) - float(getattr(json_inputs, k))) for k in INPUT_FIELDS}
        out["reproduces_json"] = {"max_abs_diff": max(diffs.values()),
                                  "ok": max(diffs.values()) < 1e-6}
        out["literature"] = MorphologyInputs.from_summary(
            lit_s, chamber_id, kinetics_variant="literature",
            source=f"fermentation.simulate(literature params) re-run; matches {_rel(path)}",
            upstream={"schedule": sched.to_dict(), "alchemi_applied": False})

        _params, rep = apply_molecular_descriptors(None, return_report=True)
        out["coupling"] = rep
        changed = {k: v["value"] for k, v in rep.get("parameters", {}).items()
                   if v.get("source") == "alchemi-informed"}
        if rep.get("status") in ("applied", "partial") and changed:
            alt_s = summarize_for_morphology(
                simulate(sched, params=changed, t_end_h=horizon, dt_h=dt_h), t_end)
            out["alchemi_informed"] = MorphologyInputs.from_summary(
                alt_s, chamber_id, kinetics_variant="alchemi_informed",
                source="fermentation.simulate(params + apply_molecular_descriptors)",
                upstream={"schedule": sched.to_dict(), "alchemi_applied": True,
                          "changed_parameters": {
                              k: {"value": v["value"], "literature_value": v.get("literature_value"),
                                  "inputs": v.get("inputs"), "mapping": v.get("mapping")}
                              for k, v in rep["parameters"].items()
                              if v.get("source") == "alchemi-informed"},
                          "descriptors": rep.get("descriptors"),
                          "descriptor_provenance": rep.get("descriptor_provenance"),
                          "status": rep.get("status"), "notes": rep.get("notes")})
    except Exception as e:  # noqa: BLE001 - fall back to the JSON values, but say so
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ----------------------------------------------------------------------------- mapping


@dataclass
class MappingReport:
    chamber_id: str
    provenance: str
    kinetics_variant: str
    inputs: dict[str, Any]
    input_source: str
    fallback_fields: list[str]
    drivers: dict[str, dict[str, Any]]
    outputs: dict[str, dict[str, Any]]
    unmapped: dict[str, str]
    config: dict[str, dict[str, Any]]
    seed: int
    notes: list[str] = field(default_factory=list)
    alchemi_attribution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(_jsonable(self.to_dict()), indent=2))


def _jsonable(o: Any) -> Any:
    if isinstance(o, Mapping):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, Path):
        return _rel(o)
    if hasattr(o, "tolist") and not isinstance(o, (str, bytes)):
        return _jsonable(o.tolist())
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _alchemi_fields(inputs: MorphologyInputs) -> list[str]:
    up = inputs.upstream or {}
    if not up.get("alchemi_applied"):
        return []
    fields: list[str] = []
    for ch in (up.get("changed_parameters") or {}).values():
        fields.extend(ch.get("inputs") or {})
    desc = up.get("descriptors") or {}
    for acid, dd in desc.items():
        for k in dd or {}:
            fields.append(f"{acid}.{k}")
    params = list((up.get("changed_parameters") or {}).keys())
    return sorted(set(fields)) + [f"-> kinetics parameter {p}" for p in params]


def map_to_crumb_params(
    inputs: MorphologyInputs,
    config: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    name: str | None = None,
) -> tuple[CrumbParams, MappingReport]:
    """Monotone, bounded mapping from kinetics scalars to crumb generator parameters.

    Drivers (all in [0, 1], linear + clipped):
      s_gas      = w*u(1-1/E_final; gas_lo, gas_hi) + (1-w)*u(CO2_ret; co2_lo, co2_hi)
      s_rate     = u(max_rate; rate_lo, rate_hi)
      s_gwi      = GWI
      s_collapse = overproof_step*[overproof] + (1-overproof_step)*u(fall; 0, fall_hi)
      s_acid     = u(ph_hi - pH; 0, ph_hi - ph_lo)
      s_size     = w_rate*s_rate + w_gwi*s_gwi + w_collapse*s_collapse   (weights normalised)
      s_irreg    = (s_rate + s_gwi)/2
      s_weak     = (s_acid + s_gwi)/2
    Outputs: see the ``formula`` of each entry in the returned report.
    """
    c = _cfg(config)
    x = inputs
    gas_fraction = 1.0 - 1.0 / max(x.expansion_ratio_final, 1.0)
    w = min(1.0, max(0.0, c["w_gas_expansion"]))
    s_gas = (w * _unit(gas_fraction, c["gas_fraction_lo"], c["gas_fraction_hi"])
             + (1 - w) * _unit(x.co2_retained_g_per_kg, c["co2_retained_lo"], c["co2_retained_hi"]))
    s_rate = _unit(x.max_expansion_rate_per_h, c["rate_lo"], c["rate_hi"])
    s_gwi = min(1.0, max(0.0, x.gluten_weakening_index))
    st = min(1.0, max(0.0, c["overproof_step"]))
    s_collapse = st * float(bool(x.overproof_flag)) + (1 - st) * _unit(
        x.expansion_fall_from_peak_fraction, 0.0, c["fall_hi"])
    s_acid = _unit(c["ph_hi"] - x.final_ph, 0.0, c["ph_hi"] - c["ph_lo"])
    wr, wg, wc = (max(0.0, c["w_size_rate"]), max(0.0, c["w_size_gwi"]),
                  max(0.0, c["w_size_collapse"]))
    wsum = (wr + wg + wc) or 1.0
    s_size = (wr * s_rate + wg * s_gwi + wc * s_collapse) / wsum
    s_irreg = 0.5 * (s_rate + s_gwi)
    s_weak = 0.5 * (s_acid + s_gwi)

    porosity = c["porosity_min"] + (c["porosity_max"] - c["porosity_min"]) * s_gas
    median = c["pore_median_min"] + (c["pore_median_max"] - c["pore_median_min"]) * s_size
    cv = c["cv_min"] + c["cv_span"] * s_collapse
    mean = median * math.sqrt(1.0 + cv * cv)  # lognormal: mean = median * sqrt(1 + cv^2)
    sd = cv * mean
    dmax = min(c["pore_max_cap"], max(c["pore_max_base"] + c["pore_max_span"] * s_collapse,
                                      2.5 * median))
    ecc = c["ecc_min"] + c["ecc_span"] * s_irreg
    jitter = c["jitter_min"] + c["jitter_span"] * s_irreg
    clustering = min(1.0, c["clustering_min"] + c["clustering_span"] * s_collapse)
    c_radius = c["cluster_radius_min"] + c["cluster_radius_span"] * s_collapse
    c_spacing = c["cluster_spacing_min"] + c["cluster_spacing_span"] * s_collapse
    wall = max(PRINTABLE_MIN_WALL_MM, c["wall_floor"] + c["wall_span"] * (1.0 - s_weak))

    r4 = lambda v: round(float(v), 4)  # geometry robust to ~1e-6 ODE noise
    pname = name or f"chamber_{x.chamber_id}"
    temp = CHAMBER_TEMPS_C.get(x.chamber_id)
    params = CrumbParams(
        name=pname,
        porosity_target=r4(porosity),
        pore_diam_mean_mm=r4(mean),
        pore_diam_sd_mm=r4(sd),
        pore_diam_max_mm=r4(dmax),
        eccentricity=r4(ecc),
        eccentricity_jitter=r4(jitter),
        clustering=r4(clustering),
        cluster_radius_mm=r4(c_radius),
        cluster_spacing_mm=r4(c_spacing),
        min_wall_mm=r4(wall),
        seed=int(seed),
        description=(f"Chamber {x.chamber_id}"
                     + (f" ({temp:g} degC)" if temp is not None else "")
                     + f": simulation-derived crumb hypothesis from the {x.kinetics_variant} "
                       f"kinetics run at t={x.t_end_h:g} h (morphology_map)."),
    )

    alch = _alchemi_fields(x)
    sim = lambda *f: [f"{k}={getattr(x, k)!r}" for k in f]
    drivers = {
        "gas_fraction": {"value": gas_fraction, "formula": "1 - 1/expansion_ratio_final",
                         "inputs": ["expansion_ratio_final"],
                         "source": "volume identity (assumes the rise is all gas)"},
        "s_gas": {"value": s_gas, "formula": "w*u(gas_fraction) + (1-w)*u(co2_retained)",
                  "inputs": ["expansion_ratio_final", "co2_retained_g_per_kg"]},
        "s_rate": {"value": s_rate, "formula": "u(max_expansion_rate_per_h; rate_lo, rate_hi)",
                   "inputs": ["max_expansion_rate_per_h"]},
        "s_gwi": {"value": s_gwi, "formula": "gluten_weakening_index (already 0..1)",
                  "inputs": ["gluten_weakening_index"]},
        "s_collapse": {"value": s_collapse,
                       "formula": "overproof_step*[overproof] + (1-overproof_step)*u(fall; 0, fall_hi)",
                       "inputs": ["overproof_flag", "expansion_fall_from_peak_fraction"]},
        "s_acid": {"value": s_acid, "formula": "u(ph_hi - final_ph; 0, ph_hi - ph_lo)",
                   "inputs": ["final_ph"]},
        "s_size": {"value": s_size, "formula": "(w_rate*s_rate + w_gwi*s_gwi + w_col*s_collapse)/sum(w)",
                   "inputs": ["s_rate", "s_gwi", "s_collapse"]},
        "s_irreg": {"value": s_irreg, "formula": "(s_rate + s_gwi)/2", "inputs": ["s_rate", "s_gwi"]},
        "s_weak": {"value": s_weak, "formula": "(s_acid + s_gwi)/2", "inputs": ["s_acid", "s_gwi"]},
    }

    def out(value, formula, kin_fields, coeffs, bounds, direction):
        return {
            "value": value,
            "formula": formula,
            "inputs": list(kin_fields),
            "bounds": bounds,
            "direction": direction,
            "provenance_chain": {
                "alchemi": alch,
                "simulated": sim(*kin_fields),
                "assumption": [f"{k}={c[k]:g} ({MAPPING_CONFIG[k]['source']})" for k in coeffs],
            },
        }

    gas_f = ("expansion_ratio_final", "co2_retained_g_per_kg")
    size_f = ("max_expansion_rate_per_h", "gluten_weakening_index", "overproof_flag",
              "expansion_fall_from_peak_fraction")
    col_f = ("overproof_flag", "expansion_fall_from_peak_fraction")
    irr_f = ("max_expansion_rate_per_h", "gluten_weakening_index")
    norm_gas = ("gas_fraction_lo", "gas_fraction_hi", "co2_retained_lo", "co2_retained_hi",
                "w_gas_expansion")
    norm_size = ("rate_lo", "rate_hi", "fall_hi", "overproof_step", "w_size_rate", "w_size_gwi",
                 "w_size_collapse")
    norm_col = ("fall_hi", "overproof_step")
    outputs = {
        "porosity_target": out(
            params.porosity_target, "porosity_min + (porosity_max - porosity_min)*s_gas", gas_f,
            norm_gas + ("porosity_min", "porosity_max"), [c["porosity_min"], c["porosity_max"]],
            "increases with expansion_ratio_final and co2_retained"),
        "pore_diam_median_mm": out(
            r4(median), "pore_median_min + (pore_median_max - pore_median_min)*s_size", size_f,
            norm_size + ("pore_median_min", "pore_median_max"),
            [c["pore_median_min"], c["pore_median_max"]],
            "increases with max rise rate, GWI, overproof and fall-from-peak"),
        "pore_diam_cv": out(r4(cv), "cv_min + cv_span*s_collapse", col_f,
                            norm_col + ("cv_min", "cv_span"), [c["cv_min"], c["cv_min"] + c["cv_span"]],
                            "increases with collapse"),
        "pore_diam_mean_mm": out(params.pore_diam_mean_mm, "median*sqrt(1+cv^2) (lognormal)",
                                 size_f, norm_size, None, "follows median and cv"),
        "pore_diam_sd_mm": out(params.pore_diam_sd_mm, "cv*mean", size_f, norm_size, None,
                               "follows mean and cv"),
        "pore_diam_max_mm": out(
            params.pore_diam_max_mm,
            "min(cap, max(pore_max_base + pore_max_span*s_collapse, 2.5*median))", size_f,
            ("pore_max_base", "pore_max_span", "pore_max_cap"),
            [c["pore_max_base"], c["pore_max_cap"]], "increases with collapse and median"),
        "eccentricity": out(params.eccentricity, "ecc_min + ecc_span*s_irreg", irr_f,
                            ("rate_lo", "rate_hi", "ecc_min", "ecc_span"),
                            [c["ecc_min"], c["ecc_min"] + c["ecc_span"]],
                            "increases with max rise rate and GWI"),
        "eccentricity_jitter": out(params.eccentricity_jitter, "jitter_min + jitter_span*s_irreg",
                                   irr_f, ("rate_lo", "rate_hi", "jitter_min", "jitter_span"),
                                   [c["jitter_min"], c["jitter_min"] + c["jitter_span"]],
                                   "increases with max rise rate and GWI"),
        "clustering": out(params.clustering, "clustering_min + clustering_span*s_collapse", col_f,
                          norm_col + ("clustering_min", "clustering_span"),
                          [c["clustering_min"], min(1.0, c["clustering_min"] + c["clustering_span"])],
                          "increases with overproof and fall-from-peak (merged cells)"),
        "cluster_radius_mm": out(params.cluster_radius_mm,
                                 "cluster_radius_min + cluster_radius_span*s_collapse", col_f,
                                 norm_col + ("cluster_radius_min", "cluster_radius_span"),
                                 [c["cluster_radius_min"],
                                  c["cluster_radius_min"] + c["cluster_radius_span"]],
                                 "increases with collapse"),
        "cluster_spacing_mm": out(params.cluster_spacing_mm,
                                  "cluster_spacing_min + cluster_spacing_span*s_collapse", col_f,
                                  norm_col + ("cluster_spacing_min", "cluster_spacing_span"),
                                  [c["cluster_spacing_min"],
                                   c["cluster_spacing_min"] + c["cluster_spacing_span"]],
                                  "increases with collapse"),
        "min_wall_mm": out(params.min_wall_mm,
                           "max(0.9, wall_floor + wall_span*(1 - s_weak))",
                           ("final_ph", "gluten_weakening_index"),
                           ("ph_hi", "ph_lo", "wall_floor", "wall_span"),
                           [PRINTABLE_MIN_WALL_MM, c["wall_floor"] + c["wall_span"]],
                           "decreases with lower pH and higher GWI; never below 0.9 mm"),
        "seed": {"value": int(seed), "formula": "fixed; identical for both chambers",
                 "inputs": [], "bounds": None, "direction": "n/a",
                 "provenance_chain": {"alchemi": [], "simulated": [],
                                      "assumption": ["common random numbers across chambers"]}},
    }
    unmapped = {
        "final_fermentation_quotient": (
            "recorded, not mapped: CrumbParams has no anisotropy/orientation parameter and the "
            "lactic:acetic ratio is a flavour/acidity descriptor with no established direct "
            "crumb-geometry effect at this resolution"),
        "co2_retained_peak_g_per_kg": "recorded; only the end-of-window value drives porosity",
        "expansion_ratio_peak": "recorded; enters only via expansion_fall_from_peak_fraction",
    }
    notes = [
        DISCLAIMER,
        ("u(x; lo, hi) = clip((x - lo)/(hi - lo), 0, 1); all drivers in [0, 1], so every "
         "output is bounded by construction and monotone in each input."),
        "Mapped CrumbParams are rounded to 1e-4 so the geometry is robust to ~1e-6 ODE noise.",
    ]
    if x.fallback_fields:
        notes.append(f"fallback values used for: {', '.join(x.fallback_fields)}")
    if not alch:
        notes.append("No ALCHEMI-informed parameter entered this kinetics run "
                     f"(variant: {x.kinetics_variant}).")
    rep = MappingReport(
        chamber_id=x.chamber_id,
        provenance=PROVENANCE,
        kinetics_variant=x.kinetics_variant,
        inputs={**x.values(), "t_end_h": x.t_end_h},
        input_source=x.source,
        fallback_fields=list(x.fallback_fields),
        drivers=drivers,
        outputs=outputs,
        unmapped=unmapped,
        config={k: {**MAPPING_CONFIG[k], "value": c[k]} for k in MAPPING_CONFIG},
        seed=int(seed),
        notes=notes,
    )
    return params, rep


def attribution(
    alchemi: MorphologyInputs | None, literature: MorphologyInputs,
    config: Mapping[str, Any] | None = None, seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """How much the ALCHEMI nudge moved each kinetics input and each mapped crumb parameter
    (ALCHEMI-informed minus literature run; same schedule, same mapping)."""
    if alchemi is None:
        return {"available": False, "note": "no ALCHEMI-informed kinetics run"}
    pa, ra = map_to_crumb_params(alchemi, config, seed)
    pl, rl = map_to_crumb_params(literature, config, seed)
    d_in = {k: float(getattr(alchemi, k)) - float(getattr(literature, k)) for k in INPUT_FIELDS}
    d_out = {}
    for k, o in ra.outputs.items():
        if k == "seed":
            continue
        a, b = float(o["value"]), float(rl.outputs[k]["value"])
        d_out[k] = {"alchemi_informed": a, "literature": b, "delta": a - b,
                    "relative": (a - b) / b if b else None}
    same = dataclasses.replace(pa, name="x", description="") == dataclasses.replace(
        pl, name="x", description="")
    return {
        "available": True,
        "input_deltas_alchemi_minus_literature": d_in,
        "crumb_param_deltas": d_out,
        "geometry_identical_after_rounding": bool(same),
        "max_abs_relative_param_change": max(
            (abs(v["relative"]) for v in d_out.values() if v["relative"] is not None), default=0.0),
    }


# ----------------------------------------------------------------------------- specimens


@dataclass
class _Specimen:
    chamber_id: str
    params: CrumbParams
    report: MappingReport
    inputs: MorphologyInputs
    literature: MorphologyInputs
    kin: dict[str, Any]
    mesh: Any
    field_stats: dict
    summary: dict
    validation: Any | None
    seconds: float


def _build_specimen(chamber_id: str, morph_json_path: str | Path, *,
                    kinetics_variant: str = "alchemi_informed",
                    config: Mapping[str, Any] | None = None, seed: int = DEFAULT_SEED,
                    validate: bool = True) -> _Specimen:
    from .crumb import generate_crumb
    from .render import mesh_summary
    from .validation import validate_mesh

    t0 = time.time()
    kin = kinetics_inputs(chamber_id, morph_json_path)
    lit = kin["literature"]
    if kinetics_variant == "alchemi_informed" and kin["alchemi_informed"] is not None:
        inp = kin["alchemi_informed"]
    else:
        inp = lit
    params, rep = map_to_crumb_params(inp, config, seed)
    rep.alchemi_attribution = attribution(kin["alchemi_informed"], lit, config, seed)
    if kinetics_variant == "alchemi_informed" and kin["alchemi_informed"] is None:
        rep.notes.append("ALCHEMI-informed run unavailable; literature kinetics used. "
                         + (kin["error"] or ""))
    if kin["reproduces_json"] is not None:
        rep.notes.append("literature re-run reproduces the morphology JSON: "
                         f"{kin['reproduces_json']}")
    mesh, cf = generate_crumb(params, return_field=True)
    mesh.metadata["mapping"] = "fermentai.geometry.morphology_map"
    s = mesh_summary(mesh, params)
    val = validate_mesh(mesh, params.min_wall_mm, name=params.name) if validate else None
    return _Specimen(chamber_id, params, rep, inp, lit, kin, mesh, cf.stats, s, val,
                     time.time() - t0)


def _write_specimen(sp: _Specimen, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"crumb_{sp.params.name}"
    paths = {"stl": out_dir / f"{stem}.stl",
             "mapping_report": out_dir / f"{stem}_mapping.json"}
    sp.mesh.export(paths["stl"])
    sp.report.save(paths["mapping_report"])
    if sp.validation is not None:
        paths["validation"] = out_dir / f"{stem}_validation.json"
        sp.validation.save(paths["validation"])
    return paths


def generate_chamber_specimen(
    chamber_id: str,
    morph_json_path: str | Path | None = None,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    *,
    kinetics_variant: str = "alchemi_informed",
    config: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    validate: bool = True,
) -> dict[str, Path]:
    """Generate one chamber's crumb STL + validation JSON + mapping report. Returns paths."""
    if morph_json_path is None:
        morph_json_path = DEFAULT_PREDICTIONS_DIR / f"chamber_{chamber_id}_morphology.json"
    sp = _build_specimen(chamber_id, morph_json_path, kinetics_variant=kinetics_variant,
                         config=config, seed=seed, validate=validate)
    return _write_specimen(sp, Path(out_dir))


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _public_summary(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


def generate_chain(
    chambers: tuple[str, ...] = ("A", "B"),
    predictions_dir: str | Path = DEFAULT_PREDICTIONS_DIR,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    *,
    kinetics_variant: str = "alchemi_informed",
    config: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    validate: bool = True,
    render: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Generate every chamber specimen, the side-by-side render and chain_summary.json."""
    out_dir, predictions_dir = Path(out_dir), Path(predictions_dir)
    specs: list[_Specimen] = []
    files: dict[str, dict[str, Path]] = {}
    for cid in chambers:
        mj = predictions_dir / f"chamber_{cid}_morphology.json"
        sp = _build_specimen(cid, mj, kinetics_variant=kinetics_variant, config=config,
                             seed=seed, validate=validate)
        files[cid] = _write_specimen(sp, out_dir)
        files[cid]["morphology_json"] = mj
        specs.append(sp)
        if verbose:
            p = sp.params
            print(f"[chamber {cid}] {sp.inputs.kinetics_variant} kinetics -> porosity "
                  f"{p.porosity_target:.3f}, pore mean {p.pore_diam_mean_mm:.2f} mm, ecc "
                  f"{p.eccentricity:.2f}, clustering {p.clustering:.2f}, wall {p.min_wall_mm:.2f} mm"
                  f" | {len(sp.mesh.faces):,} tris, {sp.seconds:.1f}s"
                  + ("" if sp.validation is None else
                     f", validation {'PASSED' if sp.validation.passed else 'FAILED'}"))
    render_path = None
    if render and specs:
        render_path = out_dir / "crumb_chamber_comparison.png"
        render_chamber_comparison(specs, render_path)
    summary_path = out_dir / "chain_summary.json"
    summary_path.write_text(json.dumps(_jsonable(_chain_summary(specs, files, render_path)),
                                       indent=2))
    return {"files": files, "render": render_path, "chain_summary": summary_path,
            "specimens": specs}


def _chain_summary(specs: list[_Specimen], files: dict, render_path: Path | None) -> dict:
    chambers: dict[str, Any] = {}
    for sp in specs:
        f = files[sp.chamber_id]
        up = sp.inputs.upstream or {}
        dp = up.get("descriptor_provenance")
        s = _public_summary(sp.summary)
        chambers[sp.chamber_id] = {
            "temperature_c": CHAMBER_TEMPS_C.get(sp.chamber_id),
            "stl": f["stl"], "stl_sha256": _sha256(f["stl"]), "stl_bytes": f["stl"].stat().st_size,
            "validation_json": f.get("validation"),
            "validation_passed": None if sp.validation is None else sp.validation.passed,
            "mapping_report": f["mapping_report"],
            "kinetics": {
                "provenance": "simulated",
                "variant_used": sp.inputs.kinetics_variant,
                "source": sp.inputs.source,
                "morphology_json": f["morphology_json"],
                "schedule": up.get("schedule"),
                "t_end_h": sp.inputs.t_end_h,
                "summary_used": sp.inputs.values(),
                "literature_summary": sp.literature.values(),
                "literature_rerun_reproduces_json": sp.kin.get("reproduces_json"),
                "fallback_fields": list(sp.inputs.fallback_fields),
            },
            "alchemi": {
                "applied_to_kinetics": bool(up.get("alchemi_applied")),
                "status": up.get("status"),
                # descriptor_provenance is a dict in older reports and a plain string now
                "descriptor_provenance": (dp.get("label") if isinstance(dp, dict) else dp),
                "method": (dp.get("method") if isinstance(dp, dict) else None),
                "checkpoint": (dp.get("checkpoint") if isinstance(dp, dict) else None),
                "descriptor_source_files": (dp.get("source_files") if isinstance(dp, dict) else None),
                "descriptors_used": up.get("descriptors"),
                "changed_kinetic_parameters": up.get("changed_parameters"),
                "notes": up.get("notes"),
                "cross_reference": "data/predictions/molecular_coupling_report.json",
                "attribution": sp.report.alchemi_attribution,
            },
            "crumb_params": sp.params.to_dict(),
            "mapping_drivers": {k: v["value"] for k, v in sp.report.drivers.items()},
            "measured_on_mesh": {
                k: s.get(k) for k in (
                    "cut_face_pore_area_fraction", "porosity_target", "cut_face_pore_count",
                    "cut_face_pore_density_per_cm2", "cut_face_eq_diameter_mm",
                    "porosity_3d_void_fraction", "mean_pore_depth_mm", "volume_mm3",
                    "extents_mm", "triangle_count")},
            "generator_stats": sp.field_stats,
        }
    comparison = {}
    if len(specs) == 2:
        a, b = specs
        sa, sb = a.summary, b.summary
        comparison = {
            "pair": f"{b.chamber_id} - {a.chamber_id}",
            "porosity_target": b.params.porosity_target - a.params.porosity_target,
            "pore_diam_mean_mm": b.params.pore_diam_mean_mm - a.params.pore_diam_mean_mm,
            "eccentricity": b.params.eccentricity - a.params.eccentricity,
            "clustering": b.params.clustering - a.params.clustering,
            "min_wall_mm": b.params.min_wall_mm - a.params.min_wall_mm,
            "measured_cut_face_pore_fraction": (sb["cut_face_pore_area_fraction"]
                                                - sa["cut_face_pore_area_fraction"]),
            "measured_section_diameter_mean_mm": (
                (sb["cut_face_eq_diameter_mm"] or {}).get("mean", float("nan"))
                - (sa["cut_face_eq_diameter_mm"] or {}).get("mean", float("nan"))),
            "measured_pore_count": sb["cut_face_pore_count"] - sa["cut_face_pore_count"],
        }
    return {
        "provenance": PROVENANCE,
        "disclaimer": DISCLAIMER,
        "generated_by": "fermentai.geometry.morphology_map.generate_chain",
        "chain": [
            {"step": "ALCHEMI descriptors", "provenance": "real (AIMNet2 via nvalchemi-toolkit, "
             "remote GPU)", "artifact": "data/alchemi/analysis/molecular_descriptors.json"},
            {"step": "kinetic parameter nudge", "provenance": "alchemi-informed (bounded)",
             "artifact": "fermentation.kinetics.apply_molecular_descriptors"},
            {"step": "kinetics ODE + morphology summary at window end", "provenance": "simulated",
             "artifact": "fermentation.simulation.simulate / summarize_for_morphology"},
            {"step": "morphology mapping", "provenance": "assumption (documented coefficients)",
             "artifact": "fermentai.geometry.morphology_map.MAPPING_CONFIG"},
            {"step": "stochastic crumb geometry -> STL", "provenance": "simulated (seeded)",
             "artifact": "fermentai.geometry.crumb.generate_crumb"},
        ],
        "render": render_path,
        "chambers": chambers,
        "comparison_B_minus_A": comparison,
        "how_to_test": ("Photograph real loaf cross-sections with a scale; run "
                        "image_analysis.analyze_crumb_image(path, scale_mm_per_px=...) and "
                        "compare_to_params(measured, CrumbParams) against crumb_chamber_<id>."),
    }


# ----------------------------------------------------------------------------- render


def _chamber_text(sp: _Specimen) -> str:
    x, p, s = sp.inputs, sp.params, sp.summary
    eq = s.get("cut_face_eq_diameter_mm") or {}
    var = "ALCHEMI-informed" if x.kinetics_variant == "alchemi_informed" else x.kinetics_variant
    med = sp.report.outputs["pore_diam_median_mm"]["value"]
    return (
        f"KINETICS AT {x.t_end_h:g} h ({var} run, simulated)\n"
        f"retained CO2   {x.co2_retained_g_per_kg:.2f} g/kg   rise {x.expansion_ratio_final:.2f}x "
        f"(peak {x.expansion_ratio_peak:.2f}x)\n"
        f"max rise rate  {x.max_expansion_rate_per_h:.3f} /h   fall from peak "
        f"{100 * x.expansion_fall_from_peak_fraction:.0f} %\n"
        f"pH {x.final_ph:.2f}   gluten weakening {x.gluten_weakening_index:.2f}   "
        f"overproof {'yes' if x.overproof_flag else 'no'}\n"
        f"\nMAPPED PARAMETERS (assumption; seed {p.seed})\n"
        f"porosity target {p.porosity_target:.3f}   min wall {p.min_wall_mm:.2f} mm\n"
        f"pore diameter   median {med:.2f}, mean {p.pore_diam_mean_mm:.2f} "
        f"± {p.pore_diam_sd_mm:.2f} mm\n"
        f"eccentricity    {p.eccentricity:.2f}   clustering {p.clustering:.2f}\n"
        f"\nMEASURED ON MESH\n"
        f"cut-face pore fraction {s['cut_face_pore_area_fraction']:.3f}   "
        f"pores {s['cut_face_pore_count']}\n"
        f"section diameter {eq.get('mean', float('nan')):.2f} mm mean, "
        f"{eq.get('p90', float('nan')):.2f} mm p90\n"
        f"validation {'n/a' if sp.validation is None else ('PASSED' if sp.validation.passed else 'FAILED')}"
        f"   {s['triangle_count']:,} triangles"
    )


def render_chamber_comparison(specs: list[_Specimen], path: str | Path) -> None:
    """Side-by-side figure (one row per chamber), reusing the crumb render helpers."""
    import matplotlib.pyplot as plt
    import numpy as np

    from .render import GRID, INK, INK2, MUTED, draw_oblique, draw_top

    bins = np.arange(0.0, 14.5, 0.5)  # wider than render.HIST_BINS: coarse cells reach ~12 mm
    n = len(specs)
    H = 5.6 * n + 1.6
    fig = plt.figure(figsize=(16.5, H), facecolor="white")
    gs = fig.add_gridspec(n, 3, width_ratios=[1.0, 1.2, 0.95], wspace=0.08, hspace=0.2,
                          left=0.03, right=0.985, top=1 - 1.45 / H, bottom=0.7 / H)
    for i, sp in enumerate(specs):
        color = CHAMBER_COLORS.get(sp.chamber_id, INK)
        ax_t = fig.add_subplot(gs[i, 0])
        ax_o = fig.add_subplot(gs[i, 1], projection="3d")
        sub = gs[i, 2].subgridspec(2, 1, height_ratios=[0.8, 1.3], hspace=0.38)
        ax_h, ax_p = fig.add_subplot(sub[0]), fig.add_subplot(sub[1])
        draw_top(ax_t, sp.summary, 12.0)
        draw_oblique(ax_o, sp.summary, 12.0)
        d = np.asarray(sp.summary["_eq_diameters_mm"])
        ax_h.hist(d, bins=bins, weights=np.full(len(d), 100.0 / max(len(d), 1)),
                  color=color, edgecolor="white", linewidth=1.0)
        for o in specs:
            if o is sp:
                continue
            od = np.asarray(o.summary["_eq_diameters_mm"])
            ax_h.hist(od, bins=bins, weights=np.full(len(od), 100.0 / max(len(od), 1)),
                      histtype="step", color=MUTED, linewidth=1.2, linestyle="--",
                      label=f"Chamber {o.chamber_id} (for comparison)")
        if n > 1:
            ax_h.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper right")
        ax_h.set_xlim(bins[0], bins[-1])
        ax_h.set_xlabel("pore section diameter on cut face (mm)", fontsize=8.5, color=INK2)
        ax_h.set_ylabel("% of pores", fontsize=8.5, color=INK2)
        ax_h.tick_params(colors=INK2, labelsize=8, length=0)
        for side in ("top", "right"):
            ax_h.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax_h.spines[side].set_color(GRID)
        ax_h.grid(axis="y", color=GRID, linewidth=0.6)
        ax_h.set_axisbelow(True)
        ax_h.set_title("pore-size distribution", loc="left", fontsize=9.5, color=INK2)
        ax_p.set_axis_off()
        ax_p.text(0, 1, _chamber_text(sp), va="top", ha="left", family="monospace",
                  fontsize=7.7, color=INK2, transform=ax_p.transAxes, linespacing=1.35)
        temp = CHAMBER_TEMPS_C.get(sp.chamber_id)
        ax_t.set_title(f"     CHAMBER {sp.chamber_id}"
                       + (f" ({temp:g} °C)" if temp is not None else "") + "  ·  cut face",
                       loc="left", fontsize=11, color=INK, fontweight="bold", pad=6)
        ax_t.add_patch(plt.Rectangle((0.0, 1.022), 0.028, 0.028, transform=ax_t.transAxes,
                                     color=color, clip_on=False))
        ax_o.set_title("oblique view (z × 1.5)", fontsize=9.5, color=INK2, pad=0)
    fig.suptitle("Chamber A (22 °C) vs Chamber B (27 °C) — simulation-derived crumb hypotheses",
                 x=0.03, y=1 - 0.3 / H, ha="left", va="top", fontsize=14, color=INK,
                 fontweight="bold")
    atts = [sp.report.alchemi_attribution or {} for sp in specs]
    if all(a.get("available") for a in atts):
        mx = max(a.get("max_abs_relative_param_change", 0.0) for a in atts)
        alch_note = f"ALCHEMI moved the mapped parameters by at most {100 * mx:.2g} %"
    else:
        alch_note = "ALCHEMI-informed kinetics run unavailable (literature kinetics used)"
    fig.text(0.03, 1 - 0.72 / H,
             "Chain: ALCHEMI descriptors (real) → kinetics at 16 h (simulated) → mapping "
             "(assumption) → seeded crumb STL (simulated).  80 × 80 × 12 mm, Formlabs resin.  "
             f"{alch_note}.",
             fontsize=9, color=INK2, ha="left", va="top")
    fig.text(0.03, 0.25 / H, f"Provenance: simulated.  {DISCLAIMER}", fontsize=8, color=MUTED,
             ha="left", va="bottom")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
