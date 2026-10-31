"""Mac-side tests for fermentai.molecular.analysis using a tiny SYNTHETIC fixture.

The energies below are made-up test numbers (not ALCHEMI output) and are written only to pytest's
tmp_path; nothing here touches data/alchemi/.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from fermentai.molecular import analysis as an

WATER = np.array([[0.0, 0.0, 0.0], [0.7572, 0.0, 0.5865], [-0.7572, 0.0, 0.5865]])


def _water_dimer():
    """Donor water H1 points along +x toward acceptor O at 2.90 A (linear H-bond)."""
    o1 = np.array([0.0, 0.0, 0.0])
    h1 = np.array([0.9572, 0.0, 0.0])
    h2 = o1 + 0.9572 * np.array([np.cos(np.radians(104.52)), np.sin(np.radians(104.52)), 0.0])
    o2 = np.array([2.90, 0.0, 0.0])
    h3 = o2 + np.array([0.3, 0.0, 0.91])
    h4 = o2 + np.array([0.3, 0.0, -0.91])
    return np.array([o1, h1, h2, o2, h3, h4])


def _acetic():
    # planar acetic acid (approximate geometry): C1 methyl, C2 carboxyl, O3 carbonyl, O4 hydroxyl
    return (["C", "C", "O", "O", "H", "H", "H", "H"],
            np.array([[-1.50, 0.0, 0.0], [0.0, 0.0, 0.0], [0.60, 1.05, 0.0], [0.65, -1.15, 0.0],
                      [1.60, -1.00, 0.0], [-1.85, 1.03, 0.0], [-1.85, -0.5, 0.89], [-1.85, -0.5, -0.89]]))


def _rec(id_, kind, acid, n, conf, syms, xyz, frags, e, converged=True):
    return {"id": id_, "kind": kind, "acid": acid, "n_water": n, "conformer_index": conf, "formula": "x",
            "n_atoms": len(syms), "status": "converged" if converged else "not_converged", "converged": converged,
            "energy_ev": e, "energy_hartree": e / 27.211386, "max_force_ev_per_angstrom": 0.005 if converged else 0.5,
            "iterations": 10, "wall_seconds": 1.0, "symbols": syms, "fragments": frags,
            "optimized_positions_angstrom": np.asarray(xyz).tolist(), "initial_positions_angstrom": np.asarray(xyz).tolist(),
            "charges_e": [0.0] * len(syms), "seeds": {"base_seed": 1},
            "method": {"optimizer": "FIRE2", "checkpoint": "aimnet2_wb97m_d3_3"},
            "versions": {"gpu": "TEST-FIXTURE"}, "provenance": "TEST FIXTURE (synthetic)"}


@pytest.fixture()
def fixture_dir(tmp_path):
    d = tmp_path / "json"
    d.mkdir()
    asyms, axyz = _acetic()
    w = ["O", "H", "H"]
    shifted = WATER + np.array([2.4, -1.6, 0.0])  # water O near acetic hydroxyl H
    cl_syms = asyms + w
    cl_xyz = np.vstack([axyz, shifted])
    frags_cl = [{"name": "acetic_acid", "start": 0, "stop": 8}, {"name": "water", "start": 8, "stop": 11}]
    recs = [
        _rec("water__n1__c0", "water_monomer", None, 1, 0, w, WATER, [{"name": "water", "start": 0, "stop": 3}], -10.0),
        _rec("acetic_acid__n0__c0", "acid_monomer", "acetic_acid", 0, 0, asyms, axyz,
             [{"name": "acetic_acid", "start": 0, "stop": 8}], -50.0),
        _rec("acetic_acid__n0__c1", "acid_monomer", "acetic_acid", 0, 1, asyms, axyz,
             [{"name": "acetic_acid", "start": 0, "stop": 8}], -50.1),
        _rec("acetic_acid__n1__c0", "acid_water_cluster", "acetic_acid", 1, 0, cl_syms, cl_xyz, frags_cl, -60.5),
        _rec("acetic_acid__n1__c1", "acid_water_cluster", "acetic_acid", 1, 1, cl_syms, cl_xyz, frags_cl, -60.4),
        _rec("acetic_acid__n1__c2", "acid_water_cluster", "acetic_acid", 1, 2, cl_syms, cl_xyz, frags_cl, -70.0,
             converged=False),
        _rec("water_cluster__n2__c0", "water_cluster", None, 2, 0, w + w, _water_dimer(),
             [{"name": "water", "start": 0, "stop": 3}, {"name": "water", "start": 3, "stop": 6}], -20.2),
    ]
    for r in recs:
        (d / f"{r['id']}.json").write_text(json.dumps(r))
    return d


def test_water_dimer_hbond_detected():
    xyz = _water_dimer()
    frags = [{"name": "water", "start": 0, "stop": 3}, {"name": "water", "start": 3, "stop": 6}]
    hbs = an.hydrogen_bonds(["O", "H", "H", "O", "H", "H"], xyz, frags)
    assert len(hbs) == 1
    hb = hbs[0]
    assert (hb["donor"], hb["hydrogen"], hb["acceptor"]) == (0, 1, 3)
    assert hb["type"] == "water->water"
    assert hb["d_DA_angstrom"] == pytest.approx(2.90)
    assert hb["angle_DHA_deg"] == pytest.approx(180.0, abs=1e-6)


def test_hbond_rejected_when_too_far_or_bent():
    xyz = _water_dimer()
    xyz[3:] += np.array([1.0, 0, 0])  # O...O 3.9 A
    frags = [{"name": "water", "start": 0, "stop": 3}, {"name": "water", "start": 3, "stop": 6}]
    assert an.hydrogen_bonds(["O", "H", "H", "O", "H", "H"], xyz, frags) == []


def test_acid_site_classification():
    syms, xyz = _acetic()
    fidx = np.zeros(len(syms), dtype=int)
    assert an._acid_o_site(syms, xyz, 2, fidx) == "carbonyl_O"
    assert an._acid_o_site(syms, xyz, 3, fidx) == "carboxyl_OH"


def test_covalent_bonds_water():
    assert sorted(an.covalent_bonds(["O", "H", "H"], WATER)) == [(0, 1), (0, 2)]


def test_references_and_association(fixture_dir):
    df = an.energy_table(an.load_results(fixture_dir))
    ref = an.references(df)
    assert ref["water"]["energy_ev"] == -10.0
    assert ref["acetic_acid"]["energy_ev"] == -50.1  # lowest conformer is the reference
    assert ref["acetic_acid"]["id"] == "acetic_acid__n0__c1"
    assoc = an.association_table(df, ref)
    best = assoc[assoc["is_lowest_energy_conformer"]]
    assert list(best["id"]) == ["acetic_acid__n1__c0"]  # unconverged -70 eV structure is excluded
    de = best.iloc[0]["dE_assoc_ev"]
    assert de == pytest.approx(-60.5 - (-50.1) - (-10.0))
    assert best.iloc[0]["dE_assoc_kcal_mol"] == pytest.approx(de * an.EV_TO_KCAL)
    nc = assoc[assoc["id"] == "acetic_acid__n1__c2"].iloc[0]
    assert not nc["converged"] and (pd.isna(nc.get("dE_assoc_ev")))


def test_relative_energies(fixture_dir):
    df = an.energy_table(an.load_results(fixture_dir)).set_index("id")
    assert df.loc["acetic_acid__n0__c1", "relative_energy_kcal_mol"] == pytest.approx(0.0)
    assert df.loc["acetic_acid__n0__c0", "relative_energy_kcal_mol"] == pytest.approx(0.1 * an.EV_TO_KCAL)


def test_full_run_writes_outputs(fixture_dir, tmp_path):
    out = tmp_path / "analysis"
    digest = an.run(fixture_dir, out, figures=True, md_dir=tmp_path / "no_md")
    for f in ("energy_summary.csv", "association_energies.csv", "hbonds.csv", "hbond_summary.csv",
              "analysis_summary.json", "association_energy.html", "molecular_explorer.html",
              "association_energy.png", "association_energy_per_water.png"):
        assert (out / f).exists(), f
    assert digest["n_calculations"] == 7 and digest["n_failed"] == 1
    assert digest["provenance"] == "real: ALCHEMI/AIMNet2 on TEST-FIXTURE"
    html = (out / "molecular_explorer.html").read_text()
    import re

    assert re.findall(r"<script[^>]*\ssrc=", html) == []  # self-contained: no external scripts / CDN
    assert len(html) > 1_000_000  # plotly.js is inlined
    hb = pd.read_csv(out / "hbond_summary.csv").set_index("id")
    assert hb.loc["water_cluster__n2__c0", "n_hbonds"] == 1
    assert hb.loc["acetic_acid__n1__c0", "n_intermolecular"] >= 1
    s = json.loads((out / "analysis_summary.json").read_text())
    assert s["lowest_energy_clusters"][0]["id"] == "acetic_acid__n1__c0"
    assert not math.isnan(s["lowest_energy_clusters"][0]["dE_assoc_kcal_mol"])


def test_deprotonation_and_descriptors(fixture_dir, tmp_path):
    """Synthetic charged species: anion.H2O, ion pair, hydronium (made-up energies)."""
    asyms, axyz = _acetic()
    keep = [i for i in range(8) if i != 4]  # drop carboxylic H (index 4)
    an_syms = [asyms[i] for i in keep] + ["O", "H", "H"]
    an_xyz = np.vstack([axyz[keep], WATER + np.array([2.4, -1.6, 0.0])])
    fr_an = [{"name": "acetate", "start": 0, "stop": 7}, {"name": "water", "start": 7, "stop": 10}]
    hyd = np.array([[0.0, 0.0, 0.10], [0.0, 0.94, -0.25], [0.81, -0.47, -0.25], [-0.81, -0.47, -0.25]])
    ip_syms = [asyms[i] for i in keep] + ["O", "H", "H", "H"]
    ip_xyz = np.vstack([axyz[keep], hyd + np.array([2.2, -2.2, 0.0])])
    fr_ip = [{"name": "acetate", "start": 0, "stop": 7}, {"name": "hydronium", "start": 7, "stop": 11}]
    extra = [
        dict(_rec("acetic_acid_anion__n1__c0", "anion_water_cluster", "acetic_acid", 1, 0, an_syms, an_xyz, fr_an, -45.0),
             charge=-1),
        _rec("acetic_acid_ionpair__n1__c0", "ion_pair_cluster", "acetic_acid", 1, 0, ip_syms, ip_xyz, fr_ip, -60.0),
        dict(_rec("hydronium__n0__c0", "hydronium", None, 0, 0, ["O", "H", "H", "H"], hyd,
                  [{"name": "hydronium", "start": 0, "stop": 4}], -11.0), charge=1),
    ]
    for r in extra:
        (fixture_dir / f"{r['id']}.json").write_text(json.dumps(r))
    recs = an.load_results(fixture_dir)
    df = an.energy_table(recs)
    ref = an.references(df)
    states, dep = an.ionic_tables(recs, df, ref)
    st = states.set_index("id")
    assert st.loc["acetic_acid_anion__n1__c0", "final_state"] == "anion"
    assert st.loc["acetic_acid_ionpair__n1__c0", "final_state"] in ("contact_ion_pair", "solvent_separated_ion_pair")
    row = dep[(dep["acid"] == "acetic_acid") & (dep["n_water"] == 1)].iloc[0]
    # separated: E(anion.H2O) + E(H3O+) - E(acid.H2O) - E(H2O) = -45 - 11 + 60.5 + 10
    assert row["dE_deprot_separated_ions_kcal_mol"] == pytest.approx((-45 - 11 + 60.5 + 10) * an.EV_TO_KCAL)
    assert row["dE_deprot_ion_pair_kcal_mol"] == pytest.approx((-60.0 + 60.5) * an.EV_TO_KCAL)
    out = tmp_path / "out"
    an.run(fixture_dir, out, figures=True, md_dir=tmp_path / "no_md")
    desc = json.loads((out / "molecular_descriptors.json").read_text())
    assert desc["acetic_acid"]["deprot_energy_n1_kcal"] == pytest.approx(0.5 * an.EV_TO_KCAL)
    assert "not a solution-phase pKa" in desc["provenance"]["note"]
    assert (out / "deprotonation_energy.png").exists() and (out / "deprotonation_energy.html").exists()


def test_json_clean_and_dump_strict(tmp_path):
    obj = {"a": float("nan"), "b": [np.float64("inf"), 1.5, np.int64(3)], "c": {"d": np.nan, "e": np.bool_(True)}}
    c = an.json_clean(obj)
    assert c == {"a": None, "b": [None, 1.5, 3], "c": {"d": None, "e": True}}
    p = tmp_path / "x.json"
    an.dump_json(p, obj)
    assert "NaN" not in p.read_text() and "Infinity" not in p.read_text()
    json.loads(p.read_text())
