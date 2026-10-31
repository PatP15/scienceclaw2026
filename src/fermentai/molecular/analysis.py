"""Mac-side analysis of the ALCHEMI / AIMNet2 results pulled back from Brev.

Inputs : data/alchemi/results/json/*.json   (one per optimised structure, written on the GPU box)
Outputs: data/alchemi/analysis/
    energy_summary.csv          every calculation: energy, fmax, convergence, iterations, relative energies
    association_energies.csv    dE_assoc = E(acid.nH2O) - E(acid) - n E(H2O), all conformers + best
    hbonds.csv                  every geometric hydrogen bond in every optimised structure
    hbond_summary.csv           counts per structure by type
    analysis_summary.json       compact machine-readable digest for the dashboard / agents
    association_energy.html     interactive Plotly chart (self-contained, no CDN)
    molecular_explorer.html     interactive 3D viewer of optimised structures (self-contained)
    association_energy.png, association_energy_per_water.png   static figures for slides

Definitions
-----------
* Reference energies are the *lowest* converged energy among the isolated-species conformers
  computed with the identical model/settings: E(acid) = min over acid monomer conformers,
  E(H2O) = water monomer, E((H2O)n) = min over pure water-cluster conformers.
* dE_assoc(n) = E(acid.(H2O)n) - E(acid) - n E(H2O)            (total binding of n waters)
* dE_acid-net(n) = E(acid.(H2O)n) - E(acid) - E((H2O)n)        (acid binding to a pre-formed water cluster)
  These are electronic (potential) energies at 0 K from AIMNet2 (wB97M-D3 surrogate); no zero-point,
  thermal, entropic or solvent-continuum corrections. They are *not* free energies, pKa, pH or taste.
* Hydrogen bond (geometric criterion, e.g. IUPAC 2011 recommendations / common MD practice):
  donor O-H covalent (d(O-H) <= 1.25 A), d(D...A) <= 3.5 A, d(H...A) <= 2.5 A, angle D-H...A >= 120 deg.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
ALCHEMI_DIR = REPO_ROOT / "data" / "alchemi"
EV_TO_KCAL = 23.060547830619
EV_TO_KJ = 96.48533212

HB_MAX_DA = 3.5
HB_MAX_HA = 2.5
HB_MIN_ANGLE = 120.0
HB_MAX_DH = 1.25

COV_RADII = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66}
CPK = {"H": "#e8e8e8", "C": "#505050", "N": "#3050f8", "O": "#e0201b"}
ATOM_SIZE = {"H": 7, "C": 13, "N": 13, "O": 13}
# Categorical slots (validated reference palette, light mode): 1 blue, 2 orange, 3 aqua.
SERIES = {"lactic_acid": "#2a78d6", "acetic_acid": "#eb6834", "water": "#1baf7a"}
LABEL = {"lactic_acid": "L-lactic acid", "acetic_acid": "acetic acid", "water": "water"}
ROLE = {"lactic_acid": "acid", "acetic_acid": "acid", "lactate": "anion", "acetate": "anion",
        "water": "water", "hydronium": "hydronium"}
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


# ----------------------------------------------------------------------------- loading


def load_results(results_dir: str | Path | None = None) -> list[dict[str, Any]]:
    d = Path(results_dir) if results_dir else ALCHEMI_DIR / "results" / "json"
    recs = []
    for p in sorted(d.glob("*.json")):
        recs.append(json.loads(p.read_text()))
    return recs


def provenance_label(recs: list[dict]) -> str:
    gpus = sorted({(r.get("versions") or {}).get("gpu") for r in recs if (r.get("versions") or {}).get("gpu")})
    return f"real: ALCHEMI/AIMNet2 on {', '.join(gpus) if gpus else 'GPU (unrecorded)'}"


MIN_PHYSICAL_DISTANCE = 0.70  # Angstrom, same guard as remote/run_alchemi.py


def min_interatomic_distance(xyz) -> float:
    x = np.asarray(xyz, dtype=float)
    if len(x) < 2:
        return math.inf
    d = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1) + np.eye(len(x)) * 1e9
    return float(d.min())


def usable(r: dict) -> bool:
    """Converged AND physically sensible final geometry (no atoms closer than 0.70 A)."""
    if not r.get("converged") or r.get("optimized_positions_angstrom") is None:
        return False
    return min_interatomic_distance(r["optimized_positions_angstrom"]) >= MIN_PHYSICAL_DISTANCE


def energy_table(recs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in recs:
        dmin = min_interatomic_distance(r["optimized_positions_angstrom"]) if r.get("optimized_positions_angstrom") else None
        status = r.get("status")
        if r.get("converged") and dmin is not None and dmin < MIN_PHYSICAL_DISTANCE:
            status = "failed_unphysical_geometry"
        rows.append({
            "id": r["id"], "kind": r.get("kind"), "acid": r.get("acid") or "", "n_water": r.get("n_water"),
            "conformer_index": r.get("conformer_index"), "formula": r.get("formula"), "n_atoms": r.get("n_atoms"),
            "status": status, "converged": usable(r), "min_interatomic_distance_angstrom": dmin,
            "charge": r.get("charge", 0), "energy_periodic_raw_ev": r.get("energy_periodic_raw_ev"),
            "crosscheck_delta_ev": r.get("crosscheck_delta_ev"),
            "energy_ev": r.get("energy_ev"), "energy_hartree": r.get("energy_hartree"),
            "max_force_ev_per_angstrom": r.get("max_force_ev_per_angstrom"),
            "iterations": r.get("iterations"), "wall_seconds": r.get("wall_seconds"),
            "initial_energy_ev": r.get("initial_energy_ev"),
            "optimizer": (r.get("method") or {}).get("optimizer"),
            "checkpoint": (r.get("method") or {}).get("checkpoint"),
            "seeds": json.dumps(r.get("seeds", {}), sort_keys=True),
            "provenance": r.get("provenance"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    grp = df.assign(_e=df["energy_ev"].where(df["converged"])).groupby(["kind", "acid", "n_water"])["_e"]
    df["group_min_energy_ev"] = grp.transform("min")
    df["relative_energy_kcal_mol"] = (df["energy_ev"] - df["group_min_energy_ev"]) * EV_TO_KCAL
    df["relative_energy_rank"] = grp.rank(method="first")
    return df.sort_values(["kind", "acid", "n_water", "energy_ev"]).reset_index(drop=True)


def references(df: pd.DataFrame) -> dict[str, Any]:
    ok = df[df["converged"]]
    ref: dict[str, Any] = {}
    w = ok[ok["kind"] == "water_monomer"]
    ref["water"] = None if w.empty else {"id": w.iloc[0]["id"], "energy_ev": float(w["energy_ev"].min())}
    for acid in sorted(ok.loc[ok["kind"] == "acid_monomer", "acid"].unique()):
        a = ok[(ok["kind"] == "acid_monomer") & (ok["acid"] == acid)].sort_values("energy_ev")
        ref[acid] = {"id": a.iloc[0]["id"], "energy_ev": float(a.iloc[0]["energy_ev"]), "n_conformers": len(a)}
    for n in sorted(ok.loc[ok["kind"] == "water_cluster", "n_water"].unique()):
        c = ok[(ok["kind"] == "water_cluster") & (ok["n_water"] == n)].sort_values("energy_ev")
        ref[f"water_cluster_{int(n)}"] = {"id": c.iloc[0]["id"], "energy_ev": float(c.iloc[0]["energy_ev"]),
                                          "n_conformers": len(c)}
    return ref


def association_table(df: pd.DataFrame, ref: dict) -> pd.DataFrame:
    rows = []
    cl = df[df["kind"] == "acid_water_cluster"]
    for _, r in cl.iterrows():
        acid, n = r["acid"], int(r["n_water"])
        row = {"id": r["id"], "acid": acid, "n_water": n, "conformer_index": r["conformer_index"],
               "converged": r["converged"], "energy_ev": r["energy_ev"]}
        if ref.get(acid) and ref.get("water") and r["converged"]:
            de = r["energy_ev"] - ref[acid]["energy_ev"] - n * ref["water"]["energy_ev"]
            row.update({"dE_assoc_ev": de, "dE_assoc_kcal_mol": de * EV_TO_KCAL, "dE_assoc_kj_mol": de * EV_TO_KJ,
                        "dE_assoc_per_water_kcal_mol": de * EV_TO_KCAL / n,
                        "acid_reference_id": ref[acid]["id"], "water_reference_id": ref["water"]["id"]})
            wc = ref.get(f"water_cluster_{n}") if n > 1 else ref["water"]
            if wc:
                dn = r["energy_ev"] - ref[acid]["energy_ev"] - wc["energy_ev"]
                row.update({"dE_acid_to_water_network_kcal_mol": dn * EV_TO_KCAL,
                            "water_network_reference_id": wc["id"]})
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    best = out[out["converged"]].groupby(["acid", "n_water"])["energy_ev"].transform("min")
    out["is_lowest_energy_conformer"] = out["converged"] & (out["energy_ev"] == best.reindex(out.index))
    return out.sort_values(["acid", "n_water", "energy_ev"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- geometry


def covalent_bonds(symbols: list[str], xyz: np.ndarray, fragments: list[dict] | None = None) -> list[tuple[int, int]]:
    frag_of = np.zeros(len(symbols), dtype=int)
    for k, f in enumerate(fragments or [{"start": 0, "stop": len(symbols)}]):
        frag_of[f["start"]:f["stop"]] = k
    bonds = []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            if frag_of[i] != frag_of[j]:
                continue
            lim = 1.2 * (COV_RADII.get(symbols[i], 0.75) + COV_RADII.get(symbols[j], 0.75))
            if np.linalg.norm(xyz[i] - xyz[j]) <= lim:
                bonds.append((i, j))
    return bonds


def _fragment_index(fragments: list[dict], n: int) -> tuple[np.ndarray, list[str]]:
    idx = np.zeros(n, dtype=int)
    names = []
    for k, f in enumerate(fragments):
        idx[f["start"]:f["stop"]] = k
        names.append(f["name"])
    return idx, names


def hydrogen_bonds(symbols: list[str], xyz: np.ndarray, fragments: list[dict]) -> list[dict]:
    """Geometric O-H...O hydrogen bonds (intra- and intermolecular)."""
    fidx, fnames = _fragment_index(fragments, len(symbols))
    out = []
    for h in range(len(symbols)):
        if symbols[h] != "H":
            continue
        # donor = nearest O/N (any fragment: protons may have moved between molecules)
        cands = [d for d in range(len(symbols)) if symbols[d] in ("O", "N")]
        if not cands:
            continue
        d = min(cands, key=lambda k: np.linalg.norm(xyz[k] - xyz[h]))
        dh = float(np.linalg.norm(xyz[d] - xyz[h]))
        if dh > HB_MAX_DH:
            continue
        for a in range(len(symbols)):
            if a == d or symbols[a] not in ("O", "N"):
                continue
            da = float(np.linalg.norm(xyz[a] - xyz[d]))
            ha = float(np.linalg.norm(xyz[a] - xyz[h]))
            if da > HB_MAX_DA or ha > HB_MAX_HA:
                continue
            v1, v2 = xyz[d] - xyz[h], xyz[a] - xyz[h]
            ang = float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1, 1))))
            if ang < HB_MIN_ANGLE:
                continue
            dn, an = fnames[fidx[d]], fnames[fidx[a]]
            kind = "intramolecular" if fidx[d] == fidx[a] else f"{ROLE.get(dn, dn)}->{ROLE.get(an, an)}"
            out.append({"donor": d, "hydrogen": h, "acceptor": a, "donor_fragment": int(fidx[d]),
                        "acceptor_fragment": int(fidx[a]), "donor_molecule": dn, "acceptor_molecule": an,
                        "type": kind, "d_DA_angstrom": da, "d_HA_angstrom": ha, "angle_DHA_deg": ang,
                        "acceptor_site": _acid_o_site(symbols, xyz, a, fidx) if ROLE.get(an) in ("acid", "anion") else f"{an}_O",
                        "donor_site": _acid_o_site(symbols, xyz, d, fidx) if ROLE.get(dn) in ("acid", "anion") else f"{dn}_O"})
    return out


def _acid_o_site(symbols, xyz, o, fidx) -> str:
    """Classify an acid oxygen: carbonyl O, carboxylic OH, or alpha-hydroxyl OH (lactic)."""
    same = [k for k in range(len(symbols)) if fidx[k] == fidx[o] and k != o]
    near_h = any(symbols[k] == "H" and k != o and np.linalg.norm(xyz[k] - xyz[o]) < 1.25 for k in range(len(symbols)))
    c_nb = [k for k in same if symbols[k] == "C" and np.linalg.norm(xyz[k] - xyz[o]) < 1.6]
    if not c_nb:
        return "O"
    c = c_nb[0]
    o_on_c = [k for k in same if symbols[k] == "O" and np.linalg.norm(xyz[k] - xyz[c]) < 1.6]
    if o_on_c:  # carboxyl carbon has two O neighbours
        return "carboxyl_OH" if near_h else "carbonyl_O"
    return "hydroxyl_OH" if near_h else "ether_O"


def hbond_tables(recs: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, summ = [], []
    for r in recs:
        if r.get("optimized_positions_angstrom") is None:
            continue
        xyz = np.array(r["optimized_positions_angstrom"])
        hbs = hydrogen_bonds(r["symbols"], xyz, r["fragments"])
        for hb in hbs:
            rows.append({"id": r["id"], "kind": r["kind"], "acid": r.get("acid") or "", "n_water": r["n_water"], **hb})
        types = pd.Series([h["type"] for h in hbs], dtype=object).value_counts().to_dict()
        summ.append({"id": r["id"], "kind": r["kind"], "acid": r.get("acid") or "", "n_water": r["n_water"],
                     "converged": usable(r), "n_hbonds": len(hbs),
                     "n_intermolecular": sum(1 for h in hbs if h["type"] != "intramolecular"),
                     **{f"n_{k.replace('->', '_to_')}": v for k, v in types.items()},
                     "mean_d_DA_angstrom": float(np.mean([h["d_DA_angstrom"] for h in hbs])) if hbs else math.nan,
                     "min_d_HA_angstrom": float(np.min([h["d_HA_angstrom"] for h in hbs])) if hbs else math.nan})
    return pd.DataFrame(rows), pd.DataFrame(summ).fillna({c: 0 for c in []})


# ----------------------------------------------------------------------------- charged species / acidity


def protonation_state(symbols: list[str], xyz: np.ndarray, fragments: list[dict]) -> dict[str, Any]:
    """Classify the final protonation state geometrically (each H assigned to its nearest O).

    Returns state in {"neutral_acid", "anion", "contact_ion_pair", "solvent_separated_ion_pair", "other"}.
    The carboxyl O atoms are the two O bonded (<1.6 A) to one C of the first fragment (acid/anion)."""
    xyz = np.asarray(xyz)
    o_idx = [i for i, s_ in enumerate(symbols) if s_ == "O"]
    h_on: dict[int, list[int]] = {o: [] for o in o_idx}
    for h in (i for i, s_ in enumerate(symbols) if s_ == "H"):
        d = [np.linalg.norm(xyz[h] - xyz[o]) for o in o_idx]
        k = int(np.argmin(d))
        if d[k] < 1.35:
            h_on[o_idx[k]].append(h)
    f0 = fragments[0]
    carboxyl_o: list[int] = []
    for c in range(f0["start"], f0["stop"]):
        if symbols[c] != "C":
            continue
        os_ = [o for o in range(f0["start"], f0["stop"]) if symbols[o] == "O" and np.linalg.norm(xyz[c] - xyz[o]) < 1.6]
        if len(os_) == 2:
            carboxyl_o = os_
    acid_protonated = any(h_on.get(o) for o in carboxyl_o)
    hydronium_o = [o for o, hs in h_on.items() if len(hs) == 3]
    hydroxide_o = [o for o, hs in h_on.items() if len(hs) == 1 and o not in range(f0["start"], f0["stop"])]
    out: dict[str, Any] = {"carboxyl_O": carboxyl_o, "hydronium_O": hydronium_o, "hydroxide_O": hydroxide_o,
                           "carboxyl_protonated": bool(acid_protonated)}
    if acid_protonated and not hydronium_o:
        out["state"] = "neutral_acid"
    elif not acid_protonated and len(hydronium_o) == 1:
        dmin = min(float(np.linalg.norm(xyz[hydronium_o[0]] - xyz[o])) for o in carboxyl_o)
        out["d_hydronium_carboxylate_O_angstrom"] = dmin
        out["state"] = "contact_ion_pair" if dmin <= 3.0 else "solvent_separated_ion_pair"
    elif not acid_protonated and not hydronium_o and not hydroxide_o:
        out["state"] = "anion"
    else:
        out["state"] = "other"
    return out


def _gap2(energies) -> float:
    e = np.sort(np.asarray(energies, dtype=float))
    return float((e[1] - e[0]) * EV_TO_KCAL) if len(e) > 1 else math.nan


def ionic_tables(recs: list[dict], df: pd.DataFrame, ref: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-structure protonation states for charged/ion-pair calculations, and per-(acid, n)
    deprotonation energies with consistent references (all gas-phase energies, eV -> kcal/mol)."""
    states = []
    for r in recs:
        if r.get("kind") not in ("anion_monomer", "anion_water_cluster", "ion_pair_cluster"):
            continue
        if r.get("optimized_positions_angstrom") is None:
            continue
        ps = protonation_state(r["symbols"], np.array(r["optimized_positions_angstrom"]), r["fragments"])
        start = (r.get("input_meta") or {}).get("species", "")
        states.append({"id": r["id"], "kind": r["kind"], "acid": r.get("acid"), "n_water": r.get("n_water"),
                       "conformer_index": r.get("conformer_index"), "charge": r.get("charge"),
                       "converged": usable(r), "energy_ev": r.get("energy_ev"),
                       "min_interatomic_distance_angstrom": min_interatomic_distance(r["optimized_positions_angstrom"]),
                       "starting_species": start, "final_state": ps["state"],
                       "d_hydronium_carboxylate_O_angstrom": ps.get("d_hydronium_carboxylate_O_angstrom"),
                       "derived_from": (r.get("input_meta") or {}).get("derived_from")})
    st = pd.DataFrame(states)
    rows = []
    ok = df[df["converged"]]
    h3o = ok[ok["kind"] == "hydronium"]
    e_h3o = float(h3o["energy_ev"].min()) if not h3o.empty else None
    e_w = ref.get("water", {}).get("energy_ev") if ref.get("water") else None
    for acid in ("lactic_acid", "acetic_acid"):
        for n in (0, 1, 3, 6):
            neu = ok[(ok["acid"] == acid) & (ok["n_water"] == n) &
                     (ok["kind"] == ("acid_monomer" if n == 0 else "acid_water_cluster"))]
            if neu.empty:
                continue
            e_neu = float(neu["energy_ev"].min())
            row = {"acid": acid, "n_water": n, "E_neutral_min_ev": e_neu, "neutral_id": neu.sort_values("energy_ev").iloc[0]["id"],
                   "n_neutral_starts": len(neu),
                   "neutral_gap_to_2nd_kcal_mol": _gap2(neu["energy_ev"])}
            if not st.empty:
                an = st[(st["acid"] == acid) & (st["n_water"] == n) & st["converged"] &
                        (st["kind"] != "ion_pair_cluster") & (st["final_state"] == "anion")]
                if not an.empty and e_h3o is not None and e_w is not None:
                    e_an = float(an["energy_ev"].min())
                    de = e_an + e_h3o - e_neu - e_w
                    row.update({"E_anion_min_ev": e_an, "anion_id": an.sort_values("energy_ev").iloc[0]["id"],
                                "n_anion_starts": len(an), "anion_gap_to_2nd_kcal_mol": _gap2(an["energy_ev"]),
                                "dE_deprot_separated_ions_kcal_mol": de * EV_TO_KCAL})
                ip = st[(st["acid"] == acid) & (st["n_water"] == n) & (st["kind"] == "ion_pair_cluster")]
                if not ip.empty:
                    surv = ip[ip["converged"] & ip["final_state"].isin(["contact_ion_pair", "solvent_separated_ion_pair"])]
                    row.update({"n_ion_pair_starts": len(ip), "n_ion_pairs_surviving": len(surv),
                                "n_ion_pairs_collapsed_to_neutral": int((ip["final_state"] == "neutral_acid").sum())})
                    if not surv.empty:
                        b = surv.sort_values("energy_ev").iloc[0]
                        row.update({"E_ion_pair_min_ev": float(b["energy_ev"]), "ion_pair_id": b["id"],
                                    "ion_pair_type": b["final_state"],
                                    "dE_deprot_ion_pair_kcal_mol": (float(b["energy_ev"]) - e_neu) * EV_TO_KCAL})
            rows.append(row)
    return st, pd.DataFrame(rows)


def molecular_descriptors(assoc: pd.DataFrame, hb: pd.DataFrame, deprot: pd.DataFrame, recs: list[dict],
                          prov: str) -> dict[str, Any]:
    """Machine-readable per-acid descriptors consumed by the fermentation model."""
    out: dict[str, Any] = {}
    gpu = next(((r.get("versions") or {}).get("gpu") for r in recs if (r.get("versions") or {}).get("gpu")), None)
    for acid in ("lactic_acid", "acetic_acid"):
        d: dict[str, Any] = {}
        best = assoc[(assoc["acid"] == acid) & assoc["is_lowest_energy_conformer"]] if not assoc.empty else assoc
        if not best.empty:
            d["assoc_energy_kcal_by_n"] = {int(r["n_water"]): float(r["dE_assoc_kcal_mol"]) for _, r in best.iterrows()}
            d["assoc_energy_per_water_kcal_by_n"] = {int(r["n_water"]): float(r["dE_assoc_per_water_kcal_mol"])
                                                     for _, r in best.iterrows()}
            d["assoc_energy_per_water_kcal"] = float(best["dE_assoc_per_water_kcal_mol"].mean())
            if "dE_acid_to_water_network_kcal_mol" in best:
                d["acid_to_water_network_kcal_by_n"] = {int(r["n_water"]): float(r["dE_acid_to_water_network_kcal_mol"])
                                                        for _, r in best.iterrows()
                                                        if not pd.isna(r.get("dE_acid_to_water_network_kcal_mol"))}
            ids = set(best["id"])
            h = hb[hb["id"].isin(ids) & hb["type"].isin(["acid->water", "water->acid"])] if not hb.empty else hb
            if not h.empty:
                k = h.sort_values("d_HA_angstrom").iloc[0]
                d["hbond_min_A"] = float(k["d_HA_angstrom"])
                d["hbond_min_OO_A"] = float(k["d_DA_angstrom"])
                d["hbond_min_type"] = f"{k['type']} ({k['donor_site']} -> {k['acceptor_site']})"
                d["hbond_min_structure"] = k["id"]
                d["acid_water_hbonds_by_n"] = {int(n): int((h["n_water"] == n).sum()) for n in sorted(h["n_water"].unique())}
        if not deprot.empty:
            dp = deprot[deprot["acid"] == acid]
            for _, r in dp.iterrows():
                n = int(r["n_water"])
                if not pd.isna(r.get("dE_deprot_ion_pair_kcal_mol", float("nan"))):
                    d[f"deprot_energy_n{n}_kcal"] = float(r["dE_deprot_ion_pair_kcal_mol"])
                    d[f"deprot_ion_pair_type_n{n}"] = r.get("ion_pair_type")
                elif n > 0 and not pd.isna(r.get("n_ion_pair_starts", float("nan"))):
                    d[f"deprot_energy_n{n}_kcal"] = None
                    d[f"deprot_ion_pair_type_n{n}"] = (f"no stable ion pair: {int(r['n_ion_pairs_collapsed_to_neutral'])}/"
                                                       f"{int(r['n_ion_pair_starts'])} starts relaxed back to the neutral acid")
                if not pd.isna(r.get("dE_deprot_separated_ions_kcal_mol", float("nan"))):
                    d[f"deprot_energy_separated_ions_n{n}_kcal"] = float(r["dE_deprot_separated_ions_kcal_mol"])
        out[acid] = d
    la, ac = out.get("lactic_acid", {}), out.get("acetic_acid", {})
    diff = {}
    for k, v in la.items():
        if isinstance(v, (int, float)) and isinstance(ac.get(k), (int, float)) and not isinstance(v, bool):
            diff[k] = float(v) - float(ac[k])
    out["lactic_minus_acetic"] = diff
    out["provenance"] = {
        "label": prov,
        "method": "AIMNet2 via nvalchemi-toolkit (PipelineModelWrapper: AIMNet2 + Ewald + DFT-D3(BJ)), FIRE2 optimisation",
        "gpu": gpu,
        "checkpoint": next(((r.get("method") or {}).get("checkpoint") for r in recs if r.get("method")), None),
        "versions": next((r.get("versions") for r in recs if r.get("versions")), None),
        "note": "cluster-phase (gas-phase microsolvated) 0 K electronic energetics; a relative acidity/solvation "
                "signal, not a solution-phase pKa, pH, or sourness prediction",
        "sampling_caveat": "6 seeded starts per (species, n) and local FIRE2 minimisation; global minima are not "
                           "guaranteed. Differences of a few kcal/mol at n=6 are comparable to the gap between the "
                           "lowest and second-lowest starts (see deprotonation_energies.csv *_gap_to_2nd_kcal_mol).",
        "definitions": {
            "assoc_energy_per_water_kcal": "mean over n in {1,3,6} of [E(acid.nH2O) - E(acid) - n E(H2O)]/n, lowest-energy conformers",
            "deprot_energy_n{n}_kcal": "E(anion.H3O+.(n-1)H2O) - E(acid.nH2O), matched composition, lowest-energy "
                                       "structures that remained ion pairs after optimisation (null if none did)",
            "deprot_energy_separated_ions_n{n}_kcal": "E(anion.nH2O) + E(H3O+) - E(acid.nH2O) - E(H2O); charged energies "
                                                      "are gas-phase (periodic Ewald minus Makov-Payne monopole term)",
            "hbond_min_A": "shortest intermolecular acid/water H...O distance in the lowest-energy neutral clusters",
            "lactic_minus_acetic": "lactic value minus acetic value for every numeric field present for both",
        },
        "source_files": ["data/alchemi/results/json/", "data/alchemi/analysis/association_energies.csv",
                         "data/alchemi/analysis/deprotonation_energies.csv", "data/alchemi/analysis/hbonds.csv"],
    }
    return out


# ----------------------------------------------------------------------------- figures


def _layout_common(fig, title: str, subtitle: str):
    fig.update_layout(
        title={"text": f"{title}<br><sup style='color:{TEXT2}'>{subtitle}</sup>", "x": 0.01, "xanchor": "left",
               "font": {"size": 20, "color": TEXT}},
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font={"family": "Helvetica, Arial, sans-serif", "color": TEXT},
        legend={"orientation": "h", "y": -0.18, "x": 0.0, "font": {"color": TEXT2}},
        hoverlabel={"bgcolor": "white", "font": {"color": TEXT}},
        margin={"l": 70, "r": 30, "t": 90, "b": 90},
    )


def association_figure(assoc: pd.DataFrame, prov: str):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12, subplot_titles=(
        "Total association energy ΔE<sub>assoc</sub>", "Per water molecule ΔE<sub>assoc</sub>/n"))
    ok = assoc[assoc["converged"]] if not assoc.empty else assoc
    for acid in ("lactic_acid", "acetic_acid"):
        a = ok[ok["acid"] == acid]
        if a.empty:
            continue
        best = a[a["is_lowest_energy_conformer"]].sort_values("n_water")
        rng = np.random.default_rng(0)
        jit = rng.uniform(-0.12, 0.12, len(a)) + (-0.06 if acid == "lactic_acid" else 0.06)
        for col, ycol in ((1, "dE_assoc_kcal_mol"), (2, "dE_assoc_per_water_kcal_mol")):
            fig.add_trace(go.Scatter(
                x=a["n_water"] + jit, y=a[ycol], mode="markers", name=f"{LABEL[acid]} — all conformers",
                legendgroup=acid + "c", showlegend=col == 1,
                marker={"size": 8, "color": SERIES[acid], "opacity": 0.35, "line": {"width": 1, "color": SURFACE}},
                customdata=np.stack([a["id"], a["energy_ev"]], axis=-1),
                hovertemplate="%{customdata[0]}<br>ΔE = %{y:.2f} kcal/mol<br>E = %{customdata[1]:.5f} eV<extra></extra>"),
                row=1, col=col)
            fig.add_trace(go.Scatter(
                x=best["n_water"], y=best[ycol], mode="lines+markers+text", name=f"{LABEL[acid]} — lowest energy",
                legendgroup=acid, showlegend=col == 1, line={"width": 2, "color": SERIES[acid]},
                marker={"size": 10, "color": SERIES[acid], "line": {"width": 2, "color": SURFACE}},
                text=[f"{v:.1f}" for v in best[ycol]], textposition="top right" if acid == "acetic_acid" else "bottom left",
                textfont={"color": TEXT2, "size": 11},
                customdata=best["id"], hovertemplate="%{customdata}<br>ΔE = %{y:.2f} kcal/mol<extra></extra>"),
                row=1, col=col)
    for col in (1, 2):
        fig.update_xaxes(title_text="number of water molecules n", tickvals=[1, 3, 6], range=[0.4, 6.6],
                         gridcolor=GRID, zeroline=False, row=1, col=col)
        fig.update_yaxes(title_text="kcal/mol (more negative = stronger binding)", gridcolor=GRID,
                         zeroline=True, zerolinecolor="#c3c2b7", row=1, col=col)
    _layout_common(fig, "Acid–water cluster association energies (AIMNet2, 0 K electronic)",
                   f"ΔE = E(acid·nH₂O) − E(acid) − n·E(H₂O), lowest-energy isolated references · {prov}")
    fig.update_layout(height=560)
    return fig


def _structure_traces(rec: dict, visible: bool):
    import plotly.graph_objects as go

    syms = rec["symbols"]
    xyz = np.array(rec["optimized_positions_angstrom"])
    xyz = xyz - xyz.mean(axis=0)
    q = rec.get("charges_e") or [None] * len(syms)
    fidx, fnames = _fragment_index(rec["fragments"], len(syms))
    traces = []
    bx, by, bz = [], [], []
    for i, j in covalent_bonds(syms, xyz, rec["fragments"]):
        bx += [xyz[i, 0], xyz[j, 0], None]
        by += [xyz[i, 1], xyz[j, 1], None]
        bz += [xyz[i, 2], xyz[j, 2], None]
    traces.append(go.Scatter3d(x=bx, y=by, z=bz, mode="lines", line={"color": "#8a8984", "width": 6},
                               hoverinfo="skip", showlegend=False, visible=visible, name="bonds"))
    hx, hy, hz = [], [], []
    for hb in hydrogen_bonds(syms, xyz, rec["fragments"]):
        h, a = hb["hydrogen"], hb["acceptor"]
        hx += [xyz[h, 0], xyz[a, 0], None]
        hy += [xyz[h, 1], xyz[a, 1], None]
        hz += [xyz[h, 2], xyz[a, 2], None]
    traces.append(go.Scatter3d(x=hx, y=hy, z=hz, mode="lines", line={"color": "#1baf7a", "width": 4, "dash": "dash"},
                               hoverinfo="skip", name="hydrogen bond (H···O)", visible=visible, showlegend=visible))
    hover = [f"{s}{k} · {fnames[fidx[k]].replace('_', ' ')}<br>AIMNet2 charge {q[k]:+.3f} e" if q[k] is not None
             else f"{s}{k}" for k, s in enumerate(syms)]
    traces.append(go.Scatter3d(
        x=xyz[:, 0], y=xyz[:, 1], z=xyz[:, 2], mode="markers", text=hover, hovertemplate="%{text}<extra></extra>",
        marker={"size": [ATOM_SIZE.get(s, 10) for s in syms], "color": [CPK.get(s, "#ff00ff") for s in syms],
                "line": {"width": 1, "color": "#3a3a38"}, "opacity": 1.0},
        name="atoms (CPK: C grey, O red, H white)", visible=visible, showlegend=visible))
    return traces


def explorer_figure(recs: list[dict], assoc: pd.DataFrame, prov: str):
    import plotly.graph_objects as go

    order = []
    by_id = {r["id"]: r for r in recs if r.get("optimized_positions_angstrom") is not None}
    for acid in ("lactic_acid", "acetic_acid"):
        mono = sorted([r for r in by_id.values() if r["kind"] == "acid_monomer" and r["acid"] == acid],
                      key=lambda r: r["energy_ev"] if r.get("energy_ev") is not None else 1e9)
        if mono:
            order.append(mono[0]["id"])
        for n in (1, 3, 6):
            cl = sorted([r for r in by_id.values() if r["kind"] == "acid_water_cluster" and r["acid"] == acid
                         and r["n_water"] == n], key=lambda r: r["energy_ev"] if r.get("energy_ev") is not None else 1e9)
            order += [r["id"] for r in cl]
        # charged species: lowest-energy converged structure per (kind, n)
        for kind in ("anion_monomer", "anion_water_cluster", "ion_pair_cluster"):
            for n in (0, 1, 3, 6):
                cand = sorted([r for r in by_id.values() if r["kind"] == kind and r["acid"] == acid and r["n_water"] == n
                               and usable(r)], key=lambda r: r["energy_ev"])
                if cand:
                    order.append(cand[0]["id"])
    order += sorted([k for k, r in by_id.items() if r["kind"] in ("water_monomer", "water_cluster", "hydronium")])
    assoc_by_id = {} if assoc.empty else assoc.set_index("id").to_dict("index")
    fig = go.Figure()
    per = 3
    titles = []
    for k, sid in enumerate(order):
        r = by_id[sid]
        for t in _structure_traces(r, visible=(k == 0)):
            fig.add_trace(t)
        a = assoc_by_id.get(sid, {})
        extra = f" · ΔE<sub>assoc</sub> = {a['dE_assoc_kcal_mol']:.1f} kcal/mol" if a.get("dE_assoc_kcal_mol") is not None and not (isinstance(a.get('dE_assoc_kcal_mol'), float) and math.isnan(a['dE_assoc_kcal_mol'])) else ""
        best = " (lowest-energy conformer)" if a.get("is_lowest_energy_conformer") else ""
        n_hb = len(hydrogen_bonds(r["symbols"], np.array(r["optimized_positions_angstrom"]), r["fragments"]))
        titles.append(
            f"{_pretty(r)}{best}<br><sup style='color:{TEXT2}'>E = {r['energy_ev']:.4f} eV · max|F| = "
            f"{r['max_force_ev_per_angstrom']:.3f} eV/Å · {r['status']} in {r['iterations']} steps · "
            f"{n_hb} H-bonds{extra} · {prov}</sup>")
    buttons = []
    for k, sid in enumerate(order):
        vis = [False] * (per * len(order))
        vis[per * k: per * k + per] = [True] * per
        buttons.append({"label": sid.replace("__", " ").replace("_", " "), "method": "update",
                        "args": [{"visible": vis, "showlegend": [v and (i % per) != 0 for i, v in enumerate(vis)]},
                                 {"title.text": titles[k]}]})
    axis = {"showbackground": False, "showgrid": False, "zeroline": False, "showticklabels": False, "title": ""}
    fig.update_layout(
        title={"text": titles[0] if titles else "no results", "x": 0.01, "xanchor": "left", "font": {"size": 18, "color": TEXT}},
        updatemenus=[{"buttons": buttons, "direction": "down", "x": 0.01, "xanchor": "left", "y": 1.0, "yanchor": "top",
                      "bgcolor": "white", "bordercolor": GRID, "font": {"size": 12}}],
        scene={"xaxis": axis, "yaxis": axis, "zaxis": axis, "aspectmode": "data", "bgcolor": SURFACE},
        paper_bgcolor=SURFACE, height=720, margin={"l": 0, "r": 0, "t": 110, "b": 0},
        legend={"x": 0.99, "xanchor": "right", "y": 0.02, "font": {"color": TEXT2}},
        annotations=[{"text": "Pick a structure ▾ · drag to rotate · scroll to zoom · dashed green = H···O hydrogen bond "
                      "(d(H···O) ≤ 2.5 Å, ∠O–H···O ≥ 120°)", "showarrow": False, "x": 0.01, "y": -0.0,
                      "xref": "paper", "yref": "paper", "xanchor": "left", "font": {"size": 12, "color": TEXT2}}],
    )
    return fig


def _pretty(r: dict) -> str:
    if r["kind"] == "acid_water_cluster":
        return f"{LABEL[r['acid']]} · {r['n_water']} H₂O (conformer {r['conformer_index']})"
    if r["kind"] == "acid_monomer":
        return f"{LABEL[r['acid']]} (isolated, conformer {r['conformer_index']})"
    anion = {"lactic_acid": "lactate", "acetic_acid": "acetate"}.get(r.get("acid"), "anion")
    if r["kind"] == "anion_monomer":
        return f"{anion}⁻ (isolated, from conformer {r['conformer_index']})"
    if r["kind"] == "anion_water_cluster":
        return f"{anion}⁻ · {r['n_water']} H₂O (from conformer {r['conformer_index']})"
    if r["kind"] == "ion_pair_cluster":
        ps = protonation_state(r["symbols"], np.array(r["optimized_positions_angstrom"]), r["fragments"])["state"]
        return (f"{anion}⁻ · H₃O⁺ · {r['n_water'] - 1} H₂O start → final: {ps.replace('_', ' ')} "
                f"(from conformer {r['conformer_index']})")
    if r["kind"] == "hydronium":
        return "hydronium H₃O⁺"
    if r["kind"] == "water_cluster":
        return f"(H₂O)<sub>{r['n_water']}</sub> cluster (conformer {r['conformer_index']})"
    return "water monomer"


def static_figures(assoc: pd.DataFrame, prov: str, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    ok = assoc[assoc["converged"]]
    for ycol, fname, ttl in (("dE_assoc_kcal_mol", "association_energy.png", "Acid–water association energy"),
                             ("dE_assoc_per_water_kcal_mol", "association_energy_per_water.png",
                              "Association energy per water molecule")):
        fig, ax = plt.subplots(figsize=(8, 6.0), dpi=160)
        fig.patch.set_facecolor(SURFACE)
        ax.set_facecolor(SURFACE)
        for acid, dx in (("lactic_acid", -0.08), ("acetic_acid", 0.08)):
            a = ok[ok["acid"] == acid]
            if a.empty:
                continue
            ax.scatter(a["n_water"] + dx, a[ycol], s=28, color=SERIES[acid], alpha=0.35, edgecolor="none",
                       label=f"{LABEL[acid]} — all conformers")
            b = a[a["is_lowest_energy_conformer"]].sort_values("n_water")
            ax.plot(b["n_water"] + dx, b[ycol], "-o", color=SERIES[acid], lw=2, ms=7, mec=SURFACE, mew=1.5,
                    label=f"{LABEL[acid]} — lowest energy")
            for x, y in zip(b["n_water"] + dx, b[ycol]):
                left = acid == "lactic_acid"
                ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points", xytext=(-9, -13) if left else (9, 6),
                            ha="right" if left else "left", fontsize=9, color=TEXT2)
        ax.axhline(0, color="#c3c2b7", lw=1)
        ax.set_xticks([1, 3, 6])
        ax.set_xlabel("number of water molecules n", color=TEXT)
        ax.set_ylabel("ΔE (kcal/mol); more negative = stronger binding", color=TEXT)
        ax.set_title(f"{ttl}\nAIMNet2 (wB97M-D3) via NVIDIA ALCHEMI, 0 K electronic energies", fontsize=12, color=TEXT,
                     loc="left")
        ax.grid(axis="y", color=GRID, lw=0.8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_xlim(0.3, 6.7)
        ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2)
        fig.text(0.01, 0.01, prov + " · ΔE = E(acid·nH₂O) − E(acid) − n·E(H₂O); no ZPE/thermal/solvent corrections",
                 fontsize=7.5, color=TEXT2)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        p = out_dir / fname
        fig.savefig(p, facecolor=SURFACE)
        plt.close(fig)
        paths.append(p)
    return paths


def deprotonation_figures(deprot: pd.DataFrame, prov: str, out_dir: Path) -> list[Path]:
    """Left: separated-ion ΔE_deprot vs n. Right: outcome of the matched-composition ion-pair starts
    (how many stayed ion pairs after optimisation; ΔE_IP labelled where one survived)."""
    import matplotlib
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    col = "dE_deprot_separated_ions_kcal_mol"
    if col not in deprot or not deprot[col].notna().any():
        return []
    ip = deprot[deprot.get("n_ion_pair_starts", pd.Series(dtype=float)).notna()] if "n_ion_pair_starts" in deprot else deprot.iloc[0:0]
    t_left = "Separated ions: E(A⁻·nH₂O) + E(H₃O⁺) − E(HA·nH₂O) − E(H₂O)"
    t_right = "Ion pair A⁻·H₃O⁺·(n−1)H₂O (same composition): starts that stayed ion pairs"
    paths = []

    fig = make_subplots(rows=1, cols=2, subplot_titles=[t_left, t_right], horizontal_spacing=0.12)
    for acid in ("lactic_acid", "acetic_acid"):
        g = deprot[(deprot["acid"] == acid) & deprot[col].notna()].sort_values("n_water")
        fig.add_trace(go.Scatter(x=g["n_water"], y=g[col], mode="lines+markers+text", name=LABEL[acid], legendgroup=acid,
                                 line={"width": 2, "color": SERIES[acid]},
                                 marker={"size": 10, "color": SERIES[acid], "line": {"width": 2, "color": SURFACE}},
                                 text=[f"{v:.1f}" for v in g[col]],
                                 textposition="bottom right" if acid == "lactic_acid" else "top right",
                                 textfont={"color": TEXT2, "size": 11},
                                 hovertemplate=LABEL[acid] + "<br>n = %{x}<br>ΔE = %{y:.1f} kcal/mol<extra></extra>"),
                      row=1, col=1)
        h = ip[ip["acid"] == acid].sort_values("n_water")
        if not h.empty:
            frac = h["n_ion_pairs_surviving"] / h["n_ion_pair_starts"]
            labels = [f"{int(a)}/{int(b)}" + (f"<br>ΔE<sub>IP</sub> = {e:+.1f}" if not pd.isna(e) else "")
                      for a, b, e in zip(h["n_ion_pairs_surviving"], h["n_ion_pair_starts"],
                                         h.get("dE_deprot_ion_pair_kcal_mol", pd.Series([np.nan] * len(h))))]
            fig.add_trace(go.Bar(x=[str(int(n)) for n in h["n_water"]], y=frac, name=LABEL[acid], legendgroup=acid,
                                 showlegend=False, marker={"color": SERIES[acid]}, text=labels, textposition="outside",
                                 textfont={"color": TEXT2, "size": 11},
                                 hovertemplate=LABEL[acid] + "<br>n = %{x}<br>fraction surviving = %{y:.2f}<extra></extra>"),
                          row=1, col=2)
    fig.update_xaxes(title_text="number of water molecules n", tickvals=[0, 1, 3, 6], gridcolor=GRID, row=1, col=1)
    fig.update_yaxes(title_text="ΔE<sub>deprot</sub> (kcal/mol); lower = easier proton release", gridcolor=GRID, row=1, col=1)
    fig.update_xaxes(title_text="number of water molecules n", type="category", row=1, col=2)
    fig.update_yaxes(title_text="fraction of starts still ion pairs after optimisation", range=[0, 1.05],
                     gridcolor=GRID, row=1, col=2)
    _layout_common(fig, "Cluster-phase deprotonation (AIMNet2 via NVIDIA ALCHEMI)",
                   f"0 K electronic energies of microsolvated clusters, a relative acidity signal, not a pKa · {prov}")
    fig.update_layout(height=580, barmode="group", bargap=0.35, bargroupgap=0.08)
    fig.update_annotations(font={"size": 12, "color": TEXT2})
    fig.write_html(out_dir / "deprotonation_energy.html", include_plotlyjs=True, full_html=True,
                   config={"displaylogo": False})
    paths.append(out_dir / "deprotonation_energy.html")

    mfig, (ax, bx) = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
    mfig.patch.set_facecolor(SURFACE)
    for a_ in (ax, bx):
        a_.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        a_.grid(axis="y", color=GRID, lw=0.8)
    width = 0.36
    ns = sorted(ip["n_water"].unique()) if not ip.empty else []
    for k, acid in enumerate(("lactic_acid", "acetic_acid")):
        g = deprot[(deprot["acid"] == acid) & deprot[col].notna()].sort_values("n_water")
        ax.plot(g["n_water"], g[col], "-o", color=SERIES[acid], lw=2, ms=7, mec=SURFACE, mew=1.5, label=LABEL[acid])
        for x, y in zip(g["n_water"], g[col]):
            ax.annotate(f"{y:.1f}", (x, y), textcoords="offset points",
                        xytext=(8, -13) if acid == "lactic_acid" else (8, 6), fontsize=9, color=TEXT2)
        h = ip[ip["acid"] == acid].set_index("n_water")
        xs = np.arange(len(ns)) + (k - 0.5) * width
        for x, n in zip(xs, ns):
            if n not in h.index:
                continue
            surv, tot = h.loc[n, "n_ion_pairs_surviving"], h.loc[n, "n_ion_pair_starts"]
            e = h.loc[n, "dE_deprot_ion_pair_kcal_mol"] if "dE_deprot_ion_pair_kcal_mol" in h else np.nan
            bx.bar(x, surv / tot, width=width - 0.04, color=SERIES[acid], label=LABEL[acid] if n == ns[0] else None)
            bx.annotate(f"{int(surv)}/{int(tot)}" + ("" if pd.isna(e) else f"\nΔE_IP = {e:+.1f}"), (x, surv / tot),
                        textcoords="offset points", xytext=(0, 4), ha="center", fontsize=8.5, color=TEXT2)
    ax.set_xticks([0, 1, 3, 6])
    ax.set_xlim(-0.4, 6.9)
    ax.set_xlabel("number of water molecules n", color=TEXT)
    ax.set_ylabel("ΔE_deprot (kcal/mol); lower = easier proton release", color=TEXT)
    ax.set_title(t_left, fontsize=9.5, color=TEXT, loc="left")
    bx.set_xticks(np.arange(len(ns)))
    bx.set_xticklabels([str(int(n)) for n in ns])
    bx.set_ylim(0, 1.05)
    bx.set_xlabel("number of water molecules n", color=TEXT)
    bx.set_ylabel("fraction of starts still ion pairs after optimisation", color=TEXT)
    bx.set_title(t_right, fontsize=9.5, color=TEXT, loc="left")
    h_, l_ = ax.get_legend_handles_labels()
    mfig.legend(h_, l_, frameon=False, fontsize=9, loc="lower center", bbox_to_anchor=(0.5, 0.035), ncol=2)
    mfig.suptitle("Cluster-phase deprotonation: AIMNet2 (wB97M-D3) via NVIDIA ALCHEMI", x=0.01, ha="left",
                  fontsize=12, color=TEXT)
    mfig.text(0.01, 0.01, prov + " · 0 K electronic energies of gas-phase clusters (kcal/mol); relative acidity "
              "signal, not a pKa · 6 seeded starts per species; ion pairs 6–12 starts (contact + solvent-separated)", fontsize=7.5, color=TEXT2)
    mfig.tight_layout(rect=(0, 0.09, 1, 0.95))
    p = out_dir / "deprotonation_energy.png"
    mfig.savefig(p, facecolor=SURFACE)
    plt.close(mfig)
    paths.append(p)
    return paths


# ----------------------------------------------------------------------------- MD (optional P6)


def read_frames(path: Path) -> list[tuple[int, list[str], np.ndarray]]:
    lines = Path(path).read_text().splitlines()
    out, i = [], 0
    while i < len(lines):
        n = int(lines[i].split()[0])
        step = int(next((t.split("=")[1] for t in lines[i + 1].split() if t.startswith("step=")), "0"))
        syms, xyz = [], []
        for ln in lines[i + 2 : i + 2 + n]:
            p = ln.split()
            syms.append(p[0])
            xyz.append([float(v) for v in p[1:4]])
        out.append((step, syms, np.array(xyz)))
        i += 2 + n
    return out


def _min_heavy_distance_to_rest(xyz, syms, fidx, k) -> float:
    mine = [i for i in range(len(syms)) if fidx[i] == k and syms[i] != "H"]
    other = [i for i in range(len(syms)) if fidx[i] != k and syms[i] != "H"]
    if not mine or not other:
        return math.nan
    return float(np.min(np.linalg.norm(xyz[mine][:, None, :] - xyz[other][None, :, :], axis=-1)))


def md_tables(md_dir: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-run summary + per-frame H-bond time series for the NVT Langevin runs."""
    md_dir = Path(md_dir) if md_dir else ALCHEMI_DIR / "md"
    runs, series = [], []
    for jp in sorted(md_dir.glob("*.json")):
        rec = json.loads(jp.read_text())
        row = {k: rec.get(k) for k in ("id", "start_structure", "temperature_k", "seed", "n_steps", "timestep_fs",
                                       "friction_per_fs", "status", "wall_seconds", "ns_per_day",
                                       "mean_temperature_second_half_k", "std_temperature_second_half_k")}
        fp = md_dir / f"{rec['id']}_frames.xyz"
        if rec.get("status") == "completed" and fp.exists():
            frags = rec["fragments"]
            fidx, _ = _fragment_index(frags, len(rec["symbols"]))
            frames = read_frames(fp)
            n_inter, n_acid_w, detached = [], [], []
            for step, syms, xyz in frames:
                hbs = [h for h in hydrogen_bonds(syms, xyz, frags) if h["type"] != "intramolecular"]
                n_inter.append(len(hbs))
                n_acid_w.append(sum(1 for h in hbs if h["type"] in ("acid->water", "water->acid", "water->anion",
                                                                     "anion->water")))
                det = sum(1 for k in range(len(frags)) if _min_heavy_distance_to_rest(xyz, syms, fidx, k) > 4.5)
                detached.append(det)
                series.append({"id": rec["id"], "start_structure": rec["start_structure"],
                               "temperature_k": rec["temperature_k"], "step": step,
                               "time_ps": step * rec["timestep_fs"] / 1000.0, "n_intermolecular_hbonds": len(hbs),
                               "n_acid_water_hbonds": n_acid_w[-1], "n_detached_molecules": det})
            half = len(frames) // 2
            row.update({"n_frames": len(frames),
                        "mean_intermolecular_hbonds_second_half": float(np.mean(n_inter[half:])) if frames else None,
                        "mean_acid_water_hbonds_second_half": float(np.mean(n_acid_w[half:])) if frames else None,
                        "max_detached_molecules": int(max(detached)) if frames else None,
                        "final_detached_molecules": int(detached[-1]) if frames else None})
            cp = md_dir / f"{rec['id']}.csv"
            if cp.exists():
                c = pd.read_csv(cp)
                if "energy" in c:
                    e = c["energy"].to_numpy()
                    row["mean_potential_energy_second_half_ev"] = float(np.mean(e[len(e) // 2:]))
        runs.append(row)
    return pd.DataFrame(runs), pd.DataFrame(series)


def _md_agg(runs: pd.DataFrame) -> pd.DataFrame:
    ok = runs[runs["status"] == "completed"].copy()
    if ok.empty:
        return ok
    head = ok["start_structure"].str.split("__").str[0]
    ok["anion"] = head.str.endswith("_anion")
    ok["acid"] = head.str.replace("_anion", "", regex=False)
    ok["n_water"] = ok["start_structure"].str.extract(r"__n(\d+)__")[0].astype(int)
    agg = (ok.groupby(["acid", "anion", "n_water", "start_structure", "temperature_k"])
           .agg(tot=("mean_intermolecular_hbonds_second_half", "mean"),
                tot_sd=("mean_intermolecular_hbonds_second_half", "std"),
                aw=("mean_acid_water_hbonds_second_half", "mean"),
                aw_sd=("mean_acid_water_hbonds_second_half", "std"),
                n_seeds=("mean_intermolecular_hbonds_second_half", "size"),
                t_meas=("mean_temperature_second_half_k", "mean")).reset_index())
    for c in ("tot", "aw"):
        agg[f"{c}_sem"] = (agg[f"{c}_sd"] / np.sqrt(agg["n_seeds"])).fillna(0.0)
    return agg


_ANION_LABEL = {"lactic_acid": "lactate⁻", "acetic_acid": "acetate⁻"}


def _md_series_style(acid: str, is_anion: bool, n: int):
    name = f"{_ANION_LABEL.get(acid, acid)} · {n} H₂O" if is_anion else f"{LABEL.get(acid, acid)} · {n} H₂O"
    plotly_style = ("dash", "square") if is_anion else (("solid", "circle") if n >= 6 else ("dot", "diamond"))
    mpl_style = ("--", "s") if is_anion else (("-", "o") if n >= 6 else (":", "D"))
    return name, plotly_style, mpl_style


def md_figure(runs: pd.DataFrame, prov: str):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    agg = _md_agg(runs)
    if agg.empty:
        return None
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.1, subplot_titles=(
        "Acid (or anion)–water H-bonds", "All intermolecular H-bonds"))
    for (acid, is_anion, n), g in agg.groupby(["acid", "anion", "n_water"]):
        g = g.sort_values("temperature_k")
        name, (dash, sym), _ = _md_series_style(acid, is_anion, n)
        for col, (y, e) in enumerate((("aw", "aw_sem"), ("tot", "tot_sem")), start=1):
            fig.add_trace(go.Scatter(
                x=g["temperature_k"] - 273.15, y=g[y], mode="lines+markers", name=name, legendgroup=name,
                showlegend=col == 1, error_y={"type": "data", "array": g[e], "visible": True, "thickness": 1.2, "width": 4},
                line={"color": SERIES.get(acid, TEXT2), "width": 2, "dash": dash},
                marker={"size": 9, "color": SERIES.get(acid, TEXT2), "symbol": sym, "line": {"width": 1.5, "color": SURFACE}},
                customdata=np.stack([g["start_structure"], g["t_meas"], g["n_seeds"], g[e]], axis=-1),
                hovertemplate="%{customdata[0]}<br>T set %{x:.0f} °C (measured %{customdata[1]:.0f} K)"
                              "<br>H-bonds = %{y:.2f} ± %{customdata[3]:.2f} (SEM, %{customdata[2]} seeds)<extra></extra>"),
                row=1, col=col)
        for col in (1, 2):
            fig.update_xaxes(title_text="thermostat temperature (°C)", gridcolor=GRID, row=1, col=col)
            fig.update_yaxes(title_text="mean count over 2nd ps (± SEM, 3 seeds)", gridcolor=GRID, rangemode="tozero",
                             row=1, col=col)
    ok = runs[runs["status"] == "completed"]
    _layout_common(fig, "Hydrogen bonding in NVT Langevin MD (AIMNet2 via NVIDIA ALCHEMI)",
                   f"gas-phase clusters, {int(ok['n_steps'].iloc[0] * ok['timestep_fs'].iloc[0] / 1000)} ps per run, "
                   f"γ = {ok['friction_per_fs'].iloc[0]} fs⁻¹ · {prov}")
    fig.update_layout(height=560)
    fig.update_annotations(font={"size": 12, "color": TEXT2})
    return fig


def md_static_figure(runs: pd.DataFrame, prov: str, out_dir: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    agg = _md_agg(runs)
    if agg.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    for ax, (y, e, title) in zip(axes, (("aw", "aw_sem", "Acid (or anion)–water H-bonds"),
                                        ("tot", "tot_sem", "All intermolecular H-bonds"))):
        ax.set_facecolor(SURFACE)
        for (acid, is_anion, n), g in agg.groupby(["acid", "anion", "n_water"]):
            g = g.sort_values("temperature_k")
            name, _, (ls, mk) = _md_series_style(acid, is_anion, n)
            ax.errorbar(g["temperature_k"] - 273.15, g[y], yerr=g[e], ls=ls, marker=mk, color=SERIES.get(acid, TEXT2),
                        lw=2, ms=6, mec=SURFACE, capsize=3, label=name)
        ax.set_title(title, fontsize=10, color=TEXT, loc="left")
        ax.set_xlabel("thermostat temperature (°C)", color=TEXT)
        ax.set_ylabel("mean count over 2nd ps (± SEM, 3 seeds)", color=TEXT)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color=GRID, lw=0.8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    h_, l_ = axes[0].get_legend_handles_labels()
    fig.legend(h_, l_, frameon=False, fontsize=8.5, loc="lower center", bbox_to_anchor=(0.5, 0.035), ncol=3)
    fig.suptitle("Hydrogen bonding in NVT Langevin MD: AIMNet2 via NVIDIA ALCHEMI", x=0.01, ha="left",
                 fontsize=12, color=TEXT)
    fig.text(0.01, 0.01, prov + " · gas-phase clusters, 2 ps per run, γ = 0.01 fs⁻¹; geometric H-bond criterion",
             fontsize=7.5, color=TEXT2)
    fig.tight_layout(rect=(0, 0.13, 1, 0.95))
    p = out_dir / "md_hbonds_vs_temperature.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p

# ----------------------------------------------------------------------------- driver


def run(results_dir: Path | None = None, out_dir: Path | None = None, figures: bool = True,
        md_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else ALCHEMI_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = load_results(results_dir)
    if not recs:
        raise SystemExit(f"no result JSON found in {results_dir or ALCHEMI_DIR / 'results' / 'json'}")
    prov = provenance_label(recs)
    df = energy_table(recs)
    ref = references(df)
    assoc = association_table(df, ref)
    hb, hbs = hbond_tables(recs)
    ion_states, deprot = ionic_tables(recs, df, ref)
    if not ion_states.empty:
        ion_states.to_csv(out_dir / "ionic_states.csv", index=False)
    if not deprot.empty:
        deprot.to_csv(out_dir / "deprotonation_energies.csv", index=False)
    df.to_csv(out_dir / "energy_summary.csv", index=False)
    assoc.to_csv(out_dir / "association_energies.csv", index=False)
    hb.to_csv(out_dir / "hbonds.csv", index=False)
    hbs.to_csv(out_dir / "hbond_summary.csv", index=False)

    best = assoc[assoc.get("is_lowest_energy_conformer", False) == True] if not assoc.empty else assoc
    hbs_by_id = hbs.set_index("id") if not hbs.empty else pd.DataFrame()
    digest = {
        "provenance": prov,
        "model": sorted({(r.get("method") or {}).get("checkpoint") for r in recs if r.get("method")}),
        "method": next((r.get("method") for r in recs if r.get("method")), None),
        "versions": next((r.get("versions") for r in recs if r.get("versions")), None),
        "n_calculations": len(recs),
        "n_converged": int(df["converged"].sum()),
        "n_failed": int((~df["converged"]).sum()),
        "references": ref,
        "lowest_energy_clusters": [
            {"acid": r["acid"], "n_water": int(r["n_water"]), "id": r["id"],
             "dE_assoc_kcal_mol": r.get("dE_assoc_kcal_mol"),
             "dE_assoc_per_water_kcal_mol": r.get("dE_assoc_per_water_kcal_mol"),
             "dE_acid_to_water_network_kcal_mol": r.get("dE_acid_to_water_network_kcal_mol"),
             "n_hbonds": int(hbs_by_id.loc[r["id"], "n_hbonds"]) if r["id"] in hbs_by_id.index else None,
             "n_intermolecular_hbonds": int(hbs_by_id.loc[r["id"], "n_intermolecular"]) if r["id"] in hbs_by_id.index else None}
            for _, r in best.sort_values(["acid", "n_water"]).iterrows()],
        "definitions": {
            "dE_assoc": "E(acid.nH2O) - min_conf E(acid) - n E(H2O); AIMNet2 electronic energy, 0 K, no ZPE/thermal/solvent",
            "dE_acid_to_water_network": "E(acid.nH2O) - min_conf E(acid) - min_conf E((H2O)n)",
            "hbond": f"d(D-H)<={HB_MAX_DH} A, d(D..A)<={HB_MAX_DA} A, d(H..A)<={HB_MAX_HA} A, angle(D-H..A)>={HB_MIN_ANGLE} deg",
        },
        "caveat": "ALCHEMI/AIMNet2 gives molecular interaction energies only; it does not predict dough pH, "
                  "sourness, gas production or crumb structure.",
        "files": {k: f"data/alchemi/analysis/{v}" for k, v in {
            "energy_summary": "energy_summary.csv", "association_energies": "association_energies.csv",
            "hbonds": "hbonds.csv", "hbond_summary": "hbond_summary.csv",
            "association_energy_html": "association_energy.html", "molecular_explorer_html": "molecular_explorer.html",
            "association_energy_png": "association_energy.png",
            "association_energy_per_water_png": "association_energy_per_water.png"}.items()},
    }
    xc_path = (Path(results_dir).parent if results_dir else ALCHEMI_DIR / "results") / "crosscheck_upstream_aimnet.json"
    if xc_path.exists():
        xc = json.loads(xc_path.read_text())
        digest["crosscheck_upstream_aimnet"] = {k: xc.get(k) for k in ("n", "max_abs_delta_energy_ev",
                                                                        "mean_abs_delta_energy_ev",
                                                                        "max_abs_force_diff_ev_per_angstrom")}
    descriptors = molecular_descriptors(assoc, hb, deprot, recs, prov)
    if "crosscheck_upstream_aimnet" in digest:
        descriptors["provenance"]["crosscheck_upstream_aimnet"] = digest["crosscheck_upstream_aimnet"]
    digest["files"]["molecular_descriptors"] = "data/alchemi/analysis/molecular_descriptors.json"
    if not deprot.empty:
        digest["deprotonation"] = deprot.to_dict("records")
        digest["files"]["deprotonation_energies"] = "data/alchemi/analysis/deprotonation_energies.csv"
        digest["files"]["ionic_states"] = "data/alchemi/analysis/ionic_states.csv"
    md_runs, md_series = md_tables(md_dir)
    if not md_runs.empty:
        md_runs.to_csv(out_dir / "md_summary.csv", index=False)
        md_series.to_csv(out_dir / "md_hbond_timeseries.csv", index=False)
        digest["md"] = {"n_runs": len(md_runs), "n_completed": int((md_runs["status"] == "completed").sum()),
                        "runs": md_runs.to_dict("records")}
        digest["files"]["md_summary"] = "data/alchemi/analysis/md_summary.csv"
        digest["files"]["md_hbond_timeseries"] = "data/alchemi/analysis/md_hbond_timeseries.csv"
        digest["files"]["md_hbonds_vs_temperature_html"] = "data/alchemi/analysis/md_hbonds_vs_temperature.html"
        _md_descriptors(md_runs, descriptors)
    (out_dir / "molecular_descriptors.json").write_text(json.dumps(descriptors, indent=2, default=_json_default))
    if figures:
        if not assoc.empty and "dE_assoc_kcal_mol" in assoc:
            association_figure(assoc, prov).write_html(out_dir / "association_energy.html", include_plotlyjs=True,
                                                       full_html=True, config={"displaylogo": False})
            static_figures(assoc, prov, out_dir)
        if not deprot.empty:
            for pth in deprotonation_figures(deprot, prov, out_dir):
                digest["files"][pth.stem + "_" + pth.suffix[1:]] = f"data/alchemi/analysis/{pth.name}"
        if not md_runs.empty:
            mf = md_figure(md_runs, prov)
            if mf is not None:
                mf.write_html(out_dir / "md_hbonds_vs_temperature.html", include_plotlyjs=True, full_html=True,
                              config={"displaylogo": False})
            if md_static_figure(md_runs, prov, out_dir) is not None:
                digest["files"]["md_hbonds_vs_temperature_png"] = "data/alchemi/analysis/md_hbonds_vs_temperature.png"
        explorer_figure(recs, assoc, prov).write_html(out_dir / "molecular_explorer.html", include_plotlyjs=True,
                                                      full_html=True, config={"displaylogo": False})
    (out_dir / "analysis_summary.json").write_text(json.dumps(digest, indent=2, default=_json_default))
    return digest


def _md_descriptors(md_runs: pd.DataFrame, descriptors: dict) -> None:
    """Per acid: acid(anion)-water H-bond counts from NVT MD, mean +- SEM over independent seeds."""
    ok = md_runs[md_runs["status"] == "completed"].copy()
    if ok.empty:
        return
    head = ok["start_structure"].str.split("__").str[0]
    ok["species"] = np.where(head.str.endswith("_anion"), "anion", "neutral")
    ok["acid"] = head.str.replace("_anion", "", regex=False)
    ok["n_water"] = ok["start_structure"].str.extract(r"__n(\d+)__")[0].astype(int)
    for acid, g in ok.groupby("acid"):
        d = descriptors.setdefault(acid, {})
        out: dict[str, Any] = {}
        for (sp, n, T), h in g.groupby(["species", "n_water", "temperature_k"]):
            v = h["mean_acid_water_hbonds_second_half"].astype(float)
            w = h["mean_intermolecular_hbonds_second_half"].astype(float)
            key = f"{sp}_n{int(n)}"
            out.setdefault(key, {})[f"{T:.2f}"] = {
                "acid_water_hbonds_mean": float(v.mean()),
                "acid_water_hbonds_sem": float(v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else None,
                "all_intermolecular_hbonds_mean": float(w.mean()), "n_seeds": len(v)}
        d["md_hbonds_by_species_and_T"] = out
    descriptors["provenance"]["definitions"]["md_hbonds_by_species_and_T"] = (
        "NVT Langevin (BAOAB) 2 ps per run at T (K), mean over the 2nd ps of acid(or anion)-water geometric H-bonds; "
        "mean and SEM over independent velocity/noise seeds")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if math.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Analyse ALCHEMI results (Mac-side)")
    ap.add_argument("--results", default=None, help="dir of result JSONs (default data/alchemi/results/json)")
    ap.add_argument("--out", default=None, help="output dir (default data/alchemi/analysis)")
    ap.add_argument("--no-figures", action="store_true")
    a = ap.parse_args(argv)
    d = run(Path(a.results) if a.results else None, Path(a.out) if a.out else None, figures=not a.no_figures)
    print(json.dumps({k: d[k] for k in ("provenance", "n_calculations", "n_converged", "n_failed")}, indent=1))
    for row in d["lowest_energy_clusters"]:
        print(row)


if __name__ == "__main__":
    main()
