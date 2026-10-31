"""Metric definitions on toy cases: information gain, EIG, posterior shrinkage, CIs, regret."""

import math

import numpy as np
import pandas as pd
import pytest

from fermentai.agents import schedules as S
from fermentai.design import optimizer as O
from fermentai.design.agent_adapter import build_evidence_calibrated, true_costs
from fermentai.design.benchmark import Model, mean_ci, model_metrics, paired, time_to_objective
from fermentai.design.fitting import FREE_PARAMS, fit_experiments, prior_cov_z, prior_fit
from fermentai.design.world import draw_world, run_experiment


def test_eig_linear_gaussian_toy():
    # one parameter, prior var s2, one observation with sensitivity j (whitened): EIG = 0.5 ln(1 + j^2 s2)
    s2, j = 0.25**2, 3.0
    assert O.eig(np.array([[j]]), np.array([[s2]])) == pytest.approx(0.5 * math.log(1 + j * j * s2))
    # running the same experiment again is worth less once the first is accounted for
    cov1 = O.condition(np.array([[s2]]), np.array([[j]]))
    assert cov1[0, 0] == pytest.approx(1 / (1 / s2 + j * j))
    assert O.eig(np.array([[j]]), cov1) < O.eig(np.array([[j]]), np.array([[s2]]))
    # orthogonal experiment is unaffected by conditioning on the first
    cov = np.diag([s2, s2])
    Ja, Jb = np.array([[j, 0.0]]), np.array([[0.0, j]])
    assert O.eig(Jb, O.condition(cov, Ja)) == pytest.approx(O.eig(Jb, cov))


def test_prior_fit_has_no_information():
    pf = prior_fit()
    assert pf.info_gain_nats == 0.0
    assert all(v == 1.0 for v in pf.sd_ratio.values())
    assert np.allclose(pf.cov_z, prior_cov_z())


def test_fit_shrinks_uncertainty_and_moves_toward_truth():
    w = draw_world(5)
    exps = [run_experiment(w, sid, S.LIBRARY_BY_ID[sid].points) for sid in ("steady_22_14h", "steady_27_10h",
                                                                            "steady_18_16h")]
    f1 = fit_experiments(exps[:1])
    f3 = fit_experiments(exps)
    assert 0 < f1.info_gain_nats < f3.info_gain_nats
    assert np.mean(list(f3.sd_ratio.values())) < np.mean(list(f1.sd_ratio.values())) < 1.0
    # info gain is exactly 0.5 * logdet ratio of prior and posterior covariances
    ld = lambda m: np.linalg.slogdet(m)[1]
    assert f3.info_gain_nats == pytest.approx(0.5 * (ld(prior_cov_z()) - ld(f3.cov_z)))
    # expansion_max (peak height) is directly observed and must be well recovered
    assert abs(f3.fitted["expansion_max"] - w.true_free["expansion_max"]) < 0.1
    assert set(f3.fitted) == set(FREE_PARAMS)


def test_model_metrics_zero_regret_and_error_at_truth():
    w = draw_world(7)
    ev = build_evidence_calibrated(w.true_params)
    tc = true_costs(ev)
    best = min(tc, key=tc.get)
    pf = prior_fit()
    oracle = Model(fit=pf, evidence=ev)  # recommendation from the TRUE evidence
    oracle.fit.params = dict(w.true_params)
    m = model_metrics(oracle, w, tc, tc[best], tol=0.0)
    assert m["regret"] == pytest.approx(0.0)
    assert m["objective_reached"] == 1.0
    assert m["rmse_ph"] == pytest.approx(0.0, abs=1e-12)


def test_mean_ci_and_paired_toy():
    c = mean_ci([1.0, 2.0, 3.0])
    assert c["mean"] == 2.0 and c["lo"] < 2.0 < c["hi"] and c["n"] == 3
    # t(0.975, 2) = 4.303; sd = 1 -> half-width 4.303 / sqrt(3)
    assert c["hi"] - 2.0 == pytest.approx(4.302653 / math.sqrt(3), rel=1e-5)
    p = paired([1.0, -1.0, 0.0, 2.0])
    assert (p["n_pos"], p["n_neg"], p["n_zero"]) == (2, 1, 1)
    assert paired([0.0, 0.0])["wilcoxon_p"] == 1.0


def test_time_to_objective_censoring():
    df = pd.DataFrame({
        "variant": "v", "selector": "agent", "mode": ["independent"] * 3 + ["collaborative"] * 3, "world": 0,
        "round": [0, 1, 2] * 2, "agent_objective_reached": [0, 0, 0, 0, 1, 1]})
    t = time_to_objective(df, 2).set_index("mode")
    assert t.loc["collaborative", "time_to_objective"] == 1 and t.loc["collaborative", "reached"]
    assert t.loc["independent", "time_to_objective"] == 3 and not t.loc["independent", "reached"]
