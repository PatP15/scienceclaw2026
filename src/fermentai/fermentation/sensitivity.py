"""Parameter sensitivity of key outputs (pH at 12 h, peak expansion and its timing).

API:
- ``key_outputs(df) -> dict`` – ph_12h, peak_expansion, peak_time_h.
- ``one_at_a_time(schedule, names=None, t_end_h=16.0) -> DataFrame`` – each sampled parameter
  set to its configured min and max (others nominal); reports the output swing.
- ``global_rank_correlation(unc_result) -> DataFrame`` – Spearman rank correlation between
  sampled parameters and outputs from a ``simulate_with_uncertainty`` result (all parameters
  varied jointly).
All results are model (in-silico) sensitivities, provenance "simulated".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import kinetics as K
from .simulation import TemperatureSchedule, simulate

OUTPUTS = ("ph_12h", "peak_expansion", "peak_time_h")


def key_outputs(df: pd.DataFrame) -> dict[str, float]:
    e = df.expansion_ratio.to_numpy()
    i = int(np.argmax(e))
    return {
        "ph_12h": float(np.interp(12.0, df.time_h, df.ph)),
        "peak_expansion": float(e[i]),
        "peak_time_h": float(df.time_h.iloc[i]),
    }


def one_at_a_time(
    schedule: TemperatureSchedule,
    names: Iterable[str] | None = None,
    t_end_h: float = 16.0,
    dt_h: float = 0.05,
) -> pd.DataFrame:
    specs = K.parameter_specs()
    names = list(names) if names is not None else [n for n, s in specs.items() if s.sampled]
    base = key_outputs(simulate(schedule, t_end_h=t_end_h, dt_h=dt_h))
    rows = []
    for n in names:
        s = specs[n]
        lo = key_outputs(simulate(schedule, params={n: s.min}, t_end_h=t_end_h, dt_h=dt_h))
        hi = key_outputs(simulate(schedule, params={n: s.max}, t_end_h=t_end_h, dt_h=dt_h))
        row: dict[str, Any] = {
            "parameter": n,
            "nominal": s.value,
            "min": s.min,
            "max": s.max,
            "units": s.units,
            "reference": s.reference,
        }
        for o in OUTPUTS:
            row[f"{o}_at_min"] = lo[o]
            row[f"{o}_at_max"] = hi[o]
            row[f"{o}_swing"] = hi[o] - lo[o]
            row[f"{o}_abs_swing"] = abs(hi[o] - lo[o])
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs.update(baseline=base, provenance=K.PROVENANCE)
    out["provenance"] = K.PROVENANCE
    return out


def global_rank_correlation(unc: dict) -> pd.DataFrame:
    t = unc["time_h"]
    S = unc["samples"]
    ph12 = np.array([np.interp(12.0, t, row) for row in S["ph"]]) if t[-1] >= 12 else None
    E = S["expansion_ratio"]
    outs = {"peak_expansion": E.max(axis=1), "peak_time_h": t[np.argmax(E, axis=1)]}
    if ph12 is not None:
        outs = {"ph_12h": ph12, **outs}
    P = unc["param_samples"]
    specs = K.parameter_specs()
    rows = []
    for n in P.columns:
        if n not in specs or not specs[n].sampled or P[n].nunique() < 3:
            continue
        row: dict[str, Any] = {"parameter": n}
        for o, y in outs.items():
            rho = spearmanr(P[n], y).statistic if np.ptp(y) > 0 else np.nan
            row[f"{o}_spearman"] = float(rho)
        rows.append(row)
    out = pd.DataFrame(rows)
    out["provenance"] = K.PROVENANCE
    return out
