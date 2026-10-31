"""Sourdough fermentation kinetics: parameters, rate laws and the buffered pH model.

Everything here is a *model* (provenance label ``"simulated"``). Nothing in this module reads
sensors. Units are per kg of fresh dough unless noted; biomass states are dimensionless proxies
normalised to the inoculated level (initial value = 1).

Public API (stable; other packages import these)
------------------------------------------------
- ``STATE_NAMES`` – order of the ODE state vector.
- ``ParameterSpec`` – metadata for one parameter (value, units, min/max, reference, ...).
- ``load_kinetics_config(path=None) -> dict`` – raw YAML (``configs/kinetics.yaml``).
- ``parameter_specs(path=None) -> dict[str, ParameterSpec]`` – every parameter *and* initial
  condition, keyed by name.
- ``default_params(path=None) -> dict[str, float]`` – ``{name: value}`` for all of the above.
- ``resolve_params(params=None) -> dict[str, float]`` – defaults overlaid with a partial dict.
- ``ctmi(T, t_min, t_opt, t_max)`` – Rosso et al. (1993) cardinal-temperature factor (0..1).
- ``ph_from_acids(lactic_g_kg, acetic_g_kg, params) -> float`` – charge-balance pH with a lumped
  flour buffer.
- ``titratable_acidity(lactic, acetic, ph, params) -> float`` – TTA proxy (mL 0.1 N NaOH / 10 g
  dough to pH 8.5).
- ``rhs(t, y, temperature_c, p) -> np.ndarray`` – ODE right-hand side.
- ``expansion_from_state(gas_retained, gluten_integrity, temperature_c, p) -> (ratio, capacity)``.
- ``apply_molecular_descriptors(params=None, path="data/alchemi/analysis/molecular_descriptors.json",
  return_report=False)`` - upstream ALCHEMI hook: nudges the relative lactic-vs-acetic acid terms
  (``undissociated_lactic_rel_potency``, ``pka_lactic``, ``pka_acetic``) within their ranges;
  safe no-op if the file is absent/partial. ``read_molecular_descriptors(path)`` parses the file.

Equations are written out in ``docs/scientific_assumptions.md``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import brentq

PROVENANCE = "simulated"

#: ODE state vector order. The first six are the §6 model states; the last two are auxiliary
#: states of the phenomenological dough-expansion sub-model.
STATE_NAMES: tuple[str, ...] = (
    "substrate",  # fermentable carbohydrate, g hexose-equivalent / kg dough
    "yeast",  # yeast biomass proxy, relative to inoculum (dimensionless)
    "lab",  # lactic-acid-bacteria biomass proxy, relative to inoculum (dimensionless)
    "lactic_acid",  # g / kg dough (total, dissociated + undissociated)
    "acetic_acid",  # g / kg dough (total)
    "co2_cumulative",  # g CO2 produced / kg dough (cumulative, modelled; NOT a sensor value)
    "gas_retained",  # g CO2 held as gas bubbles in the dough / kg dough (auxiliary)
    "gluten_integrity",  # gas-holding capacity of the gluten network, 1 = fresh (auxiliary)
)

# Molar masses (g/mol)
MW_LACTIC = 90.08
MW_ACETIC = 60.05
MW_HEXOSE = 180.16
MW_CO2 = 44.01
R_GAS = 8.314462618  # J / (mol K)
P_ATM = 101325.0  # Pa

ROOT = Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    """Directory holding the YAML configs (override with ``FERMENTAI_CONFIG_DIR``)."""
    return Path(os.environ.get("FERMENTAI_CONFIG_DIR", ROOT / "configs"))


@dataclass(frozen=True)
class ParameterSpec:
    """Metadata for a single model parameter or initial condition."""

    name: str
    value: float
    units: str
    min: float
    max: float
    reference: str
    group: str = "parameters"
    sd: float | None = None
    sampled: bool = True
    note: str = ""
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def sample(self, rng: np.random.Generator) -> float:
        """Draw one value inside [min, max].

        Truncated normal around ``value`` when ``sd`` is given, otherwise triangular with mode
        ``value``. Non-sampled parameters return ``value``.
        """
        if not self.sampled or self.max <= self.min:
            return float(self.value)
        if self.sd:
            for _ in range(1000):
                x = rng.normal(self.value, self.sd)
                if self.min <= x <= self.max:
                    return float(x)
            return float(np.clip(self.value, self.min, self.max))
        mode = float(np.clip(self.value, self.min, self.max))
        return float(rng.triangular(self.min, mode, self.max))


def _resolve_path(path: str | Path | None, name: str) -> Path:
    return Path(path) if path is not None else config_dir() / name


@lru_cache(maxsize=8)
def _load_yaml_cached(path_str: str, mtime: float) -> dict:
    with open(path_str) as fh:
        return yaml.safe_load(fh)


def load_kinetics_config(path: str | Path | None = None) -> dict:
    """Load ``configs/kinetics.yaml`` (cached by path + mtime). Returns a fresh copy."""
    p = _resolve_path(path, "kinetics.yaml")
    import copy

    return copy.deepcopy(_load_yaml_cached(str(p), p.stat().st_mtime))


def parameter_specs(path: str | Path | None = None) -> dict[str, ParameterSpec]:
    """All parameters and initial conditions from kinetics.yaml as ``ParameterSpec`` objects."""
    cfg = load_kinetics_config(path)
    specs: dict[str, ParameterSpec] = {}
    known = {"value", "units", "min", "max", "sd", "sampled", "reference", "note", "source"}
    for group in ("parameters", "initial_conditions"):
        for name, d in (cfg.get(group) or {}).items():
            if name in specs:
                raise ValueError(f"duplicate parameter name {name!r} in kinetics.yaml")
            specs[name] = ParameterSpec(
                name=name,
                value=float(d["value"]),
                units=str(d["units"]),
                min=float(d.get("min", d["value"])),
                max=float(d.get("max", d["value"])),
                sd=(float(d["sd"]) if d.get("sd") is not None else None),
                sampled=bool(d.get("sampled", True)),
                reference=str(d["reference"]),
                note=str(d.get("note", "")),
                source=str(d.get("source", "")),
                group=group,
                extra={k: v for k, v in d.items() if k not in known},
            )
    return specs


def default_params(path: str | Path | None = None) -> dict[str, float]:
    """``{name: value}`` for every parameter and initial condition."""
    return {k: s.value for k, s in parameter_specs(path).items()}


def resolve_params(params: Mapping[str, float] | None = None) -> dict[str, float]:
    """Defaults from kinetics.yaml overlaid with ``params`` (unknown keys raise ``KeyError``)."""
    p = default_params()
    if params:
        unknown = set(params) - set(p)
        if unknown:
            raise KeyError(f"unknown kinetic parameter(s): {sorted(unknown)}")
        p.update({k: float(v) for k, v in params.items()})
    return p


# ---------------------------------------------------------------------------------------------
# Rate-law building blocks
# ---------------------------------------------------------------------------------------------


def ctmi(T: float, t_min: float, t_opt: float, t_max: float) -> float:
    """Cardinal Temperature Model with Inflection (Rosso et al. 1993), normalised to 1 at t_opt.

    Returns 0 outside (t_min, t_max).
    """
    if T <= t_min or T >= t_max:
        return 0.0
    num = (T - t_max) * (T - t_min) ** 2
    den = (t_opt - t_min) * (
        (t_opt - t_min) * (T - t_opt) - (t_opt - t_max) * (t_opt + t_min - 2.0 * T)
    )
    if den == 0:
        return 0.0
    return min(1.0, max(0.0, num / den))


def undissociated_fraction(ph: float, pka: float) -> float:
    """Henderson–Hasselbalch fraction of a monoprotic acid in the protonated (HA) form."""
    return 1.0 / (1.0 + 10.0 ** (ph - pka))


def ph_factor(ph: float, ph_min: float) -> float:
    """Presser et al. (1997)-type pH factor ``1 - 10**(pH_min - pH)``, clipped to [0, 1]."""
    return min(1.0, max(0.0, 1.0 - 10.0 ** (ph_min - ph)))


def co2_gas_density(temperature_c: float) -> float:
    """Ideal-gas density of CO2 at 1 atm, g/L."""
    return P_ATM * MW_CO2 / (R_GAS * (temperature_c + 273.15)) / 1000.0


# ---------------------------------------------------------------------------------------------
# Buffered pH model
# ---------------------------------------------------------------------------------------------


def _buffer_groups(p: Mapping[str, float]) -> list[tuple[float, float]]:
    """(concentration mol/kg dough, pKa) for the lumped flour buffer groups."""
    return [
        (p["buffer_b1"] / 1000.0, p["buffer_pka1"]),
        (p["buffer_b2"] / 1000.0, p["buffer_pka2"]),
    ]


def _proton_balance(ph: float, la_mol: float, ac_mol: float, p: Mapping[str, float]) -> float:
    """Protons released by acids minus protons taken up by the buffer (mol/kg dough)."""
    h = 10.0**-ph
    h_ref = 10.0 ** -p["ph_flour"]
    released = la_mol * (1.0 - undissociated_fraction(ph, p["pka_lactic"])) + ac_mol * (
        1.0 - undissociated_fraction(ph, p["pka_acetic"])
    )
    bound = 0.0
    for conc, pka in _buffer_groups(p):
        ka = 10.0**-pka
        bound += conc * (h / (h + ka) - h_ref / (h_ref + ka))
    # free protons in the aqueous phase (mol/L water * L water/kg dough); tiny but keeps the
    # balance exact
    bound += (h - h_ref) * p["water_fraction"]
    return released - bound


def ph_from_acids(lactic_g_kg: float, acetic_g_kg: float, p: Mapping[str, float]) -> float:
    """pH of the dough aqueous phase from total lactic and acetic acid (g/kg dough).

    Solves the proton balance: protons released by (partially dissociated) lactic and acetic
    acid = protons bound by two lumped flour buffer groups (proteins, phosphates/phytate, ...)
    relative to the acid-free flour dough at ``ph_flour``. Ideal (activity = concentration)
    behaviour is assumed. Returns ``ph_flour`` when there is no acid.
    """
    la = max(lactic_g_kg, 0.0) / MW_LACTIC
    ac = max(acetic_g_kg, 0.0) / MW_ACETIC
    if la + ac <= 0:
        return float(p["ph_flour"])
    lo, hi = 1.5, float(p["ph_flour"])
    f_lo = _proton_balance(lo, la, ac, p)
    if f_lo > 0:  # cannot happen for physical inputs; guard anyway
        return lo
    return float(brentq(_proton_balance, lo, hi, args=(la, ac, p), xtol=1e-6))


def titratable_acidity(
    lactic_g_kg: float, acetic_g_kg: float, ph: float, p: Mapping[str, float], end_ph: float = 8.5
) -> float:
    """Titratable-acidity proxy: mL of 0.1 N NaOH per 10 g dough to reach ``end_ph``.

    Counts the remaining undissociated acids plus buffer groups that would be deprotonated
    between the current pH and the endpoint. (TTA is *total* acidity, not pH.)
    """
    la = max(lactic_g_kg, 0.0) / MW_LACTIC
    ac = max(acetic_g_kg, 0.0) / MW_ACETIC
    mol = la * (
        undissociated_fraction(ph, p["pka_lactic"])
        - undissociated_fraction(end_ph, p["pka_lactic"])
    )
    mol += ac * (
        undissociated_fraction(ph, p["pka_acetic"])
        - undissociated_fraction(end_ph, p["pka_acetic"])
    )
    h, h_end = 10.0**-ph, 10.0**-end_ph
    for conc, pka in _buffer_groups(p):
        ka = 10.0**-pka
        mol += conc * (h / (h + ka) - h_end / (h_end + ka))
    # mol/kg -> mmol per 10 g = mol*10 ; mL of 0.1 N = mmol / 0.1
    return max(0.0, mol * 10.0 / 0.1)


# ---------------------------------------------------------------------------------------------
# Expansion sub-model
# ---------------------------------------------------------------------------------------------


def expansion_from_state(
    gas_retained: float, gluten_integrity: float, temperature_c: float, p: Mapping[str, float]
) -> tuple[float, float]:
    """(expansion ratio V/V0, current gas-holding capacity) from auxiliary states.

    V/V0 = 1 + gas volume / initial dough volume; in a straight-sided vessel this equals the
    relative height h/h0 measured by the side camera.
    """
    gas_l_per_kg = max(gas_retained, 0.0) / co2_gas_density(temperature_c) * p["gas_volume_factor"]
    ratio = 1.0 + gas_l_per_kg / p["dough_specific_volume"]
    capacity = 1.0 + (p["expansion_max"] - 1.0) * max(gluten_integrity, 0.0)
    return ratio, capacity


# ---------------------------------------------------------------------------------------------
# ODE right-hand side
# ---------------------------------------------------------------------------------------------


@dataclass
class Rates:
    """Diagnostic rates/factors at one instant (useful for agents explaining the model)."""

    ph: float
    f_temp_yeast: float
    f_temp_lab: float
    mu_yeast: float
    mu_lab: float
    lactic_rate: float
    acetic_rate: float
    co2_rate: float
    sugar_rate: float
    expansion_ratio: float
    expansion_capacity: float


def _rates(y: np.ndarray, T: float, p: Mapping[str, float]) -> tuple[np.ndarray, Rates]:
    S, XY, XL, La, Ac, C, G, Q = (max(float(v), 0.0) for v in y)

    ph = ph_from_acids(La, Ac, p)
    fTY = ctmi(T, p["t_min_yeast"], p["t_opt_yeast"], p["t_max_yeast"])
    fTL = ctmi(T, p["t_min_lab"], p["t_opt_lab"], p["t_max_lab"])
    monod_y = S / (p["ks_yeast"] + S)
    monod_l = S / (p["ks_lab"] + S)

    # undissociated acids in the aqueous phase, mmol/L. Yeast inhibition (Gänzle 1998) is
    # expressed in acetic-acid equivalents: [HAc] + r * [HLa], r = relative potency of
    # undissociated lactic vs acetic acid (assumption; ALCHEMI-adjustable, see
    # apply_molecular_descriptors).
    hac_mm = (
        Ac / MW_ACETIC * undissociated_fraction(ph, p["pka_acetic"]) / p["water_fraction"] * 1000.0
    )
    hla_mm = (
        La / MW_LACTIC * undissociated_fraction(ph, p["pka_lactic"]) / p["water_fraction"] * 1000.0
    )
    hac_eq_mm = hac_mm + p["undissociated_lactic_rel_potency"] * hla_mm
    f_ac_y = max(0.0, 1.0 - hac_eq_mm / p["hac_inhib_yeast"])
    f_ph_y = ph_factor(ph, p["ph_min_yeast"])
    f_ph_lg = ph_factor(ph, p["ph_min_growth_lab"])
    f_ph_lp = ph_factor(ph, p["ph_min_acid_lab"])

    mu_y = (
        p["mu_max_yeast"] * fTY * monod_y * f_ac_y * f_ph_y * max(0.0, 1.0 - XY / p["xmax_yeast"])
    )
    mu_l = p["mu_max_lab"] * fTL * monod_l * f_ph_lg * max(0.0, 1.0 - XL / p["xmax_lab"])
    dXY = mu_y * XY
    dXL = mu_l * XL

    # Luedeking–Piret lactic acid production (growth-associated + maintenance / non-growth)
    r_la = p["alpha_lactic"] * dXL + p["beta_lactic"] * fTL * monod_l * f_ph_lp * XL
    fq = p["fq_ref"] * math.exp(p["fq_temp_coeff"] * (T - p["fq_ref_temp"]))
    r_ac = r_la / MW_LACTIC / fq * MW_ACETIC

    het = p["lab_hetero_fraction"]
    la_mol = r_la / MW_LACTIC
    r_s_lab = la_mol * (het * 1.0 + (1.0 - het) * 0.5) * MW_HEXOSE
    r_s_y = p["qs_yeast"] * fTY * monod_y * f_ac_y * f_ph_y * XY
    dS = -(r_s_lab + r_s_y)

    r_co2 = p["yield_co2_yeast"] * r_s_y + het * la_mol * MW_CO2

    # Expansion: CO2 first dissolves in the dough liquor, then inflates gas cells. Retention of
    # new gas falls as the rise approaches the (degrading) gas-holding capacity of the gluten
    # network; gas is lost slowly by leakage and quickly by rupture once rise > capacity.
    E, Ecap = expansion_from_state(G, Q, T, p)
    to_gas = 1.0 - math.exp(-C / p["co2_dissolved_capacity"])
    fill = E / Ecap
    # retention efficiency of newly produced gas falls as cells crowd toward capacity
    retention = max(0.0, 1.0 - fill ** p["rupture_exponent"])
    # active cell rupture only when the rise exceeds the current (degrading) capacity
    k_loss = p["k_gas_leak"] + p["k_rupture"] * max(0.0, fill - 1.0)
    dG = r_co2 * to_gas * retention - k_loss * G
    acid_drive = max(0.0, p["gluten_ph_onset"] - ph)
    dQ = (
        -p["k_gluten_decay"]
        * p["q10_gluten"] ** ((T - 25.0) / 10.0)
        * (1.0 + p["gluten_acid_sensitivity"] * acid_drive)
        * Q
    )

    dy = np.array([dS, dXY, dXL, r_la, r_ac, r_co2, dG, dQ], dtype=float)
    diag = Rates(
        ph=ph,
        f_temp_yeast=fTY,
        f_temp_lab=fTL,
        mu_yeast=mu_y,
        mu_lab=mu_l,
        lactic_rate=r_la,
        acetic_rate=r_ac,
        co2_rate=r_co2,
        sugar_rate=-dS,
        expansion_ratio=E,
        expansion_capacity=Ecap,
    )
    return dy, diag


def rhs(t: float, y: np.ndarray, temperature_c: float, p: Mapping[str, float]) -> np.ndarray:
    """ODE right-hand side dy/dt (per hour) at dough temperature ``temperature_c``."""
    return _rates(y, temperature_c, p)[0]


def diagnostics(y: np.ndarray, temperature_c: float, p: Mapping[str, float]) -> Rates:
    """Instantaneous rates and limiting factors for state ``y`` (for explanations/debugging)."""
    return _rates(np.asarray(y, dtype=float), temperature_c, p)[1]


def initial_state(
    p: Mapping[str, float], overrides: Mapping[str, float] | None = None
) -> np.ndarray:
    """Initial state vector from ``*_0`` initial conditions, optionally overridden by state name."""
    y0 = {
        "substrate": p["substrate_0"],
        "yeast": p["yeast_0"],
        "lab": p["lab_0"],
        "lactic_acid": p["lactic_acid_0"],
        "acetic_acid": p["acetic_acid_0"],
        "co2_cumulative": 0.0,
        "gas_retained": 0.0,
        "gluten_integrity": 1.0,
    }
    if overrides:
        unknown = set(overrides) - set(y0)
        if unknown:
            raise KeyError(
                f"unknown state name(s) in initial: {sorted(unknown)}; valid: {STATE_NAMES}"
            )
        y0.update({k: float(v) for k, v in overrides.items()})
    return np.array([y0[n] for n in STATE_NAMES], dtype=float)


# ---------------------------------------------------------------------------------------------
# Upstream coupling: ALCHEMI molecular descriptors -> relative lactic-vs-acetic acid terms
# ---------------------------------------------------------------------------------------------

DEFAULT_DESCRIPTORS_PATH = "data/alchemi/analysis/molecular_descriptors.json"

_ACID_KEYS = {
    "lactic": ("lactic_acid", "lactic", "LA", "lactate"),
    "acetic": ("acetic_acid", "acetic", "AA", "acetate"),
}

# Candidate inputs in order of preference: (field, kind, campaign label). A candidate is used only
# if BOTH acids have a finite value for that same field (never mixes definitions across acids).
# kind "by_n": dict {n: value} -> mean over the n values present (finite) for both acids.
_ASSOC_CANDIDATES = (
    (
        "c2_boltzmann_assoc_energy_per_water_kcal_by_n_neutral",
        "by_n",
        "campaign 2 (Boltzmann-averaged neutral clusters, mean over common n)",
    ),
    (
        "c2_assoc_energy_per_water_kcal_by_n_neutral",
        "by_n",
        "campaign 2 (lowest-minimum neutral clusters, mean over common n)",
    ),
    ("assoc_energy_per_water_kcal", "scalar", "campaign 1 (lowest conformers, mean over n=1,3,6)"),
    ("assoc_energy_per_water_kcal_mol", "scalar", "campaign 1"),
    ("dE_assoc_per_water_kcal_mol", "scalar", "campaign 1"),
)
_HBOND_CANDIDATES = (
    ("hbond_min_A", "scalar", "campaign 1 (shortest acid-water H...O contact)"),
    ("hbond_min_angstrom", "scalar", "campaign 1"),
    ("hbond_min_a", "scalar", "campaign 1"),
)
# Fallback for the relative pKa when the top-level relative_pka_estimate is absent.
_DEPROT_CANDIDATES = (
    ("deprot_energy_n6_kcal", "scalar", "campaign 1 (n=6 contact ion pair, 0 K electronic)"),
    ("deprot_energy_n6_kcal_mol", "scalar", "campaign 1 (n=6 contact ion pair)"),
    (
        "deprot_energy_separated_ions_n6_kcal",
        "scalar",
        "campaign 1 (separated gas-phase ions, n=6, 0 K electronic)",
    ),
)


def _finite(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _acid_block(data: Any, acid: str) -> dict | None:
    """Find the descriptor dict for one acid in a tolerant set of JSON layouts."""
    if isinstance(data, list):
        for rec in data:
            if (
                isinstance(rec, dict)
                and str(rec.get("acid", rec.get("name", ""))) in _ACID_KEYS[acid]
            ):
                return rec
        return None
    if not isinstance(data, dict):
        return None
    for container in (data, data.get("acids"), data.get("descriptors"), data.get("per_acid")):
        if isinstance(container, dict):
            for k in _ACID_KEYS[acid]:
                if isinstance(container.get(k), dict):
                    return container[k]
        if isinstance(container, list):
            found = _acid_block(container, acid)
            if found is not None:
                return found
    return None


def _pair_value(lac: dict, ace: dict, candidates) -> dict | None:
    """First candidate field with finite values for both acids -> selection record."""
    for fld, kind, campaign in candidates:
        if fld not in lac or fld not in ace:
            continue
        if kind == "by_n":
            a, b = lac[fld], ace[fld]
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            ns = sorted(
                (
                    n
                    for n in a
                    if n in b and _finite(a[n]) is not None and _finite(b[n]) is not None
                ),
                key=lambda n: float(n) if _finite(n) is not None else 0.0,
            )
            if not ns:
                continue
            va = float(np.mean([_finite(a[n]) for n in ns]))
            vb = float(np.mean([_finite(b[n]) for n in ns]))
            return {
                "field": fld,
                "campaign": campaign,
                "lactic": va,
                "acetic": vb,
                "difference": va - vb,
                "n_used": [str(n) for n in ns],
            }
        va, vb = _finite(lac[fld]), _finite(ace[fld])
        if va is not None and vb is not None:
            return {
                "field": fld,
                "campaign": campaign,
                "lactic": va,
                "acetic": vb,
                "difference": va - vb,
            }
    return None


def _relative_pka_estimate(data: Any) -> dict | None:
    """Top-level campaign-2 ``relative_pka_estimate`` (cluster-phase dpKa lactic - acetic)."""
    if not isinstance(data, dict):
        return None
    est = data.get("relative_pka_estimate")
    if not isinstance(est, dict):
        return None
    val = _finite(est.get("value"))
    if val is None:
        return None
    return {
        "value": val,
        "uncertainty": _finite(est.get("uncertainty")),
        "experiment": _finite(est.get("experiment")),
        "per_n": est.get("per_n") if isinstance(est.get("per_n"), dict) else None,
        "method": est.get("method"),
        "note": est.get("note"),
    }


def read_molecular_descriptors(path: str | Path | None = None) -> dict[str, Any]:
    """Parse the ALCHEMI descriptor file. Never raises.

    Returns ``{"status": "read", "path", "descriptor_provenance", "lactic": {...},
    "acetic": {...}, "relative_pka_estimate": {...} | None}`` where the per-acid dicts are the raw
    blocks, or ``{"status": "absent" | "invalid", ...}``.
    """
    p = Path(path) if path is not None else Path(DEFAULT_DESCRIPTORS_PATH)
    if not p.is_absolute() and not p.exists():
        p = ROOT / p
    if not p.exists():
        return {"status": "absent", "path": str(p)}
    try:
        import json

        data = json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"status": "invalid", "path": str(p), "error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, Any] = {"status": "read", "path": str(p)}
    if isinstance(data, dict):
        out["descriptor_provenance"] = data.get("provenance", data.get("source"))
    for acid in ("lactic", "acetic"):
        out[acid] = _acid_block(data, acid) or {}
    out["relative_pka_estimate"] = _relative_pka_estimate(data)
    return out


def _label(prov: Any) -> Any:
    return prov.get("label", prov) if isinstance(prov, dict) else prov


def apply_molecular_descriptors(
    params: Mapping[str, float] | None = None,
    path: str | Path | None = DEFAULT_DESCRIPTORS_PATH,
    return_report: bool = False,
):
    """Nudge the *relative* lactic-vs-acetic acid parameters using ALCHEMI cluster descriptors.

    Returns a new full parameter dict (defaults overlaid with ``params``), or
    ``(params, report)`` when ``return_report=True``. If the descriptor file is absent, invalid
    or lacks a usable lactic+acetic pair for a mapping, the affected parameters are returned
    unchanged with ``source: "literature"`` / ``"assumption"`` (never NaN, never out of range).

    Mappings (docs/scientific_assumptions.md §9; config ``molecular_coupling`` in kinetics.yaml).
    Only lactic-minus-acetic *differences* are used; the report records which field and which
    ALCHEMI campaign each input came from.

    1. ``undissociated_lactic_rel_potency``: hydrophilicity score
       ``h = mean(-dAssoc/assoc_scale, -dHbond/hbond_scale)``. dAssoc prefers the campaign-2
       Boltzmann-averaged association energy per water (mean over the cluster sizes n present
       for both acids), then campaign-2 lowest-minimum, then campaign-1. dHbond is the campaign-1
       shortest acid-water H-bond. h > 0 (lactic more strongly hydrated) moves the potency toward
       ``min`` by ``tanh(h)`` of the gap; h < 0 toward ``max``.
    2. ``pka_lactic`` / ``pka_acetic``: the cluster-phase relative pKa (lactic - acetic) is taken
       from the top-level campaign-2 ``relative_pka_estimate.value`` when present, otherwise from
       a deprotonation-energy difference / (RT ln 10) (n=6 ion pair, then separated ions, same
       field for both acids). ``dpka_shrinkage`` (10 %) of its discrepancy from the literature
       gap (-0.90) is applied to the gap, capped at ``2 * dpka_cap`` (0.05), split equally
       between the two acids and clipped to their [min, max].
    """
    specs = parameter_specs()
    p = resolve_params(params)
    cfg = load_kinetics_config().get("molecular_coupling") or {}
    if path is None:
        path = cfg.get("descriptors_path", DEFAULT_DESCRIPTORS_PATH)
    targets = ("undissociated_lactic_rel_potency", "pka_lactic", "pka_acetic")
    report: dict[str, Any] = {
        "path": str(path),
        "parameters": {
            n: {
                "value": p[n],
                "literature_value": specs[n].value,
                "source": specs[n].source or "literature",
            }
            for n in targets
        },
        "inputs": {},
        "notes": [],
    }
    desc = read_molecular_descriptors(path)
    report["status"] = desc["status"]
    report["path"] = desc.get("path", str(path))
    if desc["status"] != "read":
        if "error" in desc:
            report["notes"].append(desc["error"])
        return (p, report) if return_report else p
    report["descriptor_provenance"] = _label(desc.get("descriptor_provenance"))
    lac, ace = desc["lactic"], desc["acetic"]

    applied = []
    # 1) relative inhibition potency of undissociated lactic acid -------------------------------
    assoc = _pair_value(lac, ace, _ASSOC_CANDIDATES)
    hb = _pair_value(lac, ace, _HBOND_CANDIDATES)
    comps = []
    if assoc is not None:
        report["inputs"]["association_energy_per_water"] = assoc
        comps.append(-assoc["difference"] / float(cfg.get("assoc_scale_kcal", 2.0)))
    if hb is not None:
        report["inputs"]["hbond_min"] = hb
        comps.append(-hb["difference"] / float(cfg.get("hbond_scale_A", 0.50)))
    n = "undissociated_lactic_rel_potency"
    if comps:
        h = float(np.mean(comps))
        s = specs[n]
        r0 = p[n]
        r = r0 - (r0 - s.min) * math.tanh(h) if h >= 0 else r0 + (s.max - r0) * math.tanh(-h)
        r = float(np.clip(r, s.min, s.max))
        p[n] = r
        report["parameters"][n].update(
            value=r,
            source="alchemi-informed",
            inputs={
                "d_assoc_per_water_kcal": assoc["difference"] if assoc else None,
                "d_assoc_campaign": assoc["campaign"] if assoc else None,
                "d_hbond_min_A": hb["difference"] if hb else None,
                "d_hbond_campaign": hb["campaign"] if hb else None,
            },
            mapping=f"hydrophilicity score h={h:.3f}; tanh-bounded move within [{s.min}, {s.max}]",
        )
        applied.append(n)
    else:
        report["notes"].append(
            "potency: no association-energy or H-bond field with finite values for both acids"
        )

    # 2) small relative pKa correction ----------------------------------------------------------
    rt = float(cfg.get("rt_ln10_kcal", 1.364))
    lit_gap = specs["pka_lactic"].value - specs["pka_acetic"].value
    est = desc.get("relative_pka_estimate")
    rel = None
    if est is not None:
        rel = {
            "field": "relative_pka_estimate.value",
            "campaign": "campaign 2 (cluster-phase quasi-harmonic exchange cycle, n=6 and n=10)",
            "cluster_dpka": est["value"],
            "uncertainty": est["uncertainty"],
            "per_n": est["per_n"],
            "method": est["method"],
        }
    else:
        dep = _pair_value(lac, ace, _DEPROT_CANDIDATES)
        if dep is not None:
            rel = {
                "field": dep["field"],
                "campaign": dep["campaign"],
                "cluster_dpka": dep["difference"] / rt,
                "uncertainty": None,
                "d_deprot_energy_kcal": dep["difference"],
            }
    if rel is not None:
        cap = float(cfg.get("dpka_cap", 0.025))
        shrink = float(cfg.get("dpka_shrinkage", 0.10))
        cluster_gap = rel["cluster_dpka"]
        raw_shift = shrink * (cluster_gap - lit_gap)
        shift = float(np.clip(raw_shift, -2 * cap, 2 * cap))
        new_la = float(
            np.clip(p["pka_lactic"] + shift / 2, specs["pka_lactic"].min, specs["pka_lactic"].max)
        )
        new_ac = float(
            np.clip(p["pka_acetic"] - shift / 2, specs["pka_acetic"].min, specs["pka_acetic"].max)
        )
        p["pka_lactic"], p["pka_acetic"] = new_la, new_ac
        exp_gap = (
            est["experiment"] if est is not None and est["experiment"] is not None else lit_gap
        )
        dev = cluster_gap - exp_gap
        unc = rel.get("uncertainty")
        comparison = {
            "cluster_dpka_lactic_minus_acetic": cluster_gap,
            "cluster_uncertainty": unc,
            "experimental_dpka": exp_gap,
            "literature_dpka_used_by_model": lit_gap,
            "deviation_from_experiment": dev,
            "deviation_in_sigma": (dev / unc) if unc else None,
            "sign_agrees_with_experiment": bool(cluster_gap < 0) == bool(exp_gap < 0),
            "raw_shift_before_cap": raw_shift,
            "applied_gap_shift": shift,
            "cap_binding": abs(raw_shift) > 2 * cap,
            "resulting_model_dpka": new_la - new_ac,
        }
        rel["comparison_to_experiment"] = comparison
        report["inputs"]["relative_pka"] = rel
        for nm, v in (("pka_lactic", new_la), ("pka_acetic", new_ac)):
            report["parameters"][nm].update(
                value=v,
                source="alchemi-informed",
                inputs={
                    "field": rel["field"],
                    "campaign": rel["campaign"],
                    "cluster_dpka": cluster_gap,
                    "experimental_dpka": exp_gap,
                },
                mapping=(
                    f"gap shift = clip({shrink:g} x ({cluster_gap:+.3f} - ({lit_gap:+.2f})), "
                    f"+-{2 * cap:g}) = {shift:+.4f}"
                ),
            )
        applied += ["pka_lactic", "pka_acetic"]
        if not comparison["sign_agrees_with_experiment"]:
            report["notes"].append(
                "cluster estimate does not reproduce lactic < acetic pKa ordering; correction capped"
            )
        if comparison["cap_binding"]:
            report["notes"].append(
                "pKa: the 0.05 cap binds; the applied shift reflects only the sign of the "
                "cluster-vs-experiment discrepancy"
            )
    else:
        report["notes"].append(
            "pKa: no relative_pka_estimate and no deprotonation field with finite values for both acids"
        )

    report["applied"] = applied
    report["status"] = (
        "applied"
        if len(applied) == len(targets)
        else ("partial" if applied else "no_usable_fields")
    )
    return (p, report) if return_report else p
