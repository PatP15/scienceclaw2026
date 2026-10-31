"""End-to-end: every arm runs on tiny worlds, budgets match, results are deterministic."""

import pandas as pd
import pytest

from fermentai.agents import schedules as S
from fermentai.design.benchmark import BenchConfig, run_world, summarize

LAM = 0.86  # fixed here to skip the prior-lambda computation in tests
TINY = BenchConfig(n_rounds=1, max_nfev=12)


@pytest.fixture(scope="module")
def tiny_rows():
    return [r for seed in (0, 1) for r in run_world(seed, TINY, LAM)]


def test_both_policies_run_end_to_end(tiny_rows):
    df = pd.DataFrame(tiny_rows)
    assert set(df.arm) == {"agent/independent", "agent/collaborative", "info/independent", "info/collaborative"}
    fin = df[df["round"] == 1]
    assert len(fin) == 2 * 4
    assert (fin.n_experiments == 2).all()  # identical budget for every arm
    assert fin.choice_A.isin(S.LIBRARY_BY_ID).all() and fin.choice_B.isin(S.LIBRARY_BY_ID).all()
    assert (fin.pooled_info_gain_nats > 0).all()
    assert (fin.pooled_sd_ratio_mean < 1).all()
    collab = fin[fin["mode"] == "collaborative"]
    assert (collab.messages_cum > 0).all() and (collab.lead != "").all()
    assert (fin[fin["mode"] == "independent"].messages_cum == 0).all()
    # collaborative agents share one model -> agent-level == pooled metrics
    assert (collab.agent_rmse_ph - collab.pooled_rmse_ph).abs().max() < 1e-12
    assert (df.provenance == "simulated").all()
    s = summarize(df, TINY)
    vs = s["variants"]["well_specified"]
    assert vs["n_worlds"] == 2
    assert {"agent", "info"} <= set(vs["paired_final"])
    assert "IN-SILICO" in s["label"]


def test_round_one_independent_agents_duplicate(tiny_rows):
    """With identical priors and no communication both agents pick the same first experiment."""
    df = pd.DataFrame(tiny_rows)
    ind = df[(df["round"] == 1) & (df["mode"] == "independent")]
    assert (ind.within_round_dup_cum == 1).all()
    col = df[(df["round"] == 1) & (df["mode"] == "collaborative")]
    assert (col.within_round_dup_cum == 0).all()


def test_deterministic_with_seed(tiny_rows):
    cfg = BenchConfig(n_rounds=1, max_nfev=12, selectors=("info",), modes=("collaborative",))
    a = pd.DataFrame(run_world(1, cfg, LAM)).drop(columns=["seconds"])
    b = pd.DataFrame(run_world(1, cfg, LAM)).drop(columns=["seconds"])
    pd.testing.assert_frame_equal(a, b)
    ref = pd.DataFrame(tiny_rows)
    ref = ref[(ref.world == 1) & (ref.arm == "info/collaborative")].drop(columns=["seconds"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), ref[a.columns])
