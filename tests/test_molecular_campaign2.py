"""Tests for campaign-2 Mac-side code (structures_c2, campaign2). SYNTHETIC data only; no GPU."""

from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd
import pytest

from fermentai.molecular import campaign2 as c2
from fermentai.molecular import structures as st
from fermentai.molecular import structures_c2 as s2


@pytest.fixture(scope="module")
def small_cfg():
    cfg = copy.deepcopy(st.load_config())
    c = cfg["campaign2"]
    c["sampling"]["n_starts"] = 2
    c["large"]["n_starts"] = 1
    c["large"]["water_cluster_starts"] = 1
    c["cosolute_starts"] = 1
    c["cosolute_monomer_starts"] = 1
    c["dimer_starts"] = 1
    return cfg


@pytest.fixture(scope="module")
def built(small_cfg):
    return {s.id: s for s in s2.build_campaign2(small_cfg)}


def test_c2_build_deterministic(small_cfg, built):
    again = {s.id: s for s in s2.build_campaign2(small_cfg)}
    assert list(again) == list(built)
    for k in built:
        np.testing.assert_allclose(again[k].coords, built[k].coords, atol=1e-10)


def test_c2_charges_and_compositions(built):
    from collections import Counter

    assert built["lactic_acid_anion__n6__s00"].charge == -1
    assert built["acetic_acid__n6__s00"].charge == 0
    assert built["hydronium__n0__s00"].charge == 1
    a, b = built["lactic_acid__n6__s00"], built["lactic_acid_anion__n6__s00"]
    assert Counter(a.symbols) - Counter(b.symbols) == Counter({"H": 1})
    assert st.formula(built["acetic_acid__n15__s00"].symbols) == "C2H34O17"
    names = [f.name for f in built["lactic_acid__gln_gln__n3__s00"].fragments]
    assert names == ["lactic_acid", "gln_gln", "water", "water", "water"]
    assert "N" in built["acetic_acid__gln_gln__n3__s00"].symbols
    assert [f.name for f in built["lactic_acid_anion__n3__s00"].fragments][0] == "lactate"


def test_c2_no_clashes(small_cfg, built):
    p = small_cfg["structures"]["placement"]
    for s in built.values():
        rng = s.fragment_ranges()
        x, sy = s.coords, s.symbols
        for i, fi in enumerate(rng):
            for fj in rng[i + 1:]:
                d = np.linalg.norm(x[fi["start"]:fi["stop"], None] - x[None, fj["start"]:fj["stop"]], axis=-1)
                for a in range(d.shape[0]):
                    for b in range(d.shape[1]):
                        lim = p["min_any_h_angstrom"] if "H" in (sy[fi["start"] + a], sy[fj["start"] + b]) \
                            else p["min_heavy_heavy_angstrom"]
                        assert d[a, b] >= lim - 1e-9, (s.id, d[a, b])


def test_cyclic_dimer_geometry(built):
    s = built["acetic_acid__acetic_acid__n0__cyc"]
    o = [i for i, e in enumerate(s.symbols) if e == "O"]
    x = s.coords
    inter = sorted(np.linalg.norm(x[i] - x[j]) for i in o[:2] for j in o[2:])
    assert inter[0] == pytest.approx(2.68, abs=0.05) and inter[1] == pytest.approx(2.68, abs=0.05)


# ------------------------------------------------------------------ analysis helpers


def test_unique_minima_and_boltzmann():
    e = np.array([0.0, 0.004, 1.0, 1.0, 5.0])
    u = c2.unique_minima(e)
    assert list(u) == [0.0, 1.0, 5.0]
    f = c2.boltzmann_free_energy(e)
    expect = -c2.RT * math.log(1 + math.exp(-1 / c2.RT) + math.exp(-5 / c2.RT))
    assert f == pytest.approx(expect)
    assert c2.boltzmann_free_energy(np.array([3.0])) == 3.0


def _pool(spec):
    rows = []
    for sp, n, energies in spec:
        for k, e in enumerate(energies):
            rows.append({"id": f"{sp}__n{n}__s{k}", "species": sp, "n_water": n, "kind": "x",
                         "energy_ev": e / c2.EV_TO_KCAL, "usable": True, "origin": "seeded_start"})
    return pd.DataFrame(rows)


def test_relative_deprotonation_cancels_references():
    pool = _pool([("lactic_acid", 6, [100.0, 101.0]), ("lactic_acid_anion", 6, [80.0, 82.0]),
                  ("acetic_acid", 6, [50.0, 50.5]), ("acetic_acid_anion", 6, [32.0, 33.0])])
    st_ = c2.species_table(pool)
    d = c2.relative_deprotonation(st_, pool, 6, n_boot=200)
    assert d["ddE_kcal"] == pytest.approx((80 - 100) - (32 - 50))
    assert d["ddE_bootstrap_sd_kcal"] >= 0
    lo, hi = d["ddE_bootstrap_ci95_kcal"]
    assert lo <= d["ddE_kcal"] + 2.0 and hi >= d["ddE_kcal"] - 2.0


def test_thermo_cycle_and_relative_pka():
    vib = pd.DataFrame([
        {"species": "lactic_acid", "n_water": 6, "G_qh_kcal": 10.0, "n_imag": 0},
        {"species": "lactic_acid_anion", "n_water": 6, "G_qh_kcal": 5.0, "n_imag": 0},
        {"species": "acetic_acid", "n_water": 6, "G_qh_kcal": 20.0, "n_imag": 0},
        {"species": "acetic_acid_anion", "n_water": 6, "G_qh_kcal": 16.0, "n_imag": 0},
    ])
    cyc = c2.thermo_cycle(vib, 6)
    assert cyc["dd_lowest_kcal"] == pytest.approx(-1.0)
    pka = c2.relative_pka([cyc], [{"n_water": 6, "ddE_bootstrap_sd_kcal": 0.5}])
    assert pka["value"] == pytest.approx(-1.0 / c2.RT_LN10)
    assert pka["experiment"] == pytest.approx(-0.90)
    assert pka["uncertainty"] == pytest.approx(0.5 / c2.RT_LN10)
    assert "not an absolute pKa" in pka["note"]
    assert c2.relative_pka([], [])["value"] is None


def test_association_and_pairs():
    pool = _pool([("water", 1, [10.0]), ("acetic_acid", 0, [50.0, 50.3]), ("acetic_acid", 3, [75.0]),
                  ("water", 3, [28.0]), ("acetic_acid+acetic_acid", 0, [85.0]), ("lactic_acid", 0, [70.0]),
                  ("lactic_acid+acetic_acid", 0, [104.0])])
    st_ = c2.species_table(pool)
    a = c2.association_by_n(st_).set_index("species")
    assert a.loc["acetic_acid", "dE_assoc_kcal"] == pytest.approx(75 - 50 - 30)
    assert a.loc["acetic_acid", "dE_to_water_network_kcal"] == pytest.approx(75 - 50 - 28)
    pr = c2.pair_energetics(st_)
    assert pr["acetic_dimer_kcal"] == pytest.approx(-15.0)
    assert pr["lactic_acetic_dimer_kcal"] == pytest.approx(104 - 70 - 50)


def test_lifetimes_and_acf():
    m = np.array([[1, 0], [1, 1], [0, 1], [1, 1], [1, 0]], dtype=bool)
    lt = c2.continuous_lifetimes(m, 0.01)
    assert sorted(np.round(lt, 4)) == [0.02, 0.02, 0.03]
    acf = c2.intermittent_acf(m, 3)
    assert acf[0] == pytest.approx(1.0)
    assert np.all(acf <= 1.0 + 1e-12)
    assert np.all(c2.intermittent_acf(np.zeros((4, 1), bool), 2) == 0)


def test_species_key():
    assert c2.species_key({"kind": "anion_water_cluster", "acid": "lactic_acid"}) == "lactic_acid_anion"
    assert c2.species_key({"kind": "cosolute_cluster", "acid": "acetic_acid",
                           "input_meta": {"cosolute": "ethanol"}}) == "acetic_acid+ethanol"
    assert c2.species_key({"kind": "water_cluster"}) == "water"


def test_job_selection():
    pool = _pool([("lactic_acid", 3, [5.0, 1.0, 1.004, 2.0, 9.0]), ("acetic_acid_anion", 6, [0.0, 0.5])])
    ids = c2.top_unique_ids(pool, "lactic_acid", 3, k=3)
    assert ids == ["lactic_acid__n3__s1", "lactic_acid__n3__s3", "lactic_acid__n3__s0"]
    assert c2.top_unique_ids(pool, "lactic_acid", 3, k=5, window_kcal=1.5) == ["lactic_acid__n3__s1", "lactic_acid__n3__s3"]
    groups = c2.anneal_groups(pool, ns=(3, 6), k=2)
    assert set(groups) == {"lactic_acid__n3", "acetic_acid_anion__n6"}
    v = c2.vib_ids(pool, ns=(3,), k=1)
    assert v[:2] == ["water__n1__s00", "hydronium__n0__s00"] and "lactic_acid__n3__s1" in v


def test_dedupe_vib_geometry_and_energy():
    x = np.array([[0.0, 0, 0], [1.0, 0, 0], [0, 1.2, 0]])
    y = x + 0.001
    z = np.array([[0.0, 0, 0], [1.5, 0, 0], [0, 1.2, 0]])
    fp = c2.distance_fingerprint
    vib = pd.DataFrame([
        {"id": "a", "species": "acetic_acid", "n_water": 0, "E_tight_kcal": 0.0, "G_qh_kcal": 10.00, "fingerprint": fp(x)},
        {"id": "b", "species": "acetic_acid", "n_water": 0, "E_tight_kcal": 0.03, "G_qh_kcal": 10.02, "fingerprint": fp(y)},
        {"id": "c", "species": "acetic_acid", "n_water": 0, "E_tight_kcal": 0.01, "G_qh_kcal": 10.05, "fingerprint": fp(z)},
        {"id": "d", "species": "acetic_acid", "n_water": 0, "E_tight_kcal": 2.00, "G_qh_kcal": 12.00, "fingerprint": fp(x)},
    ])
    out = c2.dedupe_vib(vib).set_index("id")
    assert out.loc["b", "duplicate_of"] == "a"
    assert out.loc["c", "duplicate_of"] is None or pd.isna(out.loc["c", "duplicate_of"])  # different geometry
    assert pd.isna(out.loc["d", "duplicate_of"]) or out.loc["d", "duplicate_of"] is None  # different energy
