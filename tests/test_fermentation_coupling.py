"""Upstream (ALCHEMI descriptors) and downstream (crumb morphology) coupling of the kinetic model.

Descriptor files here are synthetic test fixtures written to tmp_path, NOT ALCHEMI results.
"""

import json
import math

import numpy as np
import pytest

from fermentai.fermentation import kinetics as K
from fermentai.fermentation.simulation import (
    TemperatureSchedule,
    simulate,
    summarize_for_morphology,
)

TARGETS = ("undissociated_lactic_rel_potency", "pka_lactic", "pka_acetic")


def _write(tmp_path, obj, name="molecular_descriptors.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return p


FULL = {
    "provenance": "test-fixture",
    "acids": {
        "lactic_acid": {
            "assoc_energy_per_water_kcal": -9.0,
            "deprot_energy_n6_kcal": 14.0,
            "hbond_min_A": 1.62,
        },
        "acetic_acid": {
            "assoc_energy_per_water_kcal": -7.5,
            "deprot_energy_n6_kcal": 16.5,
            "hbond_min_A": 1.70,
        },
    },
}


def _in_range(p):
    specs = K.parameter_specs()
    for n in TARGETS:
        assert math.isfinite(p[n])
        assert specs[n].min - 1e-12 <= p[n] <= specs[n].max + 1e-12, n


def test_literature_sources_explicit():
    specs = K.parameter_specs()
    assert specs["pka_lactic"].source == "literature"
    assert specs["pka_acetic"].source == "literature"
    assert specs["hac_inhib_yeast"].source == "literature"
    assert specs["undissociated_lactic_rel_potency"].source == "assumption"


def test_absent_file_returns_defaults(tmp_path):
    p, rep = K.apply_molecular_descriptors(None, path=tmp_path / "nope.json", return_report=True)
    assert rep["status"] == "absent"
    d = K.resolve_params()
    for n in TARGETS:
        assert p[n] == d[n]
        assert rep["parameters"][n]["source"] in ("literature", "assumption")


def test_full_file_applies_bounded_relative_changes(tmp_path):
    path = _write(tmp_path, FULL)
    p, rep = K.apply_molecular_descriptors(None, path=path, return_report=True)
    assert rep["status"] == "applied"
    _in_range(p)
    d = K.resolve_params()
    for n in TARGETS:
        info = rep["parameters"][n]
        assert info["source"] == "alchemi-informed"
        assert info["literature_value"] == d[n]
    # lactic more strongly hydrated + shorter H-bond -> lower relative potency
    assert p["undissociated_lactic_rel_potency"] < d["undissociated_lactic_rel_potency"]
    # pKa gap moves by at most 0.05
    gap0 = d["pka_lactic"] - d["pka_acetic"]
    assert abs((p["pka_lactic"] - p["pka_acetic"]) - gap0) <= 0.05 + 1e-9
    # and the model still runs sensibly with these parameters
    df = simulate(TemperatureSchedule.constant(27), params={n: p[n] for n in TARGETS}, t_end_h=12)
    assert 3.5 < df.ph.iloc[-1] < 4.5


def test_partial_file_only_changes_what_it_can(tmp_path):
    path = _write(
        tmp_path,
        {
            "lactic_acid": {"assoc_energy_per_water_kcal": -9.0},
            "acetic_acid": {"assoc_energy_per_water_kcal": -7.5, "deprot_energy_n6_kcal": 16.5},
        },
    )
    p, rep = K.apply_molecular_descriptors(None, path=path, return_report=True)
    assert rep["status"] == "partial"
    d = K.resolve_params()
    assert rep["parameters"]["undissociated_lactic_rel_potency"]["source"] == "alchemi-informed"
    assert p["pka_lactic"] == d["pka_lactic"] and p["pka_acetic"] == d["pka_acetic"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        {"acids": {"lactic_acid": {"assoc_energy_per_water_kcal": "NaN"}, "acetic_acid": {}}},
        {"something_else": [1, 2, 3]},
        [],
    ],
)
def test_garbage_never_produces_nonsense(tmp_path, content):
    path = _write(tmp_path, content)
    p, rep = K.apply_molecular_descriptors(None, path=path, return_report=True)
    assert rep["status"] in ("invalid", "no_usable_fields")
    assert p == K.resolve_params()


def test_extreme_values_stay_in_range(tmp_path):
    path = _write(
        tmp_path,
        [
            {
                "acid": "lactic_acid",
                "assoc_energy_per_water_kcal": 500,
                "deprot_energy_n6_kcal": -900,
                "hbond_min_A": 9,
            },
            {
                "acid": "acetic_acid",
                "assoc_energy_per_water_kcal": -500,
                "deprot_energy_n6_kcal": 900,
                "hbond_min_A": 0.1,
            },
        ],
    )
    p, rep = K.apply_molecular_descriptors({"beta_lactic": 0.12}, path=path, return_report=True)
    assert rep["status"] == "applied"
    _in_range(p)
    assert p["beta_lactic"] == 0.12  # caller overrides preserved


def test_lactic_potency_term_inhibits_yeast():
    lo = simulate(
        TemperatureSchedule.constant(27),
        params={"undissociated_lactic_rel_potency": 0.0},
        t_end_h=16,
    )
    hi = simulate(
        TemperatureSchedule.constant(27),
        params={"undissociated_lactic_rel_potency": 0.3},
        t_end_h=16,
    )
    assert hi.co2_cumulative.iloc[-1] < lo.co2_cumulative.iloc[-1]


# ----------------------------------------------------------------------------- downstream


@pytest.fixture(scope="module")
def ab():
    return (
        simulate(TemperatureSchedule.constant(22), t_end_h=24),
        simulate(TemperatureSchedule.constant(27), t_end_h=24),
    )


def test_morphology_columns(ab):
    for df in ab:
        assert {"co2_retained", "gluten_weakening_index"} <= set(df.columns)
        g = df.gluten_weakening_index.to_numpy()
        assert (g >= 0).all() and (g <= 1).all()
        assert (np.diff(g) >= -1e-12).all()
        assert (df.co2_retained <= df.co2_cumulative + 1e-9).all()
        # retained gas saturates: its max is well below total production
        assert df.co2_retained.max() < 0.5 * df.co2_cumulative.iloc[-1]


def test_summarize_for_morphology(ab):
    a, b = ab
    sa = summarize_for_morphology(a, t_end_h=16)
    sb = summarize_for_morphology(b, t_end_h=16)
    keys = {
        "co2_cumulative_g_per_kg",
        "co2_retained_g_per_kg",
        "expansion_ratio_final",
        "expansion_ratio_peak",
        "max_expansion_rate_per_h",
        "peak_expansion_time_h",
        "overproof_flag",
        "final_ph",
        "gluten_weakening_index",
    }
    assert keys <= set(sa)
    assert sa["provenance"] == sb["provenance"] == "simulated"
    assert sa["t_end_h"] == pytest.approx(16)
    assert sb["gluten_weakening_index"] > sa["gluten_weakening_index"]
    assert sb["max_expansion_rate_per_h"] > sa["max_expansion_rate_per_h"]
    assert sb["overproof_flag"] is True
    assert summarize_for_morphology(a, t_end_h=8)["overproof_flag"] is False
    assert sb["final_ph"] < sa["final_ph"]
    json.dumps(sa)  # JSON-serialisable


# ----------------------------------------------------------------- campaign-2 descriptor paths

C2 = {
    "provenance": {"label": "test-fixture (campaign-2 layout)"},
    "relative_pka_estimate": {
        "value": -3.89,
        "uncertainty": 1.73,
        "experiment": -0.90,
        "per_n": {"6": {"dpKa": -4.65}},
    },
    "lactic_acid": {
        "assoc_energy_per_water_kcal": -9.66,  # campaign 1 (must NOT be used when c2 present)
        "c2_boltzmann_assoc_energy_per_water_kcal_by_n_neutral": {
            "1": -9.7,
            "3": -11.3,
            "6": -11.5,
            "10": -11.7,
        },
        "c2_assoc_energy_per_water_kcal_by_n_neutral": {"1": -9.5, "3": -11.1},
        "hbond_min_A": 1.64,
        "deprot_energy_n6_kcal": 11.25,
        "deprot_energy_separated_ions_n6_kcal": 152.25,
    },
    "acetic_acid": {
        "assoc_energy_per_water_kcal": -9.54,
        "c2_boltzmann_assoc_energy_per_water_kcal_by_n_neutral": {
            "1": -10.3,
            "3": -11.2,
            "6": -11.8,
            "15": -10.8,
        },
        "c2_assoc_energy_per_water_kcal_by_n_neutral": {"1": -10.3, "3": -11.0},
        "hbond_min_A": 1.49,
        "deprot_energy_n6_kcal": None,
        "deprot_energy_separated_ions_n6_kcal": 147.96,
    },
}


def test_relative_pka_estimate_preferred(tmp_path):
    p, rep = K.apply_molecular_descriptors(None, path=_write(tmp_path, C2), return_report=True)
    assert rep["status"] == "applied"
    rel = rep["inputs"]["relative_pka"]
    assert rel["field"] == "relative_pka_estimate.value"
    assert "campaign 2" in rel["campaign"]
    cmp_ = rel["comparison_to_experiment"]
    assert cmp_["experimental_dpka"] == pytest.approx(-0.90)
    assert cmp_["deviation_from_experiment"] == pytest.approx(-2.99)
    assert cmp_["sign_agrees_with_experiment"] is True
    assert cmp_["cap_binding"] is True
    assert cmp_["applied_gap_shift"] == pytest.approx(-0.05)
    d = K.resolve_params()
    assert (p["pka_lactic"] - p["pka_acetic"]) == pytest.approx(
        d["pka_lactic"] - d["pka_acetic"] - 0.05
    )
    for n in ("pka_lactic", "pka_acetic"):
        assert rep["parameters"][n]["source"] == "alchemi-informed"
        assert rep["parameters"][n]["literature_value"] == d[n]
    _in_range(p)


def test_c2_boltzmann_association_preferred_over_campaign1(tmp_path):
    _, rep = K.apply_molecular_descriptors(None, path=_write(tmp_path, C2), return_report=True)
    a = rep["inputs"]["association_energy_per_water"]
    assert a["field"] == "c2_boltzmann_assoc_energy_per_water_kcal_by_n_neutral"
    assert "campaign 2" in a["campaign"]
    assert a["n_used"] == ["1", "3", "6"]  # only cluster sizes present for both acids
    assert a["lactic"] == pytest.approx(np.mean([-9.7, -11.3, -11.5]))
    hb = rep["inputs"]["hbond_min"]
    assert hb["field"] == "hbond_min_A" and "campaign 1" in hb["campaign"]
    pot = rep["parameters"]["undissociated_lactic_rel_potency"]["inputs"]
    assert "campaign 2" in pot["d_assoc_campaign"] and "campaign 1" in pot["d_hbond_campaign"]


def test_relative_pka_absent_falls_back_to_common_deprot_field(tmp_path):
    data = json.loads(json.dumps(C2))
    del data["relative_pka_estimate"]
    _, rep = K.apply_molecular_descriptors(None, path=_write(tmp_path, data), return_report=True)
    rel = rep["inputs"]["relative_pka"]
    # acetic n6 ion pair is null -> must use the separated-ion field for BOTH acids (no mixing)
    assert rel["field"] == "deprot_energy_separated_ions_n6_kcal"
    assert rel["cluster_dpka"] == pytest.approx((152.25 - 147.96) / 1.364)
    assert rep["status"] == "applied"


def test_relative_pka_nan_and_no_deprot_is_safe(tmp_path):
    data = json.loads(json.dumps(C2))
    data["relative_pka_estimate"]["value"] = None
    for acid in ("lactic_acid", "acetic_acid"):
        data[acid].pop("deprot_energy_separated_ions_n6_kcal")
        data[acid]["deprot_energy_n6_kcal"] = None
    p, rep = K.apply_molecular_descriptors(None, path=_write(tmp_path, data), return_report=True)
    assert rep["status"] == "partial"
    assert "relative_pka" not in rep["inputs"]
    d = K.resolve_params()
    assert p["pka_lactic"] == d["pka_lactic"] and p["pka_acetic"] == d["pka_acetic"]
    assert rep["parameters"]["pka_lactic"]["source"] == "literature"
