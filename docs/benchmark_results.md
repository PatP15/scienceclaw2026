# Agent-collaboration benchmark — IN-SILICO BENCHMARK (simulated worlds)

> **Provenance: `simulated`.** Every number on this page comes from the fermentation simulator
> playing the role of "nature". No dough, sensor or judge was involved. These results say how the
> agents' experiment-selection logic behaves *inside the model*. They do not show that
> collaboration helps in the real chambers (PLAN §10, §18).

Reproduce (about 5 min on 10 worker processes):

```bash
uv run python scripts/benchmark_agents.py            # 30 well-specified + 30 misspecified worlds
uv run pytest tests/test_design_*.py                 # 17 tests, ~15 s
```

Outputs are in `data/benchmarks/`:

- `benchmark_results.csv`: one row per world × variant × arm × round, 1,200 rows.
- `summary.json`: mean ± 95% CI per arm and round, plus paired differences.
- `time_to_objective.csv` and `run_manifest.json`.
- Figures: `curves_well_specified.png`, `curves_misspecified.png`, `paired_differences_well_specified.png`, `paired_differences_misspecified.png` and `redundancy.png`.

## 1. Question

Does agent-to-agent communication (1) **change** and (2) **improve** which experiments the two
scientist agents choose, when the experiment budget is fixed?

## 2. Setup

### World (hidden truth)

Each world has a seed, and the seed sets a hidden parameter vector for the kinetic model
(`fermentai.fermentation`):

- **Well-specified** (headline): the 5 free parameters are drawn from their configured
  distributions (`ParameterSpec.sample`, i.e. triangular or truncated-normal within the
  `configs/kinetics.yaml` ranges). All other parameters stay nominal. The agents' model class
  contains the truth.
- **Misspecified** (robustness check): *all* 43 sampled parameters are drawn
  (`simulation.sample_parameters`). The agents still fit only the 5 free parameters and hold the
  rest at nominal values, so their model is wrong.

The 5 free parameters are `mu_max_lab` (acidification rate), `t_opt_lab` (its temperature
optimum), `qs_yeast` (gas rate), `t_opt_yeast` (its optimum) and `expansion_max` (peak rise).
The temperature optima can only be identified by running different temperatures.

An **experiment** runs one library schedule in one chamber. The world returns noisy observations:

- pH at 4 h, at 8 h and at the end of the schedule, with N(0, 0.05) noise (the single shared probe).
- Expansion ratio every 1 h and at the end, with N(0, 0.05) V/V0 noise (side camera).

The noise uses common random numbers: the draw depends only on (world, schedule, replicate
index). Two policies that run the same schedule in the same world therefore see the same data,
so the comparisons are paired.

### Budget

Every arm gets 4 rounds × 2 chambers = **8 experiments**. Choices are restricted to the
17-schedule validated library in `agents/schedules.py`.

### Objective

Both modes pursue the same objective, parsed from a fixed judge feedback string (the `demo_01` text):

> "Loaf A was mild and fluffy. Loaf B was more sour but a little dense. I'd like a stronger sour
> flavor without losing the airy texture."

`offline_policy.build_targets` turns this into: sourness ≈ 0.92 on the judge scale, and crumb
openness ≥ 0.76 as a constraint. The **true cost** of a schedule is
`offline_policy.candidate_cost`, evaluated on evidence built from the *true* parameters.

### Calibration (each round)

The fit is a joint MAP over all experiments the agent can see (`design/fitting.py`). It follows
`calibration.calibrate` conventions:

- bounded, normalised coordinates;
- `least_squares` (trf);
- residuals divided by the noise sd;
- a Gaussian prior at the configured value with sd = range/4.

The posterior uses the Laplace approximation, Σ = (JᵀJ + prior precision)⁻¹. Unlike `calibrate`,
it does not inflate Σ by the residual variance, because the noise sd is known here.

Why a new fit routine: `calibrate()` accepts only one schedule, so the multi-schedule fit is an
adapter in `design/`. No file in `fermentation/` was changed.

### Data access

| | independent | collaborative |
|---|---|---|
| Findings log | one private in-memory `FindingsLog` per agent | one shared append-only `FindingsLog` |
| Observations used for calibration | own chamber only (≤ 4 experiments) | all observation findings in the shared log (≤ 8). The fit is deterministic, so both agents hold the **same team model** |
| Sees peer's proposal | no | yes, read **from the log** |

### Arms (selector × mode)

**Selector `agent`: the deployed policy, unmodified.** The benchmark runs the
`agents/offline_policy` decision functions on evidence from each agent's calibrated model.
`design/agent_adapter.py` swaps in calibrated parameters; at default parameters its predictions
match `schedules.predict_schedule` exactly, and a test checks this.

- Independent: each agent takes its own top-ranked candidate
  (`rank_candidates(evidence, risk_weight)[0]`, which equals `peer_decision(communicate=False)`'s
  independent choice). Agent A has risk weight 1.5 and agent B has 1.0, the `ScientistAgent`
  temperaments.
- Collaborative: the real `ScientistAgent` exchange on the shared log:
  `choose_lead`, then `propose`, then `read_peer_proposal`, then
  `offline_critique(communicate=True)` (redundancy penalty and complementary-arm suggestion),
  then `respond`, then `reply_to_critique`.
- This policy is pure exploitation of the judge objective. It has no information term and no
  memory of earlier rounds.

**Selector `info`: a benchmark-only optimizer, `design/optimizer.py`.**

- Score = objective cost + redundancy penalty − λ·EIG.
- EIG = 0.5·logdet(I + J Σ Jᵀ), the linear-Gaussian expected information gain of the candidate's
  observation vector under the agent's current posterior.
- In collaborative mode the peer reads the lead's choice from the log, conditions Σ on it
  (greedy batch D-optimal design) and adds `rank_candidates`' redundancy penalty.
- λ = 0.863 cost units per nat. It was fixed *before* any comparison by scale-matching at the
  prior: λ × (inter-quartile range of EIG over the library) = inter-quartile range of cost over
  the library. It was not tuned on outcomes.

## 3. Metric definitions

Most metrics are reported two ways:

- **"agent"**: the mean over agents A and B of each agent's own model.
- **"pooled"**: an evaluator-only model fitted on all 2r experiments the team ran. In
  collaborative mode this is the same as the team model. In independent mode it answers: "had
  the two independent agents pooled their data afterwards, how informative were the experiments
  they chose?" The pooled view isolates **selection quality** from **data sharing**.

| metric | definition |
|---|---|
| redundant experiments | An experiment is redundant if its schedule is the same as, or near-identical to, (a) the other chamber's schedule in the same round, or (b) any earlier run by the team. Near-identical means a temperature-profile RMS difference ≤ 1.0 °C over the common window and a duration difference ≤ 1 h; in the library only `steady_26_10h` ~ `steady_27_10h` qualifies. Each experiment is counted at most once, and counts are cumulative out of 8. "within-round" counts (a) only. |
| distinct schedules | Number of different library schedules run (out of 8 experiments). |
| parameter uncertainty | Mean over the 5 free parameters of posterior sd / prior sd (prior sd = range/4). 1 means nothing learned. Uncertainty reduction = 1 − this value. |
| information gained | IG = 0.5·(logdet Σ_prior − logdet Σ_post), in nats. |
| information per experiment | IG / number of experiments the model was fitted on. |
| held-out prediction error | RMSE of predicted versus true (noise-free) pH and expansion, every 0.5 h, over 6 validated held-out schedules. None of them is in the library or near-identical to a library schedule (steady 21 °C/16 h, 23 °C/10 h, 25 °C/14 h; 28→18 °C; 19→25 °C; 31→22 °C), so they are never run. |
| parameter error | Mean over the free parameters of \|fit − true\| / range. |
| decision regret | True cost of the schedule the model recommends (`rank_candidates(..., risk_weight=1)[0]`) minus the true cost of the best library schedule. |
| objective reached | Regret ≤ 0.01. |
| time-to-objective | First round in which *both* agents' recommendations reach the objective. 5 means never within budget (censored). |
| peer changed choice | Rounds (out of 4) in which the peer's final choice differs from what it would have chosen without reading the proposal. |

**Statistics.** Each mean ± 95% CI uses the t-interval over worlds. Each paired difference is
collaborative − independent, computed within each world, and reported with:

- the t-interval;
- counts of worlds where the difference is positive / negative / zero;
- the Wilcoxon signed-rank p-value.

n = 30 worlds per variant. No correction for multiple comparisons was applied; read p-values
near 0.05 as weak evidence.

## 4. Results — well-specified worlds (n = 30), after 8 experiments

Cells show mean [95% CI]. The paired difference is collaborative − independent. The +/−/0
column counts worlds where that difference is positive, negative or zero.

**Deployed agent policy (`offline_policy`)**

| metric | independent | collaborative | paired diff | +/−/0 | Wilcoxon p |
|---|---|---|---|---|---|
| redundant experiments (of 8) | 6.07 [5.71, 6.42] | 5.27 [5.07, 5.46] | **−0.80 [−1.12, −0.48]** | 2/20/8 | 1e-4 |
| … of which within-round duplicates | 3.13 | 0.00 | −3.13 | | |
| distinct schedules | 1.93 | 2.73 | **+0.80 [+0.48, +1.12]** | 20/2/8 | 1e-4 |
| param sd/prior sd, pooled | 0.223 [0.206, 0.240] | 0.180 [0.167, 0.192] | **−0.043 [−0.066, −0.020]** | 10/20/0 | 0.001 |
| info gained, pooled (nats) | 10.53 [10.28, 10.78] | 11.20 [10.96, 11.45] | **+0.68 [+0.39, +0.96]** | 24/6/0 | 9e-5 |
| info per experiment, pooled | 1.316 | 1.400 | **+0.085 [+0.049, +0.121]** | 24/6/0 | 9e-5 |
| info gained, agents' own models | 8.89 | 11.20 | **+2.31 [+2.05, +2.57]** | 30/0/0 | 2e-9 |
| held-out pH RMSE, agents' models | 0.018 [0.013, 0.022] | 0.011 [0.008, 0.014] | **−0.007 [−0.012, −0.002]** | 9/21/0 | 0.004 |
| held-out rise RMSE, agents' models | 0.019 [0.014, 0.023] | 0.009 [0.007, 0.012] | **−0.009 [−0.014, −0.004]** | 10/20/0 | 0.003 |
| held-out pH RMSE, pooled | 0.015 | 0.011 | −0.005 [−0.010, +0.000] | 12/18/0 | 0.06 |
| held-out rise RMSE, pooled | 0.013 | 0.009 | −0.004 [−0.007, −0.000] | 12/18/0 | 0.06 |
| parameter error (agents) | 0.050 | 0.037 | **−0.013 [−0.020, −0.005]** | 5/25/0 | 7e-5 |
| decision regret (agents) | 0.003 [0.000, 0.005] | 0.002 [0.000, 0.004] | −0.001 [−0.004, +0.002] | 4/5/21 | 0.76 |
| time-to-objective (rounds) | 1.17 [0.59, 1.75] | 0.77 [0.34, 1.19] | **−0.40 [−0.69, −0.11]** | 0/8/22 | 0.02 |
| fraction of worlds reaching the objective | 0.93 | 0.97 | | | |
| peer changed choice after reading the proposal | — | 3.93 of 4 rounds | | | |

**Information-aware selector (`optimizer`)**

| metric | independent | collaborative | paired diff | +/−/0 | Wilcoxon p |
|---|---|---|---|---|---|
| redundant experiments (of 8) | 5.20 [4.97, 5.43] | 4.93 [4.60, 5.27] | **−0.27 [−0.46, −0.07]** | 0/7/23 | 0.04 |
| … of which within-round duplicates | 3.50 | 0.00 | −3.50 | | |
| distinct schedules | 2.80 | 3.07 | +0.27 [+0.07, +0.46] | 7/0/23 | 0.04 |
| param sd/prior sd, pooled | 0.137 [0.130, 0.145] | 0.142 [0.133, 0.151] | **+0.005 [+0.002, +0.008]** (worse) | 22/8/0 | 0.002 |
| info gained, pooled (nats) | 12.15 [11.98, 12.31] | 12.02 [11.83, 12.20] | **−0.13 [−0.18, −0.08]** (worse) | 6/24/0 | 3e-5 |
| info gained, agents' own models | 10.48 | 12.02 | **+1.54 [+1.48, +1.59]** | 30/0/0 | 2e-9 |
| held-out pH RMSE, agents' models | 0.012 | 0.008 | **−0.004 [−0.006, −0.003]** | 6/24/0 | 6e-6 |
| held-out rise RMSE, agents' models | 0.009 | 0.006 | **−0.004 [−0.005, −0.003]** | 3/27/0 | 6e-7 |
| held-out pH / rise RMSE, pooled | 0.008 / 0.006 | 0.008 / 0.006 | ≈0 (CI spans 0) | 16/14/0 | 0.98 / 0.70 |
| decision regret (agents) | 0.001 | 0.001 | −0.000 [−0.001, +0.001] | 2/3/25 | 0.74 |
| time-to-objective (rounds) | 1.03 | 0.83 | −0.20 [−0.45, +0.05] | 2/6/22 | 0.23 |
| peer changed choice after reading the proposal | — | 3.90 of 4 rounds | | | |

**Over rounds** (pooled information, collaborative − independent; see `curves_well_specified.png`):

| selector | after 2 experiments | after 4 | after 6 | after 8 |
|---|---|---|---|---|
| agent | +1.26 nats | +0.77 | +0.72 | +0.68 |
| info | +1.75 nats | +0.21 | +0.03 | −0.13 |

**Baseline.** Before any experiment, decision regret averages 0.075, and the default-model choice
reaches the objective in 53% of worlds (43% when misspecified). After 2 experiments every arm is below 0.02. The objective
is easy to reach with this library.

## 5. Results — misspecified worlds (n = 30), after 8 experiments

| metric (paired diff, collaborative − independent) | agent policy | info selector |
|---|---|---|
| redundant experiments | −0.40 [−0.73, −0.07] | −0.57 [−0.87, −0.26] |
| info gained, pooled (nats) | −0.57 [−1.88, +0.75] (n.s.) | −0.39 [−0.96, +0.19]; 23/30 worlds favour independent, Wilcoxon p = 0.0006 |
| param sd/prior sd, pooled | −0.031 [−0.064, +0.003] | +0.013 [−0.009, +0.035]; Wilcoxon p = 0.005, favours independent |
| held-out pH RMSE, pooled | **−0.009 [−0.016, −0.002]** | +0.001 [−0.000, +0.001] |
| held-out rise RMSE, pooled | **−0.019 [−0.034, −0.004]** | **−0.004 [−0.006, −0.002]** |
| decision regret (agents) | −0.002 [−0.014, +0.011] | −0.001 [−0.005, +0.004] |
| time-to-objective | **−0.30 [−0.50, −0.10]** rounds | 0.00 [−0.31, +0.31] |

With a wrong model class the held-out pH error stays high (about 0.065 in every arm). Final
regret is about 0.055, and only 63–70% of worlds reach the objective whatever the
communication mode. Model error, not experiment choice, is the binding limit.

## 6. Interpretation

**Communication changes decisions.** In ≥ 3.8 of 4 rounds, in both selectors and both world
types, the peer's final choice differs from the one it would have made without reading the
proposal. Within-round duplicates drop from 3.1–3.5 per world (independent) to **0**
(collaborative). Without communication the two agents start from the same prior and the same
objective, so they nearly always pick the same experiment.

**Whether it improves things depends on the metric and the selector:**

1. **Deployed agent policy: modest, real gains from better selection.** The pooled view
   compares the same 8-experiment budget and the same amount of data, so it isolates selection.
   In well-specified worlds the collaborative team's experiments carry more information
   (+0.68 nats, +6%; 24/30 worlds) and leave lower parameter uncertainty (−0.043). Pooled
   held-out prediction is better only borderline (p ≈ 0.06). In misspecified worlds pooled
   held-out errors improve clearly, but pooled information does not significantly.
2. **The large effects are data sharing, not selection.** The agents' own models gain +2.3 nats,
   have 33–53% lower held-out error and smaller parameter error. That is mostly because
   each independent agent learns from 4 experiments and each collaborative agent from 8. This is
   a genuine operational benefit of the shared log, but it is not "smarter experiment choice".
3. **With an information-aware selector, collaboration's selection advantage vanishes by the
   end of the budget.** It is large after the first round (+1.75 nats), because independent
   agents duplicate their first experiment. By 8 experiments the independent team's pooled data
   are *slightly more* informative (−0.13 nats; 24/30 worlds), and pooled held-out errors are
   indistinguishable. In misspecified worlds the same direction appears (23/30 worlds), with a
   CI that includes 0. Hypotheses, none tested yet:
   - (a) Five parameters are spanned by a handful of schedules (steady 18 °C, steady 22 °C and
     32→18 °C dominate every arm). A replicate with fresh noise of the most informative schedule
     is worth almost as much as a "complementary" schedule, so de-duplication buys little.
   - (b) The shared model becomes confident sooner. Its EIG values shrink while λ stays fixed, so
     the objective-cost term dominates and the team exploits earlier: collaborative runs
     `steady_27_10h`, the true optimum in 10 of 30 worlds, 16 times versus 6.
   - (c) The redundancy rule counts replicates as waste, while EIG does not.
4. **No measurable effect on the decision the judge cares about.** Final decision regret is
   statistically indistinguishable in every comparison, because nearly every arm finds a
   near-optimal schedule within 2–4 experiments. The deployed policy reaches the objective about
   0.3–0.4 rounds earlier with communication (p ≈ 0.02 in both world types). The info selector
   shows no significant speed-up.
5. **Total redundancy stays high in every arm** (4.7–6.1 of 8). Communication removes
   within-round duplicates, but repeats across rounds take their place: neither policy remembers
   which schedules were already run. The deployed collaborative pair typically fixes on one
   two-schedule contrast and re-runs it every round. Adding history to `rank_candidates`
   (`exclude_ids` already exists) is the obvious next experiment. It was not done here because
   it would change the deployed policy being benchmarked.

**Verdict.** In this simulator, communication reliably changes experiment selection and removes
same-round duplication. With the deployed policy it gives a small but consistent gain in
information and parameter uncertainty for the same budget, plus large gains from data sharing.
It does **not** measurably improve the final recommendation. With an information-aware selector,
communication does not improve the information content of the selected experiments by the end of
the budget (slightly worse in well-specified worlds). The case for collaboration rests mainly on
data sharing and early-round efficiency, not on better selection as such.

## 7. Limitations

- **Same simulator for world and agent model.** In well-specified worlds the agents' model class
  contains the truth, which is optimistic. The misspecified variant varies all 43 sampled parameters but
  still uses the same equations. Real dough can differ structurally.
- **Library-constrained choices.** Only 17 validated schedules. Several are near-equivalent for
  identifying 5 parameters, which limits how much any selection rule can differ.
- **Deterministic policies.** Differences between worlds come only from the hidden parameters and
  measurement noise. With identical priors, independent agents are near-copies, which favours
  collaboration on duplication; heterogeneous priors would change this.
- **Fixed objective.** One judge string, so one target. Regret saturates quickly, so the
  benchmark says little about harder objectives.
- **Laplace / linear-Gaussian approximations.** These are used for posterior sd, information
  gain and EIG. Bounds and nonlinearity make them approximate. The posterior is not inflated for
  model error, so in misspecified worlds the uncertainty metrics are overconfident; the
  held-out errors carry that information instead.
- **Redundancy definition.** It is a count rule (same or near-identical schedule). It treats
  replicates as waste even when they are informative under noise.
- **λ, budget and noise levels are fixed choices.** λ is scale-matched at the prior; the budget
  is 8 experiments; pH sd is 0.05 and rise sd is 0.05. Other settings were not swept.
- **Observation model.** The benchmark assumes clean, unbiased pH every 4 h and rise every
  1 h. The real setup has one probe, sample handling, and camera calibration error.
- **The deployed policy is benchmarked as-is.** The collaborative arm uses the real
  `ScientistAgent` messages. The LLM ("live") mode was not benchmarked.

## 8. Implementation notes

`src/fermentai/design/`:

- `world.py`: worlds, fast forward model on top of `simulation.integrate`, observations,
  held-out set, redundancy.
- `fitting.py`: joint calibration.
- `agent_adapter.py`: calibrated evidence for `offline_policy`.
- `optimizer.py`: EIG, conditioning, λ.
- `benchmark.py`: arms, metrics, aggregation.

Other files: `scripts/benchmark_agents.py` (parallel driver and figures) and
`tests/test_design_*.py` (17 tests).

Nothing in `agents/` or `fermentation/` was modified. The adapter replicates
`schedules._simulator_prediction` with a `params` argument; `tests/test_design_adapter.py` fails
if the two drift apart. No integration failures occurred during calibration in the full run
(`*_fit_failed_evals` = 0).

Runtime: 297 s for 60 worlds × 4 arms on 10 processes (Apple silicon). Per world, an
independent arm takes about 13–18 s and a collaborative arm about 6–8 s.
