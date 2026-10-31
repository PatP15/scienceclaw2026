"""Build reproducible starting structures for the ALCHEMI / AIMNet2 calculations.

Everything here runs on the Mac (RDKit + NumPy, no GPU).

Species
-------
* isolated acids: L-lactic acid and acetic acid (several ETKDG conformers each)
* isolated water monomer
* pure water clusters (H2O)_n, n in water_counts \\ {1}, as optional references
* acid + n water clusters, n in water_counts (default 1, 3, 6), several seeded starts each

Each structure is written as an XYZ file plus one entry in ``manifest.json`` that records the
molecular identities, fragment atom indices, seeds, charge and multiplicity. Generation is
deterministic for a given RDKit version and config (no timestamps in the manifest).

Water placement: each water is attached to a hydrogen-bonding site chosen at random (seeded)
from the polar atoms already present (acid O and acidic/hydroxyl H first, then placed waters).
For a donor H the water oxygen is placed along the X-H bond extension; for an acceptor O the
water is placed ~2.85 A away with one of its H atoms pointing at the acceptor. Clashing
placements are rejected. These are *starting guesses*; the AIMNet2 optimisation on the GPU
decides the final geometry.

CLI::

    uv run python -m fermentai.molecular.structures [--config configs/molecular.yaml]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "molecular.yaml"

# Experimental gas-phase water geometry (r_OH = 0.9572 A, HOH = 104.52 deg).
_R_OH = 0.9572
_HOH = np.deg2rad(104.52)
_WATER_LOCAL = np.array(
    [
        [0.0, 0.0, 0.0],
        [_R_OH * np.sin(_HOH / 2), 0.0, _R_OH * np.cos(_HOH / 2)],
        [-_R_OH * np.sin(_HOH / 2), 0.0, _R_OH * np.cos(_HOH / 2)],
    ]
)
_WATER_SYMBOLS = ["O", "H", "H"]


@dataclass
class Fragment:
    name: str  # "lactic_acid", "acetic_acid", "water"
    symbols: list[str]
    coords: np.ndarray  # (n, 3) Angstrom
    smiles: str
    donor_h: list[int] = field(default_factory=list)  # local indices of polar H
    acceptor_o: list[int] = field(default_factory=list)  # local indices of O


@dataclass
class Structure:
    id: str
    kind: str  # acid_monomer | water_monomer | water_cluster | acid_water_cluster | (derived, see build_derived)
    fragments: list[Fragment]
    acid: str | None
    n_water: int
    conformer_index: int
    seeds: dict[str, int]
    charge: int = 0
    multiplicity: int = 1
    meta: dict[str, Any] = field(default_factory=dict)  # derived_from / derivation / species

    @property
    def symbols(self) -> list[str]:
        return [s for f in self.fragments for s in f.symbols]

    @property
    def coords(self) -> np.ndarray:
        return np.vstack([f.coords for f in self.fragments])

    def fragment_ranges(self) -> list[dict[str, Any]]:
        out, start = [], 0
        for f in self.fragments:
            out.append({"name": f.name, "smiles": f.smiles, "start": start, "stop": start + len(f.symbols)})
            start += len(f.symbols)
        return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_CONFIG) as fh:
        return yaml.safe_load(fh)


# ----------------------------------------------------------------------------- RDKit helpers


def embed_molecule(smiles: str, seed: int, name: str) -> Fragment:
    """ETKDGv3 embedding of an explicitly hydrogenated molecule with a fixed random seed."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    params.useRandomCoords = False
    status = AllChem.EmbedMolecule(mol, params)
    if status != 0:
        raise RuntimeError(f"ETKDG embedding failed for {smiles} seed={seed}")
    conf = mol.GetConformer()
    coords = np.array(conf.GetPositions(), dtype=float)
    coords -= coords.mean(axis=0)
    symbols = [a.GetSymbol() for a in mol.GetAtoms()]
    donor_h = [
        a.GetIdx()
        for a in mol.GetAtoms()
        if a.GetSymbol() == "H" and a.GetNeighbors()[0].GetSymbol() in ("O", "N")
    ]
    acceptor_o = [a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "O"]
    return Fragment(name, symbols, coords, Chem.MolToSmiles(Chem.RemoveHs(mol)), donor_h, acceptor_o)


def water_fragment(coords: np.ndarray | None = None) -> Fragment:
    return Fragment(
        "water",
        list(_WATER_SYMBOLS),
        _WATER_LOCAL.copy() if coords is None else coords,
        "O",
        donor_h=[1, 2],
        acceptor_o=[0],
    )


def cip_label(smiles: str) -> str | None:
    """CIP label of the (single) stereocentre, used to check L-lactic acid is (S)."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    return centers[0][1] if centers else None


# ----------------------------------------------------------------------------- water placement


def _random_rotation(rng: np.random.Generator) -> np.ndarray:
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    a, b, c, d = q
    return np.array(
        [
            [a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
            [2 * (b * c + a * d), a * a - b * b + c * c - d * d, 2 * (c * d - a * b)],
            [2 * (b * d - a * c), 2 * (c * d + a * b), a * a - b * b - c * c + d * d],
        ]
    )


def _rotation_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix taking unit vector a onto unit vector b (Rodrigues)."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-10:
        if c > 0:
            return np.eye(3)
        perp = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
        v = np.cross(a, perp)
        v /= np.linalg.norm(v)
        return 2 * np.outer(v, v) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def _clash(new_sym: list[str], new_xyz: np.ndarray, sym: list[str], xyz: np.ndarray, p: dict) -> bool:
    d = np.linalg.norm(new_xyz[:, None, :] - xyz[None, :, :], axis=-1)
    for i, si in enumerate(new_sym):
        for j, sj in enumerate(sym):
            lim = p["min_any_h_angstrom"] if "H" in (si, sj) else p["min_heavy_heavy_angstrom"]
            if d[i, j] < lim:
                return True
    return False


def _bonded_neighbor(xyz: np.ndarray, sym: list[str], idx: int) -> int:
    d = np.linalg.norm(xyz - xyz[idx], axis=1)
    d[idx] = np.inf
    return int(np.argmin(d))


def place_water(frags: list[Fragment], rng: np.random.Generator, p: dict) -> Fragment:
    """Place one water H-bonded to a randomly chosen polar site of the existing fragments."""
    sym = [s for f in frags for s in f.symbols]
    xyz = np.vstack([f.coords for f in frags])
    sites: list[tuple[str, int]] = []
    offset = 0
    for k, f in enumerate(frags):
        # Prefer sites on the acid (fragment 0) for the first waters: weight acid sites x2.
        w = 2 if (k == 0 and f.name != "water") else 1
        sites += [("donor", offset + i) for i in f.donor_h] * w
        sites += [("acceptor", offset + i) for i in f.acceptor_o] * w
        offset += len(f.symbols)

    for _ in range(p["max_attempts_per_water"]):
        kind, idx = sites[rng.integers(len(sites))]
        jitter = rng.normal(scale=p["jitter_angstrom"], size=3)
        if kind == "donor":
            # water O on the extension of X-H, water H's pointing away (random spin).
            heavy = _bonded_neighbor(xyz, sym, idx)
            u = xyz[idx] - xyz[heavy]
            u /= np.linalg.norm(u)
            o_pos = xyz[idx] + u * p["donor_h_to_acceptor_o_angstrom"] + jitter
            bis = (_WATER_LOCAL[1] + _WATER_LOCAL[2]) / 2
            rot = _rotation_a_to_b(bis, u)  # lone-pair side (−bisector) faces the donor H
            ang = rng.uniform(0, 2 * np.pi)
            k_ = u
            kx = np.array([[0, -k_[2], k_[1]], [k_[2], 0, -k_[0]], [-k_[1], k_[0], 0]])
            rspin = np.eye(3) + np.sin(ang) * kx + (1 - np.cos(ang)) * kx @ kx
            local = (rspin @ rot @ _WATER_LOCAL.T).T
        else:
            # water donates one H to acceptor O; place water O at ~2.85 A in a random outward dir.
            heavy_center = xyz[idx]
            outward = heavy_center - xyz.mean(axis=0)
            if np.linalg.norm(outward) < 1e-6:
                outward = rng.normal(size=3)
            outward /= np.linalg.norm(outward)
            direction = outward + rng.normal(scale=0.6, size=3)
            direction /= np.linalg.norm(direction)
            o_pos = heavy_center + direction * p["acceptor_o_to_water_o_angstrom"] + jitter
            # orient: H1 points at the acceptor O, rest random about that axis
            to_acc = heavy_center - o_pos
            to_acc /= np.linalg.norm(to_acc)
            rot = _rotation_a_to_b(_WATER_LOCAL[1] / np.linalg.norm(_WATER_LOCAL[1]), to_acc)
            ang = rng.uniform(0, 2 * np.pi)
            kx = np.array([[0, -to_acc[2], to_acc[1]], [to_acc[2], 0, -to_acc[0]], [-to_acc[1], to_acc[0], 0]])
            rspin = np.eye(3) + np.sin(ang) * kx + (1 - np.cos(ang)) * kx @ kx
            local = (rspin @ rot @ _WATER_LOCAL.T).T
        new_xyz = local + o_pos
        if not _clash(_WATER_SYMBOLS, new_xyz, sym, xyz, p):
            return water_fragment(new_xyz)
    raise RuntimeError("could not place water without clashes; increase max_attempts_per_water")


# ----------------------------------------------------------------------------- builders


def _seed(base: int, *parts: Any) -> int:
    """Stable 31-bit seed derived from base seed and labels (hash, not Python's salted hash())."""
    h = hashlib.sha256(("|".join(str(x) for x in (base, *parts))).encode()).hexdigest()
    return int(h[:8], 16) & 0x7FFFFFFF


def build_all(cfg: dict[str, Any]) -> list[Structure]:
    s = cfg["structures"]
    base = int(s["base_seed"])
    p = s["placement"]
    out: list[Structure] = []

    # water monomer (single reference geometry; optimisation removes any bias)
    out.append(
        Structure("water__n1__c0", "water_monomer", [water_fragment()], None, 1, 0, {"base_seed": base})
    )
    # pure water clusters
    for n in s["water_counts"]:
        if n < 2:
            continue
        for c in range(s["n_conformers_water_cluster"]):
            pseed = _seed(base, "water_cluster", n, c)
            rng = np.random.default_rng(pseed)
            frags = [water_fragment()]
            for _ in range(n - 1):
                frags.append(place_water(frags, rng, p))
            out.append(
                Structure(f"water_cluster__n{n}__c{c}", "water_cluster", frags, None, n, c,
                          {"base_seed": base, "placement_seed": pseed})
            )

    for acid, spec in s["acids"].items():
        for c in range(s["n_conformers_acid"]):
            eseed = _seed(base, acid, "monomer", c)
            frag = embed_molecule(spec["smiles"], eseed, acid)
            out.append(
                Structure(f"{acid}__n0__c{c}", "acid_monomer", [frag], acid, 0, c,
                          {"base_seed": base, "etkdg_seed": eseed})
            )
        for n in s["water_counts"]:
            for c in range(s["n_conformers_cluster"]):
                eseed = _seed(base, acid, "cluster", n, c, "etkdg")
                pseed = _seed(base, acid, "cluster", n, c, "placement")
                acid_frag = embed_molecule(spec["smiles"], eseed, acid)
                rng = np.random.default_rng(pseed)
                frags = [acid_frag]
                for _ in range(n):
                    frags.append(place_water(frags, rng, p))
                out.append(
                    Structure(f"{acid}__n{n}__c{c}", "acid_water_cluster", frags, acid, n, c,
                              {"base_seed": base, "etkdg_seed": eseed, "placement_seed": pseed})
                )
    return out


# ----------------------------------------------------------------------------- derived (charged) species
#
# Stage 2 of the workflow: conjugate bases and ion pairs are derived from the *optimised* neutral
# ALCHEMI geometries (data/alchemi/results/json), so they share seeds/conformer index with the
# neutral cluster they came from (matched composition, same seeds).
#
#   <acid>_anion__n0__c<k>        carboxylate, charge -1, from optimised acid monomer conformer k
#   <acid>_anion__n<n>__c<k>      carboxylate.(H2O)n, charge -1, carboxyl H removed from acid.(H2O)n c<k>
#   <acid>_ionpair__n<n>__c<k>    carboxylate.H3O+.(H2O)n-1, charge 0: carboxyl H moved to the water O it
#                                 H-bonds to (contact ion pair, "CIP")
#   <acid>_ionpairSS__n<n>__c<k>  same composition, proton relayed one water further along an H-bond wire
#                                 (solvent-separated ion pair, "SSIP"), only if such a wire exists
#   hydronium__n0__c0             H3O+, charge +1 (reference for separated-ion deprotonation energy)

ANION_NAME = {"lactic_acid": "lactate", "acetic_acid": "acetate"}
ANION_SMILES = {"lactic_acid": "C[C@@H](C(=O)[O-])O", "acetic_acid": "CC(=O)[O-]"}


def carboxyl_h_index(symbols: list[str], xyz: np.ndarray, start: int, stop: int) -> tuple[int, int]:
    """(H index, O index) of the carboxylic-acid proton within atoms [start, stop)."""
    idx = range(start, stop)
    d = lambda i, j: float(np.linalg.norm(xyz[i] - xyz[j]))
    for c in idx:
        if symbols[c] != "C":
            continue
        os_ = [o for o in idx if symbols[o] == "O" and d(c, o) < 1.6]
        if len(os_) != 2:
            continue
        for o in os_:
            hs = [h for h in idx if symbols[h] == "H" and d(o, h) < 1.25]
            if hs:
                return hs[0], o
    raise ValueError("no carboxylic O-H found")


def _water_with_h_near(xyz, symbols, frags, target: np.ndarray, exclude: set[int]) -> int | None:
    best, bd = None, 1e9
    for k, f in enumerate(frags):
        if f["name"] != "water" or k in exclude:
            continue
        dd = float(np.linalg.norm(xyz[f["start"]] - target))
        if dd < bd:
            best, bd = k, dd
    return best


def _place_h(o: np.ndarray, toward: np.ndarray, r: float = 1.00) -> np.ndarray:
    u = toward - o
    return o + u / np.linalg.norm(u) * r


def _place_extra_h(o: np.ndarray, own_h: list[np.ndarray], toward: np.ndarray, others: np.ndarray,
                   r: float = 0.98, hoh_deg: float = 111.0) -> np.ndarray:
    """Add one H to oxygen `o` that already carries `own_h`.

    Directions are sampled on a 400-point Fibonacci sphere; only those within +-12 deg of the target
    H-O-H angle to every existing H are kept. Score = clearance from all `others` (capped at 2.2 A)
    + 0.3 * cos(angle to `toward`), so the new H points roughly at the acceptor without overlapping
    anything. (v1 used only the `toward` direction and could put the new H on top of an existing H.)"""
    k = np.arange(400) + 0.5
    phi = np.arccos(1 - 2 * k / 400)
    theta = np.pi * (1 + 5 ** 0.5) * k
    dirs = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)
    own = [(h - o) / np.linalg.norm(h - o) for h in own_h]
    t = (toward - o) / np.linalg.norm(toward - o)
    best, best_score = None, -1e9
    for d in dirs:
        if any(abs(np.degrees(np.arccos(np.clip(np.dot(d, u), -1, 1))) - hoh_deg) > 12 for u in own):
            continue
        pos = o + d * r
        pts = np.vstack([others, np.array(own_h)]) if len(others) else np.array(own_h)
        clear = min(2.2, float(np.min(np.linalg.norm(pts - pos, axis=1))))
        score = clear + 0.3 * float(np.dot(d, t))
        if score > best_score:
            best, best_score = pos, score
    return best


def build_derived(results_dir: str | Path, cfg: dict[str, Any]) -> list[Structure]:
    import json as _json

    results_dir = Path(results_dir)
    out: list[Structure] = []
    recs = {}
    for p in sorted(results_dir.glob("*.json")):
        r = _json.loads(p.read_text())
        if r.get("converged") and r.get("charge", 0) == 0 and r["kind"] in ("acid_monomer", "acid_water_cluster"):
            recs[r["id"]] = r
    for sid, r in recs.items():
        acid, n, c = r["acid"], int(r["n_water"]), int(r["conformer_index"])
        syms = list(r["symbols"])
        xyz = np.array(r["optimized_positions_angstrom"])
        xyz = xyz - xyz.mean(axis=0)
        frags = r["fragments"]
        a0, a1 = frags[0]["start"], frags[0]["stop"]
        h, o_acid = carboxyl_h_index(syms, xyz, a0, a1)
        xyz_sha = hashlib.sha256(np.asarray(r["optimized_positions_angstrom"]).tobytes()).hexdigest()[:16]
        base_meta = {"derived_from": sid, "derived_from_positions_sha256_16": xyz_sha}
        an_idx = [i for i in range(a0, a1) if i != h]
        anion = Fragment(ANION_NAME[acid], [syms[i] for i in an_idx], xyz[an_idx], ANION_SMILES[acid])
        waters = [Fragment("water", syms[f["start"]:f["stop"]], xyz[f["start"]:f["stop"]], "O") for f in frags[1:]]
        out.append(Structure(f"{acid}_anion__n{n}__c{c}", f"anion_{'monomer' if n == 0 else 'water_cluster'}",
                             [anion] + waters, acid, n, c, r["seeds"], charge=-1,
                             meta={**base_meta, "species": f"{ANION_NAME[acid]}(-).{n}H2O",
                                   "derivation": f"removed carboxylic H (atom {h}) from optimised {sid}"}))
        if n == 0:
            continue
        # contact ion pair: proton to the water O nearest the acid proton
        kw = _water_with_h_near(xyz, syms, frags, xyz[h], set())
        wf = frags[kw]
        ow = wf["start"]
        others = np.array([xyz[i] for i in range(len(syms)) if i != h and not (wf["start"] <= i < wf["stop"])])
        new_h = _place_extra_h(xyz[ow], [xyz[ow + 1], xyz[ow + 2]], xyz[o_acid], others)
        hyd_xyz = np.vstack([xyz[wf["start"]:wf["stop"]], new_h[None, :]])
        hyd = Fragment("hydronium", ["O", "H", "H", "H"], hyd_xyz, "[OH3+]")
        others = [Fragment("water", syms[f["start"]:f["stop"]], xyz[f["start"]:f["stop"]], "O")
                  for k, f in enumerate(frags[1:], start=1) if k != kw]
        out.append(Structure(f"{acid}_ionpair__n{n}__c{c}", "ion_pair_cluster", [anion, hyd] + others, acid, n, c,
                             r["seeds"], charge=0,
                             meta={**base_meta, "species": f"{ANION_NAME[acid]}(-).H3O(+).{n - 1}H2O (contact)",
                                   "derivation": f"moved carboxylic H (atom {h}) to water O (atom {ow}) of optimised {sid}"}))
        # solvent-separated: relay from W1 to the nearest other water W2 via W1's H closest to W2's O
        if n >= 2:
            w1 = frags[kw]
            best = None
            for k2, f2 in enumerate(frags[1:], start=1):
                if k2 == kw:
                    continue
                for hh in range(w1["start"] + 1, w1["stop"]):
                    dd = float(np.linalg.norm(xyz[hh] - xyz[f2["start"]]))
                    if dd < 2.6 and (best is None or dd < best[0]):
                        best = (dd, k2, hh)
            if best is not None:
                _, k2, hh = best
                w2 = frags[k2]
                # W1 becomes neutral water: keep its O and the H it did not give away, plus the acid proton
                keep_h = [i for i in range(w1["start"] + 1, w1["stop"]) if i != hh][0]
                o1, o2 = xyz[w1["start"]], xyz[w2["start"]]
                # the acid proton, now on W1, points back towards the carboxylate O (where the H-bond was)
                excl1 = {h, hh, keep_h} | set(range(w1["start"], w1["stop"]))
                others1 = np.array([xyz[i] for i in range(len(syms)) if i not in excl1])
                h_on_w1 = _place_extra_h(o1, [xyz[keep_h]], xyz[o_acid], others1, r=0.96, hoh_deg=104.5)
                w1_new = Fragment("water", ["O", "H", "H"], np.vstack([o1, xyz[keep_h], h_on_w1]), "O")
                excl2 = {h, hh} | set(range(w2["start"], w2["stop"]))
                others2 = np.array([xyz[i] for i in range(len(syms)) if i not in excl2] + [h_on_w1])
                new_h2 = _place_extra_h(o2, [xyz[w2["start"] + 1], xyz[w2["start"] + 2]], o1, others2)
                hyd2 = Fragment("hydronium", ["O", "H", "H", "H"],
                                np.vstack([xyz[w2["start"]:w2["stop"]], new_h2[None, :]]), "[OH3+]")
                rest = [Fragment("water", syms[f["start"]:f["stop"]], xyz[f["start"]:f["stop"]], "O")
                        for k, f in enumerate(frags[1:], start=1) if k not in (kw, k2)]
                out.append(Structure(
                    f"{acid}_ionpairSS__n{n}__c{c}", "ion_pair_cluster", [anion, hyd2, w1_new] + rest, acid, n, c,
                    r["seeds"], charge=0,
                    meta={**base_meta, "species": f"{ANION_NAME[acid]}(-).H3O(+).{n - 1}H2O (solvent-separated)",
                          "derivation": f"Grotthuss relay in optimised {sid}: acid H {h} -> water O {w1['start']}, "
                                        f"water H {hh} -> water O {w2['start']}"}))
    hyd0 = np.array([[0.0, 0.0, 0.10], [0.0, 0.94, -0.25], [0.81, -0.47, -0.25], [-0.81, -0.47, -0.25]])
    out.append(Structure("hydronium__n0__c0", "hydronium", [Fragment("hydronium", ["O", "H", "H", "H"], hyd0, "[OH3+]")],
                         None, 0, 0, {"base_seed": int(cfg["structures"]["base_seed"])}, charge=1,
                         meta={"species": "H3O(+)", "derivation": "idealised pyramidal geometry (r(OH)=0.98 A)"}))
    return out


# ----------------------------------------------------------------------------- IO


def to_xyz(st: Structure) -> str:
    lines = [str(len(st.symbols)), f"id={st.id} charge={st.charge} multiplicity={st.multiplicity}"]
    for sym, (x, y, z) in zip(st.symbols, st.coords):
        lines.append(f"{sym:2s} {x:14.8f} {y:14.8f} {z:14.8f}")
    return "\n".join(lines) + "\n"


def read_xyz(path: str | Path) -> tuple[list[str], np.ndarray, str]:
    lines = Path(path).read_text().splitlines()
    n = int(lines[0].split()[0])
    symbols, coords = [], []
    for ln in lines[2 : 2 + n]:
        parts = ln.split()
        symbols.append(parts[0])
        coords.append([float(v) for v in parts[1:4]])
    return symbols, np.array(coords), lines[1]


def formula(symbols: list[str]) -> str:
    from collections import Counter

    cnt = Counter(symbols)
    order = ["C", "H"] + sorted(k for k in cnt if k not in ("C", "H"))
    return "".join(f"{e}{cnt[e] if cnt[e] > 1 else ''}" for e in order if e in cnt)


def write_inputs(structures: list[Structure], out_dir: str | Path, cfg: dict[str, Any]) -> dict:
    from rdkit import rdBase

    out_dir = Path(out_dir)
    xyz_dir = out_dir / "xyz"
    xyz_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for st in structures:
        rel = f"xyz/{st.id}.xyz"
        text = to_xyz(st)
        (out_dir / rel).write_text(text)
        entries.append(
            {
                "id": st.id,
                "kind": st.kind,
                "acid": st.acid,
                "n_water": st.n_water,
                "conformer_index": st.conformer_index,
                "seeds": st.seeds,
                "charge": st.charge,
                "multiplicity": st.multiplicity,
                "n_atoms": len(st.symbols),
                "formula": formula(st.symbols),
                "fragments": st.fragment_ranges(),
                "xyz": rel,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                **st.meta,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "provenance": "simulated: RDKit ETKDGv3 + seeded H-bond placement (starting geometries, not ALCHEMI output)",
        "generator": "fermentai.molecular.structures",
        "rdkit_version": rdBase.rdkitVersion,
        "numpy_version": np.__version__,
        "config": cfg["structures"],
        "lactic_acid_cip": cip_label(cfg["structures"]["acids"]["lactic_acid"]["smiles"])
        if "lactic_acid" in cfg["structures"]["acids"]
        else None,
        "units": {"coordinates": "angstrom"},
        "structures": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default=None, help="output dir (default: config structures.output_dir)")
    ap.add_argument("--derived-from", default=None,
                    help="stage 2: also derive anions/ion pairs from optimised neutral results in this dir "
                         "(e.g. data/alchemi/results/json)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    out = Path(args.out) if args.out else REPO_ROOT / cfg["structures"]["output_dir"]
    structures = build_all(cfg)
    if args.derived_from:
        structures += build_derived(args.derived_from, cfg)
    manifest = write_inputs(structures, out, cfg)
    kinds: dict[str, int] = {}
    for e in manifest["structures"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"wrote {len(structures)} structures to {out}: {kinds}")


if __name__ == "__main__":
    main()
