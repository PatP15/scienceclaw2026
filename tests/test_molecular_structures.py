"""Mac-side tests for fermentai.molecular.structures (no GPU needed)."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from fermentai.molecular import structures as st


@pytest.fixture(scope="module")
def small_cfg():
    cfg = copy.deepcopy(st.load_config())
    s = cfg["structures"]
    s["n_conformers_acid"] = 2
    s["n_conformers_cluster"] = 2
    s["n_conformers_water_cluster"] = 1
    return cfg


def test_lactic_acid_is_L_S_configuration():
    cfg = st.load_config()
    assert st.cip_label(cfg["structures"]["acids"]["lactic_acid"]["smiles"]) == "S"


def test_explicit_hydrogens_and_formulas(small_cfg):
    structs = {s.id: s for s in st.build_all(small_cfg)}
    assert st.formula(structs["lactic_acid__n0__c0"].symbols) == "C3H6O3"
    assert st.formula(structs["acetic_acid__n0__c0"].symbols) == "C2H4O2"
    assert st.formula(structs["water__n1__c0"].symbols) == "H2O"
    assert st.formula(structs["lactic_acid__n6__c1"].symbols) == "C3H18O9"
    assert st.formula(structs["acetic_acid__n3__c0"].symbols) == "C2H10O5"


def test_expected_structure_set(small_cfg):
    structs = st.build_all(small_cfg)
    ids = {s.id for s in structs}
    for acid in ("lactic_acid", "acetic_acid"):
        for n in (1, 3, 6):
            assert f"{acid}__n{n}__c0" in ids
        assert f"{acid}__n0__c1" in ids
    assert "water__n1__c0" in ids
    assert "water_cluster__n3__c0" in ids and "water_cluster__n6__c0" in ids


def test_generation_is_deterministic(small_cfg):
    a = st.build_all(small_cfg)
    b = st.build_all(small_cfg)
    assert [s.id for s in a] == [s.id for s in b]
    for x, y in zip(a, b):
        assert x.symbols == y.symbols
        np.testing.assert_allclose(x.coords, y.coords, atol=1e-10)
        assert x.seeds == y.seeds


def test_seeds_differ_between_conformers(small_cfg):
    structs = {s.id: s for s in st.build_all(small_cfg)}
    a, b = structs["lactic_acid__n3__c0"], structs["lactic_acid__n3__c1"]
    assert a.seeds["placement_seed"] != b.seeds["placement_seed"]
    assert not np.allclose(a.coords, b.coords)


def test_no_intermolecular_clashes(small_cfg):
    p = small_cfg["structures"]["placement"]
    for s in st.build_all(small_cfg):
        ranges = s.fragment_ranges()
        xyz, sym = s.coords, s.symbols
        for i, fi in enumerate(ranges):
            for fj in ranges[i + 1 :]:
                for a in range(fi["start"], fi["stop"]):
                    for b in range(fj["start"], fj["stop"]):
                        d = np.linalg.norm(xyz[a] - xyz[b])
                        lim = p["min_any_h_angstrom"] if "H" in (sym[a], sym[b]) else p["min_heavy_heavy_angstrom"]
                        assert d >= lim - 1e-9, (s.id, a, b, d)


def test_water_geometry_preserved(small_cfg):
    for s in st.build_all(small_cfg):
        for fr in s.fragment_ranges():
            if fr["name"] != "water":
                continue
            o, h1, h2 = s.coords[fr["start"] : fr["stop"]]
            assert np.linalg.norm(h1 - o) == pytest.approx(0.9572, abs=1e-6)
            ang = np.degrees(np.arccos(np.dot(h1 - o, h2 - o) / (np.linalg.norm(h1 - o) * np.linalg.norm(h2 - o))))
            assert ang == pytest.approx(104.52, abs=1e-4)


def test_manifest_and_xyz_roundtrip(small_cfg, tmp_path):
    structs = st.build_all(small_cfg)
    manifest = st.write_inputs(structs, tmp_path, small_cfg)
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk == json.loads(json.dumps(manifest))
    assert len(on_disk["structures"]) == len(structs)
    for ent, s in zip(on_disk["structures"], structs):
        assert ent["charge"] == 0 and ent["multiplicity"] == 1
        assert ent["n_atoms"] == len(s.symbols)
        assert sum(f["stop"] - f["start"] for f in ent["fragments"]) == ent["n_atoms"]
        assert ent["fragments"][0]["name"] == (s.acid or "water")
        assert sum(1 for f in ent["fragments"] if f["name"] == "water") == ent["n_water"]
        syms, xyz, comment = st.read_xyz(tmp_path / ent["xyz"])
        assert syms == s.symbols
        np.testing.assert_allclose(xyz, s.coords, atol=1e-7)
        assert f"id={s.id}" in comment
        assert "seeds" in ent and "base_seed" in ent["seeds"]
    # second write is byte-identical (no timestamps)
    first = (tmp_path / "manifest.json").read_bytes()
    st.write_inputs(st.build_all(small_cfg), tmp_path, small_cfg)
    assert (tmp_path / "manifest.json").read_bytes() == first


def test_repo_inputs_match_generator_if_present():
    """If data/alchemi/inputs exists it must be what the generator produces (provenance check)."""
    path = st.REPO_ROOT / "data" / "alchemi" / "inputs" / "manifest.json"
    if not path.exists():
        pytest.skip("inputs not generated")
    man = json.loads(path.read_text())
    cfg = st.load_config()
    base = st.build_all(cfg)
    ids = [s.id for s in base]
    # stage 1 (RDKit-generated) entries come first and must match the generator exactly
    assert [e["id"] for e in man["structures"][: len(ids)]] == ids
    for e, s in zip(man["structures"], base):
        assert e["sha256"] == __import__("hashlib").sha256(st.to_xyz(s).encode()).hexdigest()
    # stage 2 entries (anions / ion pairs / hydronium) are derived from optimised ALCHEMI results
    for e in man["structures"][len(ids):]:
        assert e["kind"] in ("anion_monomer", "anion_water_cluster", "ion_pair_cluster", "hydronium")
        assert "derivation" in e
        if e["kind"] != "hydronium":
            assert e["derived_from"] in ids


def _stub_results_from_inputs(cfg, tmp_path):
    """Package generator geometries as if they were optimised results (TEST FIXTURE, no energies)."""
    d = tmp_path / "res"
    d.mkdir()
    for s in st.build_all(cfg):
        if s.kind not in ("acid_monomer", "acid_water_cluster"):
            continue
        rec = {"id": s.id, "kind": s.kind, "acid": s.acid, "n_water": s.n_water, "conformer_index": s.conformer_index,
               "seeds": s.seeds, "charge": 0, "converged": True, "symbols": s.symbols,
               "fragments": s.fragment_ranges(), "optimized_positions_angstrom": s.coords.tolist()}
        (d / f"{s.id}.json").write_text(json.dumps(rec))
    return d


def test_derived_species_composition_and_charge(small_cfg, tmp_path):
    from collections import Counter

    d = _stub_results_from_inputs(small_cfg, tmp_path)
    der = {s.id: s for s in st.build_derived(d, small_cfg)}
    base = {s.id: s for s in st.build_all(small_cfg)}
    assert der["hydronium__n0__c0"].charge == 1 and st.formula(der["hydronium__n0__c0"].symbols) == "H3O"
    for acid in ("lactic_acid", "acetic_acid"):
        a0 = der[f"{acid}_anion__n0__c0"]
        assert a0.charge == -1 and len(a0.symbols) == len(base[f"{acid}__n0__c0"].symbols) - 1
        for n in (1, 3, 6):
            neutral = base[f"{acid}__n{n}__c0"]
            an = der[f"{acid}_anion__n{n}__c0"]
            ip = der[f"{acid}_ionpair__n{n}__c0"]
            assert an.charge == -1 and ip.charge == 0
            assert Counter(ip.symbols) == Counter(neutral.symbols)  # matched composition
            assert Counter(an.symbols) == Counter(neutral.symbols) - Counter({"H": 1})
            names = [f.name for f in ip.fragments]
            assert names[0] in ("lactate", "acetate") and names[1] == "hydronium" and names.count("water") == n - 1
            assert ip.seeds == neutral.seeds and ip.meta["derived_from"] == neutral.id
            # carboxylate has no H on its oxygens
            frag0 = ip.fragment_ranges()[0]
            with pytest.raises(ValueError):
                st.carboxyl_h_index(ip.symbols, ip.coords, frag0["start"], frag0["stop"])


def test_protonation_state_classifier(small_cfg, tmp_path):
    from fermentai.molecular import analysis as an

    d = _stub_results_from_inputs(small_cfg, tmp_path)
    der = {s.id: s for s in st.build_derived(d, small_cfg)}
    base = {s.id: s for s in st.build_all(small_cfg)}
    neu = base["acetic_acid__n3__c0"]
    assert an.protonation_state(neu.symbols, neu.coords, neu.fragment_ranges())["state"] == "neutral_acid"
    a = der["acetic_acid_anion__n3__c0"]
    assert an.protonation_state(a.symbols, a.coords, a.fragment_ranges())["state"] == "anion"
    ip = der["acetic_acid_ionpair__n3__c0"]
    assert an.protonation_state(ip.symbols, ip.coords, ip.fragment_ranges())["state"] in (
        "contact_ion_pair", "solvent_separated_ion_pair")


def test_derived_structures_have_no_atom_overlaps(small_cfg, tmp_path):
    """Regression for v1 ion-pair bug: the transferred proton landed ~0.1 A from an existing H."""
    d = _stub_results_from_inputs(small_cfg, tmp_path)
    for s in st.build_derived(d, small_cfg):
        x = s.coords
        dm = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1) + np.eye(len(x)) * 99
        assert dm.min() > 0.85, (s.id, float(dm.min()))
