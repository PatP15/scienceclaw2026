"""Campaign-2 starting structures (additive to campaign 1; Mac-side, RDKit + NumPy, no GPU).

Writes ``data/alchemi/inputs_c2/{manifest.json, xyz/}`` in the same manifest format as
:mod:`fermentai.molecular.structures`. Sets (see ``configs/molecular.yaml`` ``campaign2``):

* sampling  - neutral acids and carboxylate anions, n = 0 / 3 / 6 waters, 40 seeded starts each
              (anions are solvated directly here, not derived from neutral minima)
* large     - n = 10, 15 for the four species (12 starts) + pure (H2O)n references (10 starts)
* cosolutes - ethanol, beta-D-glucose, Ac-Gln-Gln-NHMe: monomer, X.3H2O, and acid + X + 3H2O
* dimers    - acetic-acetic (8 random + 1 explicit cyclic C2h start) and lactic-acetic gas-phase pairs
* mixed     - lactic + acetic + 6 H2O
* refs      - water monomer and H3O+ (for vibrational thermochemistry)

Ids: ``<species>__n<n>__s<kk>`` (sampling/large), ``<acid>__<cosolute>__n3__s<kk>``,
``<a>__<b>__n<n>__s<kk>`` (dimers / mixed), ``<cosolute>__n<n>__s<kk>``.
All seeds are SHA-256 derived from ``campaign2.base_seed`` and the id parts.

CLI::  uv run python -m fermentai.molecular.structures_c2
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from fermentai.molecular.structures import (
    ANION_NAME,
    COV_RADII_PLACEMENT,
    REPO_ROOT,
    Fragment,
    Structure,
    _clash,
    _rotation_a_to_b,
    _seed,
    embed_molecule,
    load_config,
    place_water,
    water_fragment,
    write_inputs,
)

HYD0 = np.array([[0.0, 0.0, 0.10], [0.0, 0.94, -0.25], [0.81, -0.47, -0.25], [-0.81, -0.47, -0.25]])


def _spin(axis: np.ndarray, ang: float) -> np.ndarray:
    k = axis / np.linalg.norm(axis)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(ang) * kx + (1 - np.cos(ang)) * kx @ kx


def _neighbors(frag: Fragment, i: int) -> list[int]:
    out = []
    for j in range(len(frag.symbols)):
        if j == i:
            continue
        lim = 1.25 * (COV_RADII_PLACEMENT.get(frag.symbols[i], 0.75) + COV_RADII_PLACEMENT.get(frag.symbols[j], 0.75))
        if np.linalg.norm(frag.coords[i] - frag.coords[j]) <= lim:
            out.append(j)
    return out


def _lone_pair_dir(frag: Fragment, i: int) -> np.ndarray:
    nb = _neighbors(frag, i)
    if not nb:
        return np.array([0.0, 0.0, 1.0])
    v = sum((frag.coords[i] - frag.coords[j]) / np.linalg.norm(frag.coords[i] - frag.coords[j]) for j in nb)
    n = np.linalg.norm(v)
    return v / n if n > 1e-6 else np.array([0.0, 0.0, 1.0])


def place_fragment(frags: list[Fragment], new: Fragment, rng: np.random.Generator, p: dict,
                   anchor_frag: int | None = None) -> Fragment:
    """Place a rigid fragment H-bonded to an existing polar site (seeded, clash-checked).

    Donor site on the existing cluster -> an acceptor O of `new` sits 1.85 A beyond the donor H with its
    lone-pair direction facing it. Acceptor site -> a donor H of `new` points at it from 1.85 A.
    `anchor_frag` restricts the existing sites to one fragment (e.g. bind a co-solute to the acid)."""
    sym = [s for f in frags for s in f.symbols]
    xyz = np.vstack([f.coords for f in frags])
    sites: list[tuple[str, int]] = []
    off = 0
    for k, f in enumerate(frags):
        if anchor_frag is None or k == anchor_frag:
            sites += [("donor", off + i) for i in f.donor_h] + [("acceptor", off + i) for i in f.acceptor_o]
        off += len(f.symbols)
    new_acc = list(new.acceptor_o)
    new_don = list(new.donor_h)
    center = xyz.mean(axis=0)
    for _ in range(p["max_attempts_per_water"] * 2):
        kind, idx = sites[rng.integers(len(sites))]
        jitter = rng.normal(scale=p["jitter_angstrom"], size=3)
        local = new.coords - new.coords.mean(axis=0)
        if kind == "donor" and new_acc:
            heavy = int(np.argmin([np.linalg.norm(xyz[idx] - xyz[j]) if j != idx else 9e9 for j in range(len(xyz))]))
            u = xyz[idx] - xyz[heavy]
            u /= np.linalg.norm(u)
            a = new_acc[rng.integers(len(new_acc))]
            lp = _lone_pair_dir(new, a)
            rot = _spin(u, rng.uniform(0, 2 * np.pi)) @ _rotation_a_to_b(lp, -u)
            placed = (rot @ local.T).T
            target = xyz[idx] + u * p["donor_h_to_acceptor_o_angstrom"] + jitter
            placed += target - placed[a]
        elif kind == "acceptor" and new_don:
            out = xyz[idx] - center
            out = out / np.linalg.norm(out) if np.linalg.norm(out) > 1e-6 else rng.normal(size=3)
            v = out + rng.normal(scale=0.6, size=3)
            v /= np.linalg.norm(v)
            h = new_don[rng.integers(len(new_don))]
            d = _neighbors(new, h)[0]
            w = new.coords[d] - new.coords[h]
            rot = _spin(v, rng.uniform(0, 2 * np.pi)) @ _rotation_a_to_b(w, v)
            placed = (rot @ local.T).T
            target = xyz[idx] + v * p["donor_h_to_acceptor_o_angstrom"] + jitter
            placed += target - placed[h]
        else:
            continue
        if not _clash(new.symbols, placed, sym, xyz, p):
            return Fragment(new.name, list(new.symbols), placed, new.smiles, list(new.donor_h), list(new.acceptor_o))
    raise RuntimeError(f"could not place {new.name} without clashes")


def cyclic_acetic_dimer(seed: int) -> list[Fragment]:
    """Explicit C2h cyclic (doubly H-bonded) acetic acid dimer start: point inversion of a syn
    monomer through a centre placed so that O...O ~ 2.68 A."""
    a = embed_molecule("CC(=O)O", seed, "acetic_acid")
    x = a.coords.copy()
    sy = a.symbols
    c_carb = next(i for i, s in enumerate(sy) if s == "C" and sum(
        1 for j, t in enumerate(sy) if t == "O" and np.linalg.norm(x[i] - x[j]) < 1.6) == 2)
    c_me = next(i for i, s in enumerate(sy) if s == "C" and i != c_carb)
    os_ = [j for j, t in enumerate(sy) if t == "O"]
    d = x[c_carb] - x[c_me]
    d /= np.linalg.norm(d)
    proj = [float(np.dot(x[o] - x[c_carb], d)) for o in os_]
    s = (sum(proj) + 2.68) / 2.0
    centre = x[c_carb] + d * s
    b = 2 * centre - x
    # put the hydroxyl H in the plane pointing to the partner carbonyl (syn), keep geometry otherwise
    return [Fragment("acetic_acid", list(sy), x, a.smiles, a.donor_h, a.acceptor_o),
            Fragment("acetic_acid", list(sy), b, a.smiles, a.donor_h, a.acceptor_o)]


def _species_frag(spec: dict, name: str, seed: int) -> Fragment:
    acid = name.replace("_anion", "")
    frag_name = ANION_NAME[acid] if name.endswith("_anion") else acid
    return embed_molecule(spec["smiles"], seed, frag_name)


def _solvate(frags: list[Fragment], n: int, rng, p) -> list[Fragment]:
    for _ in range(n):
        frags.append(place_water(frags, rng, p))
    return frags


def build_campaign2(cfg: dict[str, Any]) -> list[Structure]:
    c2 = cfg["campaign2"]
    p = cfg["structures"]["placement"]
    base = int(c2["base_seed"])
    out: list[Structure] = []

    def add(sid, kind, frags, acid, n, k, seeds, charge, **meta):
        out.append(Structure(sid, kind, frags, acid, n, k, {"base_seed": base, **seeds}, charge=charge,
                             meta={"campaign": 2, **meta}))

    # references for thermochemistry
    add("water__n1__s00", "water_monomer", [water_fragment()], None, 1, 0, {}, 0, species="H2O")
    add("hydronium__n0__s00", "hydronium",
        [Fragment("hydronium", ["O", "H", "H", "H"], HYD0.copy(), "[OH3+]")], None, 0, 0, {}, 1, species="H3O(+)")

    def species_sets(water_counts, n_starts, set_name):
        for name, spec in c2["species"].items():
            acid = name.replace("_anion", "")
            is_anion = name.endswith("_anion")
            for n in water_counts:
                starts = min(n_starts, 12) if n == 0 else n_starts
                for k in range(starts):
                    es = _seed(base, name, set_name, n, k, "etkdg")
                    ps = _seed(base, name, set_name, n, k, "placement")
                    rng = np.random.default_rng(ps)
                    frags = _solvate([_species_frag(spec, name, es)], n, rng, p)
                    kind = ("anion" if is_anion else "acid") + ("_monomer" if n == 0 else "_water_cluster")
                    add(f"{name}__n{n}__s{k:02d}", kind, frags, acid, n, k, {"etkdg_seed": es, "placement_seed": ps},
                        int(spec["charge"]), species=name, set=set_name)

    species_sets(c2["sampling"]["water_counts"], int(c2["sampling"]["n_starts"]), "sampling")
    species_sets(c2["large"]["water_counts"], int(c2["large"]["n_starts"]), "large")
    for n in c2["large"]["water_counts"]:
        for k in range(int(c2["large"]["water_cluster_starts"])):
            ps = _seed(base, "water_cluster", n, k)
            rng = np.random.default_rng(ps)
            frags = _solvate([water_fragment()], n - 1, rng, p)
            add(f"water_cluster__n{n}__s{k:02d}", "water_cluster", frags, None, n, k, {"placement_seed": ps}, 0,
                species=f"(H2O){n}", set="large")

    # co-solutes
    nw = int(c2["cosolute_waters"])
    acids = {a: s for a, s in c2["species"].items() if not a.endswith("_anion")}
    for x, xs in c2["cosolutes"].items():
        for k in range(int(c2["cosolute_monomer_starts"])):
            es = _seed(base, x, "monomer", k)
            add(f"{x}__n0__s{k:02d}", "cosolute_monomer", [embed_molecule(xs["smiles"], es, x)], None, 0, k,
                {"etkdg_seed": es}, 0, cosolute=x, set="cosolute")
        for k in range(int(c2["cosolute_starts"])):
            es = _seed(base, x, "hydrated", k, "etkdg")
            ps = _seed(base, x, "hydrated", k, "placement")
            rng = np.random.default_rng(ps)
            frags = _solvate([embed_molecule(xs["smiles"], es, x)], nw, rng, p)
            add(f"{x}__n{nw}__s{k:02d}", "cosolute_water_cluster", frags, None, nw, k,
                {"etkdg_seed": es, "placement_seed": ps}, 0, cosolute=x, set="cosolute")
        for acid, aspec in acids.items():
            for k in range(int(c2["cosolute_starts"])):
                ea = _seed(base, acid, x, k, "etkdg_acid")
                ex = _seed(base, acid, x, k, "etkdg_cosolute")
                ps = _seed(base, acid, x, k, "placement")
                rng = np.random.default_rng(ps)
                frags = [embed_molecule(aspec["smiles"], ea, acid)]
                frags.append(place_fragment(frags, embed_molecule(xs["smiles"], ex, x), rng, p, anchor_frag=0))
                frags = _solvate(frags, nw, rng, p)
                add(f"{acid}__{x}__n{nw}__s{k:02d}", "cosolute_cluster", frags, acid, nw, k,
                    {"etkdg_seed_acid": ea, "etkdg_seed_cosolute": ex, "placement_seed": ps}, 0, cosolute=x,
                    set="cosolute")

    # acid dimers (gas phase) and mixed lactic + acetic + 6 H2O
    def pair_sets(a, b, n, kind, starts):
        for k in range(starts):
            ea = _seed(base, a, b, n, k, "etkdg_a")
            eb = _seed(base, a, b, n, k, "etkdg_b")
            ps = _seed(base, a, b, n, k, "placement")
            rng = np.random.default_rng(ps)
            frags = [embed_molecule(acids[a]["smiles"], ea, a)]
            frags.append(place_fragment(frags, embed_molecule(acids[b]["smiles"], eb, b), rng, p, anchor_frag=0))
            frags = _solvate(frags, n, rng, p)
            add(f"{a}__{b}__n{n}__s{k:02d}", kind, frags, a, n, k,
                {"etkdg_seed_a": ea, "etkdg_seed_b": eb, "placement_seed": ps}, 0, partner=b, set="dimers")

    pair_sets("acetic_acid", "acetic_acid", 0, "acid_dimer", int(c2["dimer_starts"]))
    cs = _seed(base, "acetic_dimer_cyclic")
    add("acetic_acid__acetic_acid__n0__cyc", "acid_dimer", cyclic_acetic_dimer(cs), "acetic_acid", 0, 99,
        {"etkdg_seed": cs}, 0, partner="acetic_acid", set="dimers",
        derivation="explicit C2h cyclic start (point inversion of a syn monomer, O...O ~2.68 A)")
    pair_sets("lactic_acid", "acetic_acid", 0, "acid_dimer", int(c2["dimer_starts"]))
    pair_sets("lactic_acid", "acetic_acid", int(c2["mixed_cluster_waters"]), "mixed_acid_cluster",
              int(c2["dimer_starts"]))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="campaign-2 starting structures")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    out = Path(a.out) if a.out else REPO_ROOT / cfg["campaign2"]["output_dir"]
    sts = build_campaign2(cfg)
    man = write_inputs(sts, out, {"structures": cfg["campaign2"]})
    kinds: dict[str, int] = {}
    for e in man["structures"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"wrote {len(sts)} structures to {out}: {kinds}")


if __name__ == "__main__":
    main()
