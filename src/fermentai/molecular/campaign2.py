"""Campaign-2 analysis (Mac-side, no GPU): relative pKa cycle, sampling statistics, larger clusters,
co-solutes, acid dimers and long-MD hydrogen-bond dynamics.

Reads (all optional; missing parts are skipped):
    data/alchemi/results/json/      campaign 1 minima (pooled; same model and settings)
    data/alchemi/results_c2/json/   campaign 2 minima (batched FIRE2, anneal-and-quench)
    data/alchemi/vib_c2/*.json      tight minima + harmonic thermochemistry
    data/alchemi/md_long/           20 ps multi-replica NVT runs (.json + _frames.npz)
Writes into data/alchemi/analysis/ (c2_*.csv / .json / .png / .html) and extends
molecular_descriptors.json with *new* keys only (existing keys are never modified).

Definitions (energies are AIMNet2 gas-phase cluster values; kcal/mol):
* Species minima are de-duplicated (energies equal within 0.01 kcal/mol count once).
* Boltzmann conformational free energy F = E_min - RT ln sum_i exp(-(E_i - E_min)/RT), T = 298.15 K.
* Relative acidity: HA.nW + A'-.nW -> A-.nW + HA'.nW (no H3O+/H2O needed, they cancel):
  ddX(n) = [X(lactate-.nW) - X(lactic.nW)] - [X(acetate-.nW) - X(acetic.nW)], X = E, F or G.
  Relative pKa (lactic - acetic) = ddG / (RT ln 10) with RT ln 10 = 1.3642 kcal/mol at 298.15 K.
* Uncertainty from conformer sampling: bootstrap (B = 2000) over the seeded starts of each of the four
  species, recomputing the min-energy ddE; reported as the standard deviation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fermentai.molecular import analysis as an

ALCHEMI_DIR = an.ALCHEMI_DIR
EV_TO_KCAL = an.EV_TO_KCAL
KB_KCAL = 0.0019872036
T_REF = 298.15
RT = KB_KCAL * T_REF
RT_LN10 = RT * math.log(10.0)
PKA_EXP = {"lactic_acid": 3.86, "acetic_acid": 4.76}  # aqueous, 25 C (CRC Handbook)
ANION = {"lactic_acid": "lactic_acid_anion", "acetic_acid": "acetic_acid_anion"}
EXPECTED_STATE = {"acid_water_cluster": "neutral_acid", "anion_water_cluster": "anion"}


# ----------------------------------------------------------------------------- loading


def species_key(r: dict) -> str | None:
    """Canonical species name: lactic_acid, acetic_acid_anion, water_cluster, ethanol, ... (no n)."""
    k, acid = r.get("kind"), r.get("acid")
    meta = r.get("input_meta") or {}
    if k in ("acid_monomer", "acid_water_cluster"):
        return acid
    if k in ("anion_monomer", "anion_water_cluster"):
        return ANION.get(acid)
    if k in ("water_monomer", "water_cluster"):
        return "water"
    if k == "hydronium":
        return "hydronium"
    if k in ("cosolute_monomer", "cosolute_water_cluster"):
        return meta.get("cosolute")
    if k == "cosolute_cluster":
        return f"{acid}+{meta.get('cosolute')}"
    if k in ("acid_dimer", "mixed_acid_cluster"):
        return f"{acid}+{meta.get('partner')}"
    return None


def load_pool(dirs: list[Path]) -> pd.DataFrame:
    """All usable minima from the given result dirs with species/n keys and sanity flags."""
    rows = []
    for d in dirs:
        for p in sorted(Path(d).glob("*.json")):
            r = json.loads(p.read_text())
            sp = species_key(r)
            if sp is None or r.get("energy_ev") is None:
                continue
            ok = an.usable(r)
            state = None
            if ok and r.get("kind") in EXPECTED_STATE:
                state = an.protonation_state(r["symbols"], np.array(r["optimized_positions_angstrom"]),
                                             r["fragments"])["state"]
                ok = state == EXPECTED_STATE[r["kind"]]
            frag_ok = True
            if ok and len(r.get("fragments") or []) > 1:
                frag_ok = not _detached(r)
            rows.append({"id": r["id"], "species": sp, "n_water": int(r.get("n_water") or 0), "kind": r.get("kind"),
                         "energy_ev": float(r["energy_ev"]), "usable": bool(ok and frag_ok), "state": state,
                         "fragmented": not frag_ok, "campaign": r.get("campaign", 1),
                         "origin": "anneal_quench" if r.get("origin") else "seeded_start",
                         "source_dir": Path(d).parent.name})
    return pd.DataFrame(rows)


def _detached(r: dict, cutoff: float = 4.5) -> bool:
    x = np.array(r["optimized_positions_angstrom"])
    fidx, _ = an._fragment_index(r["fragments"], len(r["symbols"]))
    return any(an._min_heavy_distance_to_rest(x, r["symbols"], fidx, k) > cutoff for k in range(len(r["fragments"])))


# ----------------------------------------------------------------------------- statistics


DEDUP_E_KCAL = 0.05  # float32 energy noise of identical minima is up to ~0.03 kcal/mol
DEDUP_FP_ANGSTROM = 0.02  # RMS difference of sorted interatomic-distance fingerprints


def unique_minima(e_kcal: np.ndarray, tol: float = DEDUP_E_KCAL) -> np.ndarray:
    e = np.sort(np.asarray(e_kcal, dtype=float))
    out = []
    for v in e:
        if not out or v - out[-1] > tol:
            out.append(v)
    return np.array(out)


def boltzmann_free_energy(e_kcal: np.ndarray, T: float = T_REF) -> float:
    e = unique_minima(e_kcal)
    rt = KB_KCAL * T
    return float(e[0] - rt * np.log(np.sum(np.exp(-(e - e[0]) / rt))))


def species_table(pool: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (sp, n), g in pool[pool["usable"]].groupby(["species", "n_water"]):
        e = g["energy_ev"].to_numpy() * EV_TO_KCAL
        u = unique_minima(e)
        best = g.loc[g["energy_ev"].idxmin()]
        rows.append({"species": sp, "n_water": int(n), "n_minima": len(g), "n_unique": len(u),
                     "n_anneal_quench": int((g["origin"] == "anneal_quench").sum()),
                     "E_min_kcal": float(u[0]), "gap_to_2nd_kcal": float(u[1] - u[0]) if len(u) > 1 else math.nan,
                     "F_boltzmann_kcal": boltzmann_free_energy(e), "best_id": best["id"],
                     "best_origin": best["origin"],
                     "boltzmann_weight_of_best": float(1.0 / np.sum(np.exp(-(u - u[0]) / RT)))})
    return pd.DataFrame(rows)


def _get(st: pd.DataFrame, sp: str, n: int, col: str) -> float | None:
    m = st[(st["species"] == sp) & (st["n_water"] == n)]
    return None if m.empty else float(m.iloc[0][col])


def relative_deprotonation(st: pd.DataFrame, pool: pd.DataFrame, n: int, n_boot: int = 2000,
                           seed: int = 0) -> dict[str, Any] | None:
    """ddE(n) and ddF(n) (lactic - acetic), plus bootstrap sampling uncertainty of ddE."""
    sps = ("lactic_acid_anion", "lactic_acid", "acetic_acid_anion", "acetic_acid")
    vals = {sp: _get(st, sp, n, "E_min_kcal") for sp in sps}
    if any(v is None for v in vals.values()):
        return None
    fb = {sp: _get(st, sp, n, "F_boltzmann_kcal") for sp in sps}
    dd_e = (vals["lactic_acid_anion"] - vals["lactic_acid"]) - (vals["acetic_acid_anion"] - vals["acetic_acid"])
    dd_f = (fb["lactic_acid_anion"] - fb["lactic_acid"]) - (fb["acetic_acid_anion"] - fb["acetic_acid"])
    rng = np.random.default_rng(seed)
    arrs = {sp: pool[(pool["species"] == sp) & (pool["n_water"] == n) & pool["usable"]]["energy_ev"].to_numpy()
            * EV_TO_KCAL for sp in sps}
    boot = np.empty(n_boot)
    for b in range(n_boot):
        m = {sp: rng.choice(a, size=len(a), replace=True).min() for sp, a in arrs.items()}
        boot[b] = (m["lactic_acid_anion"] - m["lactic_acid"]) - (m["acetic_acid_anion"] - m["acetic_acid"])
    return {"n_water": n, "ddE_kcal": dd_e, "ddF_boltzmann_kcal": dd_f, "ddE_bootstrap_sd_kcal": float(boot.std()),
            "ddE_bootstrap_ci95_kcal": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "n_minima": {sp: int(len(a)) for sp, a in arrs.items()}}


# ----------------------------------------------------------------------------- thermochemistry


def load_vib(vib_dir: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(Path(vib_dir).glob("*.json")):
        r = json.loads(p.read_text())
        if r.get("status") != "completed":
            continue
        th = r["thermo"]
        rows.append({"id": r["id"], "kind": r.get("kind"), "acid": r.get("acid"), "n_water": int(r.get("n_water") or 0),
                     "species": species_key({"kind": r.get("kind"), "acid": r.get("acid"), "input_meta": {}}),
                     "E_tight_kcal": r["tight_energy_ev"] * EV_TO_KCAL,
                     "G_qh_kcal": r["G_qh_ev"] * EV_TO_KCAL, "G_rrho_kcal": r["G_rrho_ev"] * EV_TO_KCAL,
                     "E_zpe_kcal": r["E_plus_zpe_ev"] * EV_TO_KCAL, "n_imag": th["n_imag"],
                     "imag_cm1": th["imag_cm1"], "lowest_real_cm1": th["lowest_real_cm1"],
                     "tight_fmax": r["tight_fmax_ev_per_angstrom"],
                     "fingerprint": distance_fingerprint(np.array(r["tight_positions_angstrom"]))})
    df = pd.DataFrame(rows)
    return dedupe_vib(df) if not df.empty else df


def distance_fingerprint(x: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1)
    return np.sort(d[np.triu_indices(len(x), 1)])


def dedupe_vib(vib: pd.DataFrame) -> pd.DataFrame:
    """Mark duplicates (same species/n, |dE_tight| < 0.05 kcal/mol and fingerprint RMS < 0.02 A); keep lowest G."""
    vib = vib.sort_values("G_qh_kcal").reset_index(drop=True)
    dup_of = [None] * len(vib)
    for i in range(len(vib)):
        if dup_of[i] is not None:
            continue
        for j in range(i + 1, len(vib)):
            if dup_of[j] is not None or vib.at[i, "species"] != vib.at[j, "species"] or vib.at[i, "n_water"] != vib.at[j, "n_water"]:
                continue
            fi, fj = vib.at[i, "fingerprint"], vib.at[j, "fingerprint"]
            if (len(fi) == len(fj) and abs(vib.at[i, "E_tight_kcal"] - vib.at[j, "E_tight_kcal"]) < DEDUP_E_KCAL
                    and float(np.sqrt(np.mean((fi - fj) ** 2))) < DEDUP_FP_ANGSTROM):
                dup_of[j] = vib.at[i, "id"]
    vib["duplicate_of"] = dup_of
    return vib


def thermo_cycle(vib: pd.DataFrame, n: int, col: str = "G_qh_kcal") -> dict[str, Any] | None:
    """ddX(n) from vibrationally characterised minima: lowest X and Boltzmann over the characterised set."""
    sps = ("lactic_acid_anion", "lactic_acid", "acetic_acid_anion", "acetic_acid")
    lo, bz = {}, {}
    if "duplicate_of" in vib:
        vib = vib[vib["duplicate_of"].isna()]
    for sp in sps:
        g = vib[(vib["species"] == sp) & (vib["n_water"] == n)]
        if g.empty:
            return None
        lo[sp] = float(g[col].min())
        bz[sp] = boltzmann_free_energy(g[col].to_numpy())

    def dd(d):
        return (d["lactic_acid_anion"] - d["lactic_acid"]) - (d["acetic_acid_anion"] - d["acetic_acid"])

    return {"n_water": n, "quantity": col, "dd_lowest_kcal": dd(lo), "dd_boltzmann_kcal": dd(bz),
            "n_characterised": {sp: int(((vib["species"] == sp) & (vib["n_water"] == n)).sum()) for sp in sps},
            "n_imag_max": int(vib[vib["n_water"] == n]["n_imag"].max())}


def relative_pka(cycles: list[dict], deprots: list[dict], prefer_n: tuple[int, ...] = (6, 10)) -> dict[str, Any]:
    """Headline relative pKa (lactic - acetic) from the cluster thermodynamic cycle."""
    use = [c for c in cycles if c and c["n_water"] in prefer_n]
    if not use:
        use = [c for c in cycles if c]
    if not use:
        return {"value": None, "note": "no vibrationally characterised cycle available"}
    per_n = {}
    for c in use:
        boot = next((d["ddE_bootstrap_sd_kcal"] for d in deprots if d and d["n_water"] == c["n_water"]), None)
        per_n[str(c["n_water"])] = {"ddG_qh_kcal": c["dd_boltzmann_kcal"], "dpKa": c["dd_boltzmann_kcal"] / RT_LN10,
                                    "ddG_qh_lowest_kcal": c["dd_lowest_kcal"],
                                    "sampling_sd_kcal": boot}
    dd = np.array([v["ddG_qh_kcal"] for v in per_n.values()])
    sds = [v["sampling_sd_kcal"] for v in per_n.values() if v["sampling_sd_kcal"] is not None]
    spread_n = float(np.ptp(dd) / 2) if len(dd) > 1 else 0.0
    spread_choice = float(np.mean([abs(v["ddG_qh_kcal"] - v["ddG_qh_lowest_kcal"]) for v in per_n.values()]))
    unc_kcal = float(math.sqrt((max(sds) if sds else 0.0) ** 2 + spread_n ** 2 + spread_choice ** 2))
    val = float(dd.mean() / RT_LN10)
    exp = PKA_EXP["lactic_acid"] - PKA_EXP["acetic_acid"]
    return {"value": val, "uncertainty": unc_kcal / RT_LN10, "units": "pKa units (lactic - acetic)",
            "ddG_kcal": float(dd.mean()), "ddG_uncertainty_kcal": unc_kcal,
            "experiment": exp, "experiment_pka": dict(PKA_EXP), "experiment_ddG_kcal": exp * RT_LN10,
            "deviation_from_experiment": val - exp, "per_n": per_n,
            "uncertainty_components_kcal": {"conformer_sampling_bootstrap_sd": max(sds) if sds else None,
                                            "half_range_over_n": spread_n,
                                            "lowest_vs_boltzmann_choice": spread_choice},
            "method": "cluster-phase thermodynamic cycle HA.nW + A'-.nW -> A-.nW + HA'.nW with AIMNet2 (ALCHEMI) "
                      "electronic energies + quasi-harmonic (100 cm-1) ideal-gas G corrections at 298.15 K; "
                      "Boltzmann average over vibrationally characterised minima; dpKa = ddG / (RT ln 10)",
            "note": "cluster-phase relative estimate with 6-15 explicit waters and no bulk continuum; not an "
                    "absolute pKa and not a solution-phase prediction"}


# ----------------------------------------------------------------------------- association, dimers, cosolutes


def association_by_n(st: pd.DataFrame) -> pd.DataFrame:
    rows = []
    w = _get(st, "water", 1, "E_min_kcal")
    for sp in ("lactic_acid", "acetic_acid", "lactic_acid_anion", "acetic_acid_anion"):
        e0, f0 = _get(st, sp, 0, "E_min_kcal"), _get(st, sp, 0, "F_boltzmann_kcal")
        if e0 is None or w is None:
            continue
        for n in sorted(st[(st["species"] == sp) & (st["n_water"] > 0)]["n_water"].unique()):
            e, f = _get(st, sp, n, "E_min_kcal"), _get(st, sp, n, "F_boltzmann_kcal")
            wc = _get(st, "water", n, "E_min_kcal")
            rows.append({"species": sp, "n_water": int(n), "dE_assoc_kcal": e - e0 - n * w,
                         "dE_assoc_per_water_kcal": (e - e0 - n * w) / n,
                         "dF_assoc_boltzmann_kcal": f - f0 - n * w, "dF_assoc_per_water_kcal": (f - f0 - n * w) / n,
                         "dE_to_water_network_kcal": (e - e0 - wc) if wc is not None else math.nan,
                         "gap_to_2nd_kcal": _get(st, sp, n, "gap_to_2nd_kcal"),
                         "n_minima": int(_get(st, sp, n, "n_minima"))})
    return pd.DataFrame(rows)


def lactic_minus_acetic_by_n(assoc: pd.DataFrame, deprots: list[dict]) -> pd.DataFrame:
    rows = []
    for n in sorted(assoc["n_water"].unique()) if not assoc.empty else []:
        a = assoc[assoc["n_water"] == n].set_index("species")
        row = {"n_water": int(n)}
        if {"lactic_acid", "acetic_acid"} <= set(a.index):
            row["d_assoc_per_water_neutral_kcal"] = a.loc["lactic_acid", "dE_assoc_per_water_kcal"] - a.loc["acetic_acid", "dE_assoc_per_water_kcal"]
        if {"lactic_acid_anion", "acetic_acid_anion"} <= set(a.index):
            row["d_assoc_per_water_anion_kcal"] = a.loc["lactic_acid_anion", "dE_assoc_per_water_kcal"] - a.loc["acetic_acid_anion", "dE_assoc_per_water_kcal"]
        d = next((x for x in deprots if x and x["n_water"] == n), None)
        if d:
            row.update({"ddE_deprot_kcal": d["ddE_kcal"], "ddE_deprot_sd_kcal": d["ddE_bootstrap_sd_kcal"],
                        "ddF_deprot_boltzmann_kcal": d["ddF_boltzmann_kcal"]})
        rows.append(row)
    return pd.DataFrame(rows)


def pair_energetics(st: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    la, aa, w = (_get(st, s, 0, "E_min_kcal") for s in ("lactic_acid", "acetic_acid", "water"))
    w = _get(st, "water", 1, "E_min_kcal")
    aad = _get(st, "acetic_acid+acetic_acid", 0, "E_min_kcal")
    if aad is not None and aa is not None:
        out["acetic_dimer_kcal"] = aad - 2 * aa
        out["acetic_dimer_literature_kcal"] = [-16.0, -14.0]
    lad = _get(st, "lactic_acid+acetic_acid", 0, "E_min_kcal")
    if lad is not None and la is not None and aa is not None:
        out["lactic_acetic_dimer_kcal"] = lad - la - aa
    mix = _get(st, "lactic_acid+acetic_acid", 6, "E_min_kcal")
    l6, a6, w6 = (_get(st, s, 6, "E_min_kcal") for s in ("lactic_acid", "acetic_acid", "water"))
    if None not in (mix, la, aa, w):
        out["mixed_cluster_total_kcal"] = mix - la - aa - 6 * w
    if None not in (mix, l6, a6, w6):
        out["mixed_cluster_pairing_kcal"] = mix + w6 - l6 - a6
        out["mixed_cluster_pairing_definition"] = "E(L.A.6W) + E((H2O)6) - E(L.6W) - E(A.6W)"
    return out


def cosolute_energetics(st: pd.DataFrame, cosolutes: list[str], n_w: int = 3) -> dict[str, Any]:
    out: dict[str, Any] = {}
    w = _get(st, "water", 1, "E_min_kcal")
    for acid in ("lactic_acid", "acetic_acid"):
        ea, ea3 = _get(st, acid, 0, "E_min_kcal"), _get(st, acid, n_w, "E_min_kcal")
        blk = {}
        for x in cosolutes:
            ex, ex3 = _get(st, x, 0, "E_min_kcal"), _get(st, x, n_w, "E_min_kcal")
            ec = _get(st, f"{acid}+{x}", n_w, "E_min_kcal")
            if None in (ec, ea, ex, w):
                continue
            d = {"total_kcal": ec - ea - ex - n_w * w}
            if ea3 is not None:
                d["cosolute_to_hydrated_acid_kcal"] = ec - ea3 - ex
            if ex3 is not None:
                d["acid_to_hydrated_cosolute_kcal"] = ec - ex3 - ea
            d["gap_to_2nd_kcal"] = _get(st, f"{acid}+{x}", n_w, "gap_to_2nd_kcal")
            d["best_id"] = st[(st["species"] == f"{acid}+{x}") & (st["n_water"] == n_w)].iloc[0]["best_id"]
            blk[x] = d
        out[acid] = blk
    return out


def acid_partner_hbonds(rec: dict) -> list[dict]:
    """Geometric H-bonds between fragment 0 (acid) and fragment 1 (co-solute / partner acid)."""
    hbs = an.hydrogen_bonds(rec["symbols"], np.array(rec["optimized_positions_angstrom"]), rec["fragments"])
    return [h for h in hbs if {h["donor_fragment"], h["acceptor_fragment"]} == {0, 1}]


# ----------------------------------------------------------------------------- long MD


def hbond_matrix(symbols, frames: np.ndarray, fragments, acid_frag: int = 0) -> tuple[list[tuple], np.ndarray]:
    """Boolean [n_frames, n_pairs] presence of acid(anion)-water H-bonds (either direction)."""
    keys: dict[tuple, int] = {}
    pres: list[set] = []
    for x in frames:
        s = set()
        for h in an.hydrogen_bonds(symbols, x, fragments):
            if h["type"] == "intramolecular":
                continue
            if acid_frag not in (h["donor_fragment"], h["acceptor_fragment"]):
                continue
            k = (h["donor"], h["hydrogen"], h["acceptor"])
            keys.setdefault(k, len(keys))
            s.add(keys[k])
        pres.append(s)
    m = np.zeros((len(frames), len(keys)), dtype=bool)
    for i, s in enumerate(pres):
        m[i, list(s)] = True
    return list(keys), m


def continuous_lifetimes(m: np.ndarray, dt_ps: float) -> np.ndarray:
    """Lengths (ps) of uninterrupted runs of each H-bond (runs touching the window edges included)."""
    out = []
    for j in range(m.shape[1]):
        col = np.concatenate([[False], m[:, j], [False]]).astype(int)
        d = np.diff(col)
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        out += list((ends - starts) * dt_ps)
    return np.array(out, dtype=float)


def intermittent_acf(m: np.ndarray, max_lag: int) -> np.ndarray:
    """C(t) = <h(0)h(t)>/<h> averaged over pairs and time origins (Luzar-Chandler intermittent)."""
    h = m.astype(float)
    mean = h.mean()
    if mean == 0:
        return np.zeros(max_lag)
    c = np.array([np.mean(h[: len(h) - t] * h[t:]) for t in range(max_lag)])
    return c / mean


def rdf_cluster(symbols, frames, fragments, acid_frag=0, r_max=6.0, dr=0.05, kind="O_carboxyl-O_water"):
    """Pair-distance distribution between acid carboxyl O and water O, normalised per shell volume
    (cluster g(r)-like curve; not normalised to a bulk density). Returns (r, g, coordination number)."""
    f0 = fragments[acid_frag]
    edges = np.arange(0, r_max + dr, dr)
    hist = np.zeros(len(edges) - 1)
    for x in frames:
        carb = [o for o in an.protonation_state(symbols, x, fragments)["carboxyl_O"]]
        wo = [f["start"] for f in fragments if f["name"] == "water"]
        if not carb or not wo:
            continue
        d = np.linalg.norm(x[carb][:, None, :] - x[wo][None, :, :], axis=-1).ravel()
        hist += np.histogram(d, bins=edges)[0]
    r = 0.5 * (edges[1:] + edges[:-1])
    shell = 4 * np.pi * r ** 2 * dr
    g = hist / max(len(frames), 1) / shell
    cn = np.cumsum(hist) / max(len(frames), 1) / max(len(an.protonation_state(symbols, frames[0], fragments)["carboxyl_O"]), 1)
    return r, g, cn


def proton_transfer_events(symbols, frames, fragments) -> dict[str, int]:
    """Frames where the protonation state differs from frame 0, and number of state changes."""
    states = [an.protonation_state(symbols, x, fragments)["state"] for x in frames]
    changes = sum(1 for a, b in zip(states, states[1:]) if a != b)
    return {"n_frames_state_changed": int(sum(1 for s in states if s != states[0])), "n_state_changes": int(changes),
            "initial_state": states[0], "states_seen": sorted(set(states))}


def water_exchange(symbols, frames, fragments, acid_frag: int = 0) -> dict[str, float]:
    """First shell = waters H-bonded to the acid; count additions + removals between frames."""
    shells = []
    fidx, _ = an._fragment_index(fragments, len(symbols))
    for x in frames:
        s = set()
        for h in an.hydrogen_bonds(symbols, x, fragments):
            if h["type"] == "intramolecular":
                continue
            fr = {h["donor_fragment"], h["acceptor_fragment"]}
            if acid_frag in fr:
                s |= fr - {acid_frag}
        shells.append(s)
    ev = sum(len(a ^ b) for a, b in zip(shells, shells[1:]))
    return {"exchange_events": int(ev), "mean_first_shell_waters": float(np.mean([len(s) for s in shells]))}


def analyse_md_long(md_dir: Path, max_lag_ps: float = 5.0) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows, curves = [], {}
    for jp in sorted(Path(md_dir).glob("*.json")):
        rec = json.loads(jp.read_text())
        if rec.get("status") != "completed":
            continue
        z = np.load(md_dir / f"{rec['id']}_frames.npz")
        pos = z["positions"].astype(float)  # F, R, N, 3
        dt_ps = float(rec["timestep_fs"]) * int(rec["frame_every_steps"]) / 1000.0
        syms, frags = rec["symbols"], rec["fragments"]
        lifetimes, acfs, gs, cns, nhb, pt, ex = [], [], [], [], [], [], []
        for rep in range(pos.shape[1]):
            fr = pos[:, rep]
            _, m = hbond_matrix(syms, fr, frags)
            lifetimes.append(continuous_lifetimes(m, dt_ps))
            acfs.append(intermittent_acf(m, int(max_lag_ps / dt_ps)))
            nhb.append(m.sum(axis=1).mean())
            r, g, cn = rdf_cluster(syms, fr[:: 5], frags)
            gs.append(g)
            cns.append(cn)
            pt.append(proton_transfer_events(syms, fr[:: 5], frags))
            ex.append(water_exchange(syms, fr[:: 5], frags))
        lt = np.concatenate(lifetimes) if lifetimes else np.array([])
        acf = np.mean(acfs, axis=0)
        t = np.arange(len(acf)) * dt_ps
        tau = float(np.trapezoid(acf, t)) if len(acf) else math.nan
        head = rec["start_structure"].split("__")[0]
        rows.append({"id": rec["id"], "start_structure": rec["start_structure"], "species": head,
                     "temperature_k": rec["temperature_k"], "replicas": int(pos.shape[1]),
                     "length_ps": rec["n_steps"] * rec["timestep_fs"] / 1000.0,
                     "mean_acid_water_hbonds": float(np.mean(nhb)), "sd_acid_water_hbonds_over_replicas": float(np.std(nhb)),
                     "mean_continuous_lifetime_ps": float(lt.mean()) if len(lt) else math.nan,
                     "median_continuous_lifetime_ps": float(np.median(lt)) if len(lt) else math.nan,
                     "intermittent_tau_ps": tau,
                     "proton_state_changes": int(sum(p["n_state_changes"] for p in pt)),
                     "states_seen": sorted({s for p in pt for s in p["states_seen"]}),
                     "water_exchange_events_per_ps": float(np.mean([e["exchange_events"] for e in ex]) /
                                                           (rec["n_steps"] * rec["timestep_fs"] / 1000.0)),
                     "mean_first_shell_waters": float(np.mean([e["mean_first_shell_waters"] for e in ex])),
                     "mean_temperature_k": float(np.mean([v for v in rec["mean_temperature_second_half_k"] if v]))})
        curves[rec["id"]] = {"lifetimes_ps": lt.tolist(), "acf_t_ps": t.tolist(), "acf": acf.tolist(),
                             "rdf_r": r.tolist(), "rdf_g": np.mean(gs, axis=0).tolist(),
                             "rdf_cn": np.mean(cns, axis=0).tolist(), "species": head,
                             "temperature_k": rec["temperature_k"]}
    return pd.DataFrame(rows), curves


# ----------------------------------------------------------------------------- figures


def _style_ax(ax):
    ax.set_facecolor(an.SURFACE)
    ax.grid(axis="y", color=an.GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _species_color(sp: str) -> str:
    return an.SERIES["lactic_acid" if sp.startswith("lactic") else "acetic_acid" if sp.startswith("acetic") else "water"]


def _label(sp: str) -> str:
    return {"lactic_acid": "L-lactic acid", "acetic_acid": "acetic acid", "lactic_acid_anion": "lactate⁻",
            "acetic_acid_anion": "acetate⁻"}.get(sp, sp)


def figures(out_dir: Path, prov: str, pka: dict, cycles: list, deprots: list, conv: pd.DataFrame,
            assoc: pd.DataFrame, pairs: dict, cos: dict, md_rows: pd.DataFrame, curves: dict) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    paths: list[Path] = []
    foot = prov + " · AIMNet2 (wB97M-D3) via NVIDIA ALCHEMI · gas-phase clusters"

    def save(fig, name, footnote=foot, rect_bottom=0.06):
        fig.text(0.01, 0.01, footnote, fontsize=7.5, color=an.TEXT2)
        fig.tight_layout(rect=(0, rect_bottom, 1, 0.94))
        p = out_dir / name
        fig.savefig(p, facecolor=an.SURFACE)
        plt.close(fig)
        paths.append(p)

    # 1) relative pKa: per-n ddG -> dpKa vs experiment
    if pka.get("value") is not None:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 5.4), dpi=160, gridspec_kw={"width_ratios": [1.3, 1]})
        fig.patch.set_facecolor(an.SURFACE)
        for ax in (a1, a2):
            _style_ax(ax)
        ns = [c["n_water"] for c in cycles if c]
        dE = [next((d["ddE_kcal"] for d in deprots if d and d["n_water"] == n), np.nan) / RT_LN10 for n in ns]
        sd = [next((d["ddE_bootstrap_sd_kcal"] for d in deprots if d and d["n_water"] == n), 0) / RT_LN10 for n in ns]
        dG = [c["dd_boltzmann_kcal"] / RT_LN10 for c in cycles if c]
        x = np.arange(len(ns))
        a1.errorbar(x - 0.08, dE, yerr=sd, fmt="s", color=an.TEXT2, ms=7, capsize=4, label="ΔΔE electronic (± bootstrap SD)")
        a1.plot(x + 0.08, dG, "o", color=an.SERIES["lactic_acid"], ms=9, mec=an.SURFACE, label="ΔΔG (E + qh thermal, Boltzmann)")
        a1.axhline(pka["experiment"], color=an.SERIES["acetic_acid"], lw=2, ls="--", label=f"experiment {pka['experiment']:+.2f}")
        a1.axhline(0, color="#c3c2b7", lw=1)
        a1.set_xticks(x)
        a1.set_xticklabels([f"n = {n}" for n in ns])
        a1.set_ylabel("ΔpKa (lactic − acetic)", color=an.TEXT)
        a1.set_title("Cluster thermodynamic cycle by number of explicit waters", fontsize=10, loc="left", color=an.TEXT)
        allv = [v for v in dE + dG + [pka["experiment"], 0.0] if v is not None and np.isfinite(v)]
        a1.set_ylim(min(allv) - 1.5, max(allv) + 1.0)
        a1.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
        a2.bar([0], [pka["value"]], yerr=[pka["uncertainty"]], color=an.SERIES["lactic_acid"], width=0.5, capsize=6)
        a2.bar([1], [pka["experiment"]], color=an.SERIES["acetic_acid"], width=0.5)
        for xx, v in ((0, pka["value"]), (1, pka["experiment"])):
            a2.annotate(f"{v:+.2f}", (xx + 0.27, v), textcoords="offset points", xytext=(4, 0), ha="left", va="center",
                        fontsize=10, color=an.TEXT)
        a2.set_xlim(-0.5, 1.8)
        a2.axhline(0, color="#c3c2b7", lw=1)
        a2.set_xticks([0, 1])
        a2.set_xticklabels([f"ALCHEMI estimate\n(n = {', '.join(pka['per_n'])})", "experiment\n(3.86 − 4.76)"])
        a2.set_ylabel("ΔpKa (lactic − acetic)", color=an.TEXT)
        a2.set_title(f"Headline: {pka['value']:+.2f} ± {pka['uncertainty']:.2f} vs {pka['experiment']:+.2f}",
                     fontsize=10, loc="left", color=an.TEXT)
        fig.suptitle("Relative acidity of lactic vs acetic acid: cluster-phase estimate (not a solution pKa)",
                     x=0.01, ha="left", fontsize=12, color=an.TEXT)
        save(fig, "c2_relative_pka.png", foot + " · ΔpKa = ΔΔG/(RT ln 10); uncertainty = bootstrap sampling SD ⊕ "
             "half-range over n ⊕ lowest-vs-Boltzmann choice", rect_bottom=0.1)

    # 2) convergence of lactic - acetic differences with n
    if not conv.empty:
        fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), dpi=160)
        fig.patch.set_facecolor(an.SURFACE)
        for ax in axes:
            _style_ax(ax)
            ax.axhline(0, color="#c3c2b7", lw=1)
        if "d_assoc_per_water_neutral_kcal" in conv:
            c = conv.dropna(subset=["d_assoc_per_water_neutral_kcal"])
            axes[0].plot(c["n_water"], c["d_assoc_per_water_neutral_kcal"], "-o", color=an.TEXT, lw=2,
                         label="neutral acids (lactic − acetic)")
        if "d_assoc_per_water_anion_kcal" in conv:
            c = conv.dropna(subset=["d_assoc_per_water_anion_kcal"])
            axes[0].plot(c["n_water"], c["d_assoc_per_water_anion_kcal"], "--s", color=an.TEXT2, lw=2,
                         label="anions (lactate − acetate)")
        axes[0].set_title("Δ(assoc. energy per water), lactic − acetic", fontsize=10, loc="left", color=an.TEXT)
        axes[0].set_ylabel("kcal/mol per water (negative = lactic binds water more)", color=an.TEXT)
        if "ddE_deprot_kcal" in conv:
            c = conv.dropna(subset=["ddE_deprot_kcal"])
            axes[1].errorbar(c["n_water"], c["ddE_deprot_kcal"], yerr=c["ddE_deprot_sd_kcal"], fmt="-o", capsize=4,
                             color=an.TEXT, lw=2, label="ΔΔE (lowest minima, ± bootstrap SD)")
            axes[1].plot(c["n_water"], c["ddF_deprot_boltzmann_kcal"], ":D", color=an.TEXT2, label="ΔΔF (Boltzmann)")
            if cycles:
                axes[1].plot([cc["n_water"] for cc in cycles], [cc["dd_boltzmann_kcal"] for cc in cycles], "o",
                             color=an.SERIES["lactic_acid"], ms=8, mec=an.SURFACE, label="ΔΔG (E + qh thermal)")
            axes[1].axhline(pka.get("experiment_ddG_kcal", -0.90 * RT_LN10), color=an.SERIES["acetic_acid"], ls="--",
                            label="experiment ΔΔG (from pKa)")
        axes[1].set_title("ΔΔE_deprot, lactic − acetic (negative = lactic more acidic)", fontsize=10, loc="left",
                          color=an.TEXT)
        axes[1].set_ylabel("kcal/mol", color=an.TEXT)
        for ax in axes:
            ax.set_xlabel("number of explicit water molecules n", color=an.TEXT)
            ax.set_xticks([0, 1, 3, 6, 10, 15])
            ax.legend(frameon=False, fontsize=8.5)
        fig.suptitle("Convergence of lactic − acetic differences with cluster size", x=0.01, ha="left",
                     fontsize=12, color=an.TEXT)
        save(fig, "c2_convergence_vs_n.png")

    # 3) dimers + co-solutes
    labels, vals, colors = [], [], []
    if "acetic_dimer_kcal" in pairs:
        labels.append("acetic–acetic dimer")
        vals.append(pairs["acetic_dimer_kcal"])
        colors.append(an.SERIES["acetic_acid"])
    if "lactic_acetic_dimer_kcal" in pairs:
        labels.append("lactic–acetic dimer")
        vals.append(pairs["lactic_acetic_dimer_kcal"])
        colors.append(an.SERIES["lactic_acid"])
    for acid, blk in cos.items():
        for x, d in blk.items():
            labels.append(f"{_label(acid)} + {x.replace('gln_gln', 'Ac-Gln-Gln-NHMe')} (+3 H₂O)")
            vals.append(d.get("cosolute_to_hydrated_acid_kcal", d["total_kcal"]))
            colors.append(an.SERIES[acid])
    if labels:
        fig, ax = plt.subplots(figsize=(10, 0.45 * len(labels) + 2.2), dpi=160)
        fig.patch.set_facecolor(an.SURFACE)
        _style_ax(ax)
        ax.grid(axis="x", color=an.GRID, lw=0.8)
        ax.grid(axis="y", visible=False)
        yy = np.arange(len(labels))
        ax.barh(yy, vals, color=colors, height=0.6, zorder=2)
        for y, v in zip(yy, vals):
            ax.annotate(f"{v:.1f}", (v, y), textcoords="offset points", xytext=(-5, 0), ha="right", va="center",
                        fontsize=9, color=an.TEXT, zorder=3)
        ax.set_xlim(min(vals) * 1.15, 0)
        if "acetic_dimer_kcal" in pairs:
            ax.axvspan(-16, -14, ymin=1 - 1.0 / len(labels), ymax=1.0, color=an.TEXT2, alpha=0.15, zorder=1,
                       label="literature acetic dimer (electronic) ≈ −14 to −16")
            ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.12))
        ax.set_yticks(yy)
        ax.set_yticklabels(labels, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel("interaction energy (kcal/mol); dimers: E(AB) − E(A) − E(B); co-solutes: E(acid·X·3W) − E(acid·3W) − E(X)",
                      color=an.TEXT, fontsize=8.5)
        ax.set_title("Acid–acid and acid–co-solute interaction energies (lowest minima)", fontsize=11, loc="left",
                     color=an.TEXT)
        save(fig, "c2_pair_and_cosolute_energies.png",
             foot + " · Gln-Gln = hypothesis-level proxy for acid–gluten amide contacts", rect_bottom=0.08)

    # 4) long MD: lifetimes, ACF, RDF
    if curves:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5.0), dpi=160)
        fig.patch.set_facecolor(an.SURFACE)
        for ax in axes:
            _style_ax(ax)
        for cid, c in sorted(curves.items()):
            sp, T = c["species"], c["temperature_k"]
            ls = "-" if T < 300 else "--"
            lab = f"{_label(sp)} · {T - 273.15:.0f} °C"
            col = _species_color(sp)
            lw = 2.2 if sp.endswith("anion") else 1.4
            lt = np.array(c["lifetimes_ps"])
            if len(lt):
                bins = np.logspace(-2, 1.5, 30)
                h, e = np.histogram(lt, bins=bins, density=True)
                axes[0].step(e[:-1], h, where="post", color=col, ls=ls, lw=lw, label=lab)
            axes[1].plot(c["acf_t_ps"], c["acf"], color=col, ls=ls, lw=lw, label=lab)
            axes[2].plot(c["rdf_r"], c["rdf_g"], color=col, ls=ls, lw=lw, label=lab)
        axes[0].set_xscale("log")
        axes[0].set_yscale("log")
        axes[0].set_xlabel("continuous H-bond lifetime (ps)", color=an.TEXT)
        axes[0].set_ylabel("probability density", color=an.TEXT)
        axes[0].set_title("Acid–water H-bond lifetimes", fontsize=10, loc="left", color=an.TEXT)
        axes[1].set_xlabel("t (ps)", color=an.TEXT)
        axes[1].set_ylabel("intermittent C(t)", color=an.TEXT)
        axes[1].set_title("H-bond autocorrelation (Luzar–Chandler)", fontsize=10, loc="left", color=an.TEXT)
        axes[2].set_xlabel("r(O_carboxyl···O_water) (Å)", color=an.TEXT)
        axes[2].set_ylabel("pair density per shell volume (Å⁻³)", color=an.TEXT)
        axes[2].set_title("Carboxyl O – water O distribution", fontsize=10, loc="left", color=an.TEXT)
        h_, l_ = axes[1].get_legend_handles_labels()
        fig.legend(h_, l_, frameon=False, fontsize=8, loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.03))
        fig.suptitle("20 ps NVT Langevin MD of n = 6 clusters (3 replicas each): lactic vs acetic, neutral vs anion",
                     x=0.01, ha="left", fontsize=12, color=an.TEXT)
        save(fig, "c2_md_long_hbond_dynamics.png", rect_bottom=0.16)

    # interactive HTML: convergence + pKa panel
    if not conv.empty:
        fig = make_subplots(rows=1, cols=2, subplot_titles=("ΔΔE_deprot lactic − acetic vs n (kcal/mol)",
                                                            "Δ assoc. energy per water lactic − acetic (kcal/mol)"))
        if "ddE_deprot_kcal" in conv:
            c = conv.dropna(subset=["ddE_deprot_kcal"])
            fig.add_trace(go.Scatter(x=c["n_water"], y=c["ddE_deprot_kcal"], error_y={"array": c["ddE_deprot_sd_kcal"]},
                                     mode="lines+markers", name="ΔΔE (± bootstrap SD)",
                                     line={"color": an.SERIES["lactic_acid"], "width": 2}), row=1, col=1)
            fig.add_hline(y=pka.get("experiment_ddG_kcal", -0.90 * RT_LN10), line_dash="dash", line_color=an.SERIES["acetic_acid"],
                          annotation_text="experiment ΔΔG", row=1, col=1)
        for col_, name, colr in (("d_assoc_per_water_neutral_kcal", "neutral", an.SERIES["lactic_acid"]),
                                 ("d_assoc_per_water_anion_kcal", "anion", an.SERIES["acetic_acid"])):
            if col_ in conv:
                c = conv.dropna(subset=[col_])
                fig.add_trace(go.Scatter(x=c["n_water"], y=c[col_], mode="lines+markers", name=name,
                                         line={"color": colr, "width": 2}), row=1, col=2)
        sub = (f"relative pKa estimate {pka['value']:+.2f} ± {pka['uncertainty']:.2f} (experiment {pka['experiment']:+.2f})"
               if pka.get("value") is not None else "relative pKa: thermochemistry pending")
        an._layout_common(fig, "Lactic − acetic differences vs cluster size (AIMNet2 via NVIDIA ALCHEMI)",
                          f"{sub} · {prov}")
        fig.update_layout(height=540)
        p = out_dir / "c2_convergence_vs_n.html"
        fig.write_html(p, include_plotlyjs=True, full_html=True, config={"displaylogo": False})
        paths.append(p)
    return paths


def explorer(base: Path, st: pd.DataFrame, prov: str, out: Path) -> Path | None:
    """Self-contained 3D viewer of the lowest-energy minimum of every campaign-2 species / n."""
    import plotly.graph_objects as go

    by_id = {}
    for d in (base / "results" / "json", base / "results_c2" / "json"):
        for pth in Path(d).glob("*.json"):
            by_id[pth.stem] = pth
    order = st.sort_values(["species", "n_water"])
    recs = []
    for r in order.itertuples():
        if r.species in ("water", "hydronium") and r.n_water <= 1:
            continue
        rec = json.loads(by_id[r.best_id].read_text())
        recs.append((r, rec))
    if not recs:
        return None
    fig = go.Figure()
    per, titles = 3, []
    for k, (r, rec) in enumerate(recs):
        for t in an._structure_traces(rec, visible=(k == 0)):
            fig.add_trace(t)
        n_hb = len([h for h in an.hydrogen_bonds(rec["symbols"], np.array(rec["optimized_positions_angstrom"]),
                                                 rec["fragments"]) if h["type"] != "intramolecular"])
        name = r.species.replace("+", " + ").replace("gln_gln", "Ac-Gln-Gln-NHMe").replace("_", " ")
        titles.append(f"{name} · {r.n_water} H₂O (lowest of {r.n_minima} minima, "
                      f"{'anneal-quench' if r.best_origin == 'anneal_quench' else 'seeded start'})"
                      f"<br><sup style='color:{an.TEXT2}'>E = {rec['energy_ev']:.4f} eV · charge {rec.get('charge', 0)} · "
                      f"{n_hb} intermolecular H-bonds · gap to 2nd minimum {r.gap_to_2nd_kcal:.2f} kcal/mol · {prov}</sup>")
    buttons = []
    for k, (r, rec) in enumerate(recs):
        vis = [False] * (per * len(recs))
        vis[per * k: per * k + per] = [True] * per
        buttons.append({"label": f"{r.species} n={r.n_water}", "method": "update",
                        "args": [{"visible": vis, "showlegend": [v and (i % per) != 0 for i, v in enumerate(vis)]},
                                 {"title.text": titles[k]}]})
    axis = {"showbackground": False, "showgrid": False, "zeroline": False, "showticklabels": False, "title": ""}
    fig.update_layout(title={"text": titles[0], "x": 0.01, "xanchor": "left", "font": {"size": 17, "color": an.TEXT}},
                      updatemenus=[{"buttons": buttons, "direction": "down", "x": 0.01, "xanchor": "left", "y": 1.0,
                                    "yanchor": "top", "bgcolor": "white", "bordercolor": an.GRID}],
                      scene={"xaxis": axis, "yaxis": axis, "zaxis": axis, "aspectmode": "data", "bgcolor": an.SURFACE},
                      paper_bgcolor=an.SURFACE, height=720, margin={"l": 0, "r": 0, "t": 110, "b": 0},
                      legend={"x": 0.99, "xanchor": "right", "y": 0.02, "font": {"color": an.TEXT2}})
    fig.write_html(out, include_plotlyjs=True, full_html=True, config={"displaylogo": False})
    return out


# ----------------------------------------------------------------------------- driver


def md_prov(base: Path) -> str:
    recs = [json.loads(p.read_text()) for p in sorted((Path(base) / "md_long").glob("*.json"))[:3]]
    return next((r["provenance"] for r in recs if r.get("provenance")), "real: ALCHEMI/AIMNet2")


def run(out_dir: Path, descriptors: dict, digest: dict, base: Path | None = None, figures_on: bool = True) -> dict:
    """Run the campaign-2 analysis if its data exist; extend descriptors/digest with new keys only."""
    base = Path(base) if base else ALCHEMI_DIR
    c2 = base / "results_c2" / "json"
    if not c2.exists():
        return {}
    pool = load_pool([base / "results" / "json", c2])
    st = species_table(pool)
    pool.to_csv(out_dir / "c2_minima_pool.csv", index=False)
    st.to_csv(out_dir / "c2_species_minima.csv", index=False)
    ns = sorted(set(st[st["species"] == "lactic_acid"]["n_water"]) & set(st[st["species"] == "acetic_acid_anion"]["n_water"]))
    deprots = [relative_deprotonation(st, pool, n) for n in ns]
    deprots = [d for d in deprots if d]
    vib = load_vib(base / "vib_c2") if (base / "vib_c2").exists() else pd.DataFrame()
    cycles = []
    if not vib.empty:
        vib.drop(columns=["fingerprint"]).to_csv(out_dir / "c2_vibrational_thermo.csv", index=False)
        cycles = [c for c in (thermo_cycle(vib, n) for n in sorted(vib["n_water"].unique())) if c]
    pka = relative_pka(cycles, deprots)
    assoc = association_by_n(st)
    assoc.to_csv(out_dir / "c2_association_by_n.csv", index=False)
    conv = lactic_minus_acetic_by_n(assoc, deprots)
    conv.to_csv(out_dir / "c2_lactic_minus_acetic_by_n.csv", index=False)
    pairs = pair_energetics(st)
    cos_names = sorted({s.split("+")[1] for s in st["species"] if "+" in s and s.split("+")[1] not in ("acetic_acid",)})
    cos = cosolute_energetics(st, cos_names)
    # acid-cosolute / acid-acid H-bonds in the best structures
    by_id = {}
    for d in (base / "results" / "json", c2):
        for p in Path(d).glob("*.json"):
            by_id[p.stem] = p
    for acid, blk in cos.items():
        for x, dct in blk.items():
            r = json.loads(by_id[dct["best_id"]].read_text())
            hb = acid_partner_hbonds(r)
            dct["acid_cosolute_hbonds"] = [{k: h[k] for k in ("type", "donor_site", "acceptor_site", "d_HA_angstrom",
                                                                 "d_DA_angstrom", "angle_DHA_deg")} for h in hb]
    for key in ("acetic_acid+acetic_acid", "lactic_acid+acetic_acid"):
        b = st[(st["species"] == key) & (st["n_water"] == 0)]
        if not b.empty:
            r = json.loads(by_id[b.iloc[0]["best_id"]].read_text())
            pairs[f"{key.replace('+', '_')}_best_id"] = r["id"]
            pairs[f"{key.replace('+', '_')}_hbonds"] = [{k: h[k] for k in ("donor_site", "acceptor_site", "d_HA_angstrom",
                                                                              "d_DA_angstrom")} for h in acid_partner_hbonds(r)]
    md_rows, curves = (analyse_md_long(base / "md_long") if (base / "md_long").exists() else (pd.DataFrame(), {}))
    if not md_rows.empty:
        md_rows.to_csv(out_dir / "c2_md_long_summary.csv", index=False)
        an.dump_json(out_dir / "c2_md_long_curves.json",
                     {"provenance": md_prov(base), "description": "per-run H-bond lifetime samples, intermittent "
                      "C(t), carboxyl-O/water-O pair distribution (20 ps NVT, 3 replicas)", "curves": curves}, indent=None)
    summary = {"relative_pka_estimate": pka, "relative_deprotonation_by_n": deprots, "thermo_cycles": cycles,
               "acid_pairs": pairs, "cosolutes": cos, "n_minima_pooled": int(pool["usable"].sum()),
               "n_minima_rejected": int((~pool["usable"]).sum()),
               "n_anneal_quench_minima": int((pool["origin"] == "anneal_quench").sum())}
    summary = {"provenance": an.provenance_label([json.loads(p.read_text()) for p in list(c2.glob("*.json"))[:5]]),
               **summary}
    an.dump_json(out_dir / "c2_summary.json", summary)

    # descriptors: new keys only
    descriptors["relative_pka_estimate"] = pka
    descriptors["acid_pairs"] = pairs
    for acid in ("lactic_acid", "acetic_acid"):
        d = descriptors.setdefault(acid, {})
        for sp, tag in ((acid, "neutral"), (ANION[acid], "anion")):
            a = assoc[assoc["species"] == sp]
            if not a.empty:
                d[f"c2_assoc_energy_per_water_kcal_by_n_{tag}"] = {int(r.n_water): float(r.dE_assoc_per_water_kcal) for r in a.itertuples()}
                d[f"c2_boltzmann_assoc_energy_per_water_kcal_by_n_{tag}"] = {int(r.n_water): float(r.dF_assoc_per_water_kcal) for r in a.itertuples()}
        d["c2_cosolute_interaction_kcal"] = cos.get(acid, {})
        if not md_rows.empty:
            sub = md_rows[md_rows["species"].isin([acid, ANION[acid]])]
            d["c2_md_long"] = {f"{r.species}__{r.temperature_k:.2f}K": {
                "mean_acid_water_hbonds": r.mean_acid_water_hbonds, "mean_continuous_lifetime_ps": r.mean_continuous_lifetime_ps,
                "intermittent_tau_ps": r.intermittent_tau_ps, "water_exchange_events_per_ps": r.water_exchange_events_per_ps,
                "proton_state_changes": r.proton_state_changes, "replicas": r.replicas, "length_ps": r.length_ps}
                for r in sub.itertuples()}
    descriptors["lactic_minus_acetic_c2_by_n"] = conv.to_dict("records")
    descriptors["provenance"].setdefault("campaign2", {})
    descriptors["provenance"]["campaign2"] = {
        "sources": ["data/alchemi/results_c2/json/", "data/alchemi/vib_c2/", "data/alchemi/md_long/"],
        "definitions": {
            "relative_pka_estimate": "see relative_pka_estimate.method; value = lactic - acetic pKa units",
            "c2_assoc_energy_per_water_kcal_by_n_*": "lowest pooled minimum (campaign 1 + 2), per water",
            "c2_boltzmann_assoc_energy_per_water_kcal_by_n_*": "Boltzmann conformational free energy (298 K) of the "
                                                               "pooled unique minima, per water",
            "c2_cosolute_interaction_kcal": "total = E(acid.X.3W) - E(acid) - E(X) - 3E(W); cosolute_to_hydrated_acid = "
                                            "E(acid.X.3W) - E(acid.3W) - E(X); acid_to_hydrated_cosolute = "
                                            "E(acid.X.3W) - E(X.3W) - E(acid); Gln-Gln = Ac-Gln-Gln-NHMe proxy for "
                                            "gluten amide contacts (hypothesis-level)",
            "c2_md_long": "20 ps NVT Langevin x 3 replicas; geometric acid(anion)-water H-bonds; continuous lifetimes "
                          "and Luzar-Chandler intermittent tau; exchange events of first-shell waters per ps",
        },
        "note": "existing campaign-1 keys are unchanged; campaign-2 values are under new keys"}
    digest["campaign2"] = {k: v for k, v in summary.items() if k != "thermo_cycles"}
    digest["files"].update({"c2_summary": "data/alchemi/analysis/c2_summary.json",
                            "c2_species_minima": "data/alchemi/analysis/c2_species_minima.csv",
                            "c2_association_by_n": "data/alchemi/analysis/c2_association_by_n.csv",
                            "c2_lactic_minus_acetic_by_n": "data/alchemi/analysis/c2_lactic_minus_acetic_by_n.csv"})
    if figures_on:
        prov = digest.get("provenance", "real: ALCHEMI/AIMNet2")
        for p in figures(out_dir, prov, pka, cycles, deprots, conv, assoc, pairs, cos, md_rows, curves):
            digest["files"][p.stem + "_" + p.suffix[1:]] = f"data/alchemi/analysis/{p.name}"
        ex = explorer(base, st, prov, out_dir / "c2_molecular_explorer.html")
        if ex is not None:
            digest["files"]["c2_molecular_explorer_html"] = f"data/alchemi/analysis/{ex.name}"
    return summary


# ----------------------------------------------------------------------------- job lists (Mac -> remote)

JOB_SPECIES = ("lactic_acid", "acetic_acid", "lactic_acid_anion", "acetic_acid_anion")


def top_unique_ids(pool: pd.DataFrame, species: str, n: int, k: int, window_kcal: float | None = None,
                   tol: float = 0.01) -> list[str]:
    g = pool[(pool["species"] == species) & (pool["n_water"] == n) & pool["usable"]].sort_values("energy_ev")
    out, last, e0 = [], None, None
    for r in g.itertuples():
        e = r.energy_ev * EV_TO_KCAL
        e0 = e if e0 is None else e0
        if window_kcal is not None and e - e0 > window_kcal:
            break
        if last is None or e - last > tol:
            out.append(r.id)
            last = e
        if len(out) >= k:
            break
    return out


def anneal_groups(pool: pd.DataFrame, ns=(3, 6), k: int = 5) -> dict[str, list[str]]:
    return {f"{sp}__n{n}": top_unique_ids(pool, sp, n, k) for sp in JOB_SPECIES for n in ns
            if top_unique_ids(pool, sp, n, k)}


def vib_ids(pool: pd.DataFrame, ns=(0, 3, 6, 10), k: int = 5, window_kcal: float = 3.0,
            extra: tuple[str, ...] = ("water__n1__s00", "hydronium__n0__s00")) -> list[str]:
    ids = [i for sp in JOB_SPECIES for n in ns for i in top_unique_ids(pool, sp, n, k, window_kcal)]
    return list(extra) + ids


def write_jobs(kind: str, base: Path | None = None, shards: int = 4, ns: tuple[int, ...] | None = None,
               tag: str = "") -> list[Path]:
    import yaml

    base = Path(base) if base else ALCHEMI_DIR
    cfg = yaml.safe_load((an.REPO_ROOT / "configs" / "molecular.yaml").read_text())["campaign2"]
    pool = load_pool([base / "results" / "json", base / "results_c2" / "json"])
    jd = base / "c2_jobs"
    jd.mkdir(parents=True, exist_ok=True)
    paths = []
    if kind == "anneal":
        groups = anneal_groups(pool, ns=ns or (3, 6), k=int(cfg["anneal"]["top_k_per_species"]))
        names = sorted(groups)
        for s in range(shards):
            sub = {g: groups[g] for g in names[s::shards]}
            p = jd / f"anneal{tag}_{s}.json"
            p.write_text(json.dumps(sub, indent=1))
            paths.append(p)
    elif kind == "vib":
        ids = vib_ids(pool, ns=ns or (0, 3, 6, 10), k=int(cfg["vib"]["top_k_per_species"]),
                      extra=("water__n1__s00", "hydronium__n0__s00") if (ns is None or 0 in ns) else ())
        for s in range(shards):
            p = jd / f"vib{tag}_{s}.json"
            p.write_text(json.dumps(ids[s::shards], indent=1))
            paths.append(p)
    return paths


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="campaign-2 job lists")
    ap.add_argument("kind", choices=["anneal", "vib"])
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--ns", type=int, nargs="*", default=None)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    for p in write_jobs(a.kind, shards=a.shards, ns=tuple(a.ns) if a.ns else None, tag=a.tag):
        print(p, len(json.loads(p.read_text())))
