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
    known = {"value", "units", "min", "max", "sd", "sampled", "reference", "note"}
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

    # undissociated acetic acid in the aqueous phase, mmol/L (yeast inhibitor; Gänzle 1998)
    hac_mm = (
        Ac / MW_ACETIC * undissociated_fraction(ph, p["pka_acetic"]) / p["water_fraction"] * 1000.0
    )
    f_ac_y = max(0.0, 1.0 - hac_mm / p["hac_inhib_yeast"])
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
