"""In-silico benchmark: does agent-to-agent communication change / improve experiment selection?

Label: IN-SILICO BENCHMARK — simulated worlds (PLAN §10, §18). Nothing here is a measurement.

Arms = selector x mode, all with the same budget (``n_rounds`` rounds x 2 chambers):

selector ``agent``  the deployed deterministic agent policy (``agents.offline_policy``),
                    unmodified, run on evidence from each agent's *calibrated* model
                    (``agent_adapter``). Pure exploitation of the judge objective, with the
                    peer-redundancy penalty only when agents communicate.
selector ``info``   benchmark optimizer (``optimizer``): same objective cost minus
                    lam * expected information gain (+ the same redundancy penalty and
                    conditioning on the peer's experiment when communicating).

mode ``independent``   each agent has a private findings log: it calibrates on its own
                       chamber's observations only and chooses without seeing the peer.
mode ``collaborative`` one shared append-only ``FindingsLog``: both agents publish observations
                       there, so both calibrate on the pooled data (deterministic fit -> one
                       shared team model); the lead publishes its proposal, the peer reads it
                       FROM THE LOG and critiques / avoids duplication (agent selector: the real
                       ``ScientistAgent`` propose -> critique -> reply exchange).

Each round: select -> world simulates noisy observations -> observations appended to the
log(s) -> refit (MAP + Laplace, ``fitting``) -> metrics. Metrics are computed both for the
agents' own models ("agent" = mean over A and B) and, as an evaluator-only diagnostic, for a
model fitted on all data the team collected ("pooled"; identical to the team model in
collaborative mode). The pooled view isolates *experiment selection* from *data sharing*.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from fermentai.agents import offline_policy as P
from fermentai.agents import schedules as S
from fermentai.agents.communication import FindingsLog, new_finding
from fermentai.agents.scientist import ScientistAgent

from . import optimizer as O
from .agent_adapter import SETPOINTS, build_evidence_calibrated, objective_interp, true_costs
from .fitting import FREE_PARAMS, FitResult, fit_experiments, params_to_z, prior_fit
from .world import (
    BENCHMARK_LABEL,
    PROVENANCE,
    World,
    draw_world,
    held_out_rmse,
    redundancy,
    run_experiment,
)

NOW = "2026-10-31T00:00:00Z"  # fixed timestamp -> reproducible finding ids
RISK_WEIGHT = {"A": 1.5, "B": 1.0}  # ScientistAgent temperament for setpoints A 22 °C / B 27 °C
SELECTORS = ("agent", "info")
MODES = ("independent", "collaborative")
OBS_PAYLOAD_KEY = "benchmark_observation"


@dataclass
class BenchConfig:
    n_rounds: int = 4
    selectors: tuple[str, ...] = SELECTORS
    modes: tuple[str, ...] = MODES
    misspecified: bool = False
    lam: float | None = None  # None -> optimizer.prior_lambda() (scale-matched at the prior)
    regret_tol: float = 0.01  # "objective reached" = recommendation within this cost of the true optimum
    max_nfev: int = 60
    evidence_dt_h: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Models (one per agent, or one shared)
# ---------------------------------------------------------------------------
@dataclass
class Model:
    fit: FitResult
    evidence: dict[str, Any]
    _jacs: dict[str, np.ndarray] | None = field(default=None, repr=False)

    def jacobians(self) -> dict[str, np.ndarray]:
        if self._jacs is None:
            self._jacs = O.library_jacobians(self.fit.z)
        return self._jacs


def make_model(experiments: Sequence[dict[str, Any]], prev: Model | None, cfg: BenchConfig, cache: dict) -> Model:
    if not experiments:
        if "prior" not in cache:
            pf = prior_fit()
            cache["prior"] = Model(pf, build_evidence_calibrated(pf.params, dt_h=cfg.evidence_dt_h))
        return cache["prior"]
    z0 = prev.fit.z if prev is not None else None
    f = fit_experiments(experiments, z0=z0, max_nfev=cfg.max_nfev)
    return Model(f, build_evidence_calibrated(f.params, dt_h=cfg.evidence_dt_h))


def experiments_in(logs: Sequence[FindingsLog]) -> list[dict[str, Any]]:
    """Observations an agent can see = observation findings in the log(s) it can read."""
    seen: dict[str, dict[str, Any]] = {}
    for log in logs:
        for f in log.read(type="observation"):
            if OBS_PAYLOAD_KEY in f.payload:
                seen.setdefault(f.id, f.payload[OBS_PAYLOAD_KEY])
    return list(seen.values())


def publish_observation(log: FindingsLog, chamber: str, exp: dict[str, Any], cycle_id: str) -> None:
    ph_end = exp["observed"]["ph"][-1]
    peak = max(exp["observed"]["expansion_ratio"])
    log.append(new_finding(
        f"scientist_{chamber}", "observation",
        f"[in-silico] Chamber {chamber} ran '{exp['schedule_id']}' (rep {exp['replicate']}): end pH {ph_end:.2f}, "
        f"peak rise {peak:.2f}x (simulated world, noisy).",
        payload={OBS_PAYLOAD_KEY: exp}, evidence_source="simulated", chamber_id=chamber, cycle_id=cycle_id,
        timestamp_utc=NOW))


# ---------------------------------------------------------------------------
# Selection policies
# ---------------------------------------------------------------------------
def _cand(evidence: dict[str, Any], cid: str) -> dict[str, Any]:
    return P._find(evidence, cid)


def select_independent(selector: str, models: dict[str, Model], lam: float) -> dict[str, Any]:
    """Each agent chooses from its own model only (no log access to the peer)."""
    out: dict[str, Any] = {"choice": {}, "lead": None, "messages": 0, "peer_changed": None}
    for ch in P.LOAVES:
        m = models[ch]
        if selector == "agent":
            # == peer_decision(communicate=False)["independent_choice"] / lead_decision best
            out["choice"][ch] = P.rank_candidates(m.evidence, risk_weight=RISK_WEIGHT[ch])[0]["id"]
        else:
            eigs = O.eig_table(m.jacobians(), m.fit.cov_z)
            out["choice"][ch] = O.score_candidates(m.evidence, eigs, lam, risk_weight=RISK_WEIGHT[ch])[0]["id"]
    return out


def select_collaborative(selector: str, team: Model, log: FindingsLog, cycle_id: str, lam: float) -> dict[str, Any]:
    interp = objective_interp()
    ev = team.evidence
    lead_id, lead_reason = P.choose_lead(interp, ev, SETPOINTS)
    peer_id = "B" if lead_id == "A" else "A"
    n0 = len(log.read())
    if selector == "agent":
        agents = {c: ScientistAgent(c, log=log, cycle_id=cycle_id, timestamp_utc=NOW, setpoint_c=SETPOINTS[c],
                                    evidence_source_override="simulated") for c in P.LOAVES}
        for a in agents.values():
            a.evidence = ev
        lead, peer = agents[lead_id], agents[peer_id]
        lead_dec = lead.offline_lead(interp, lead_reason)
        lead_msg = lead.propose(interp, lead_dec, decision_source="offline_policy", lead_reason=lead_reason)
        seen = peer.read_peer_proposal()  # read FROM THE LOG
        if seen is None:
            raise RuntimeError("peer could not read the proposal from the shared log")
        peer_dec, extra = peer.offline_critique(interp, seen, communicate=True, own_has_judged_outcome=True)
        crit = peer.respond(seen, peer_dec, decision_source="offline_policy",
                            independent_choice_id=extra["independent_choice"]["id"])
        _, lead_final = lead.reply_to_critique(crit, lead_msg, peer_dec)
        choice = {lead_id: lead_final["schedule_id"], peer_id: peer_dec.own_schedule_id}
        peer_changed = extra["independent_choice"]["id"] != peer_dec.own_schedule_id
    else:
        eigs = O.eig_table(team.jacobians(), team.fit.cov_z)
        lead_row = O.score_candidates(ev, eigs, lam, risk_weight=RISK_WEIGHT[lead_id])[0]
        log.append(new_finding(
            f"scientist_{lead_id}", "hypothesis",
            f"[in-silico] Proposal for chamber {lead_id}: '{lead_row['name']}' (objective cost {lead_row['score']:.3f}, "
            f"expected information gain {lead_row['eig_nats']:.2f} nats).",
            payload={"role": "proposer", "own_decision": {"schedule_id": lead_row["id"]}},
            evidence_source="simulated", chamber_id=lead_id, cycle_id=cycle_id, timestamp_utc=NOW))
        msgs = log.read(cycle_id=cycle_id, type="hypothesis", exclude_agent=f"scientist_{peer_id}")
        lead_choice_id = msgs[-1].payload["own_decision"]["schedule_id"]  # read FROM THE LOG
        J = team.jacobians()
        cov_after = O.condition(team.fit.cov_z, J[lead_choice_id])
        eigs_after = O.eig_table(J, cov_after)
        peer_row = O.score_candidates(ev, eigs_after, lam, risk_weight=RISK_WEIGHT[peer_id],
                                      avoid=_cand(ev, lead_choice_id))[0]
        alone = O.score_candidates(ev, eigs, lam, risk_weight=RISK_WEIGHT[peer_id])[0]
        log.append(new_finding(
            f"scientist_{peer_id}", "critique",
            f"[in-silico] Given chamber {lead_id} runs '{lead_choice_id}', chamber {peer_id} runs '{peer_row['id']}' "
            f"(conditional information gain {peer_row['eig_nats']:.2f} nats; alone I would have chosen '{alone['id']}').",
            payload={"role": "critic", "own_decision": {"schedule_id": peer_row["id"]},
                     "independent_choice": alone["id"]},
            evidence_source="simulated", in_reply_to=msgs[-1].id, chamber_id=peer_id, cycle_id=cycle_id,
            timestamp_utc=NOW))
        choice = {lead_id: lead_choice_id, peer_id: peer_row["id"]}
        peer_changed = alone["id"] != peer_row["id"]
    return {"choice": choice, "lead": lead_id, "messages": len(log.read()) - n0, "peer_changed": bool(peer_changed)}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def model_metrics(m: Model, world: World, tcost: dict[str, float], best_cost: float, tol: float) -> dict[str, float]:
    rec = P.rank_candidates(m.evidence, risk_weight=1.0)[0]["id"]
    rm = held_out_rmse(m.fit.params, world.true_params)
    zt = params_to_z(world.true_free)
    regret = tcost[rec] - best_cost
    return {
        "sd_ratio_mean": float(np.mean(list(m.fit.sd_ratio.values()))),
        "info_gain_nats": float(m.fit.info_gain_nats),
        "rmse_ph": rm["ph"],
        "rmse_expansion": rm["expansion_ratio"],
        "param_err_z": float(np.mean(np.abs(m.fit.z - zt))),
        "fit_failed_evals": float(m.fit.n_failed_evals),
        "regret": float(regret),
        "objective_reached": float(regret <= tol + 1e-12),
        "_rec": rec,
    }


def _mean_metrics(ms: Sequence[dict[str, float]]) -> dict[str, float]:
    return {k: float(np.mean([m[k] for m in ms])) for k in ms[0] if not k.startswith("_")}


def run_arm(world: World, selector: str, mode: str, cfg: BenchConfig, lam: float, cache: dict,
            tcost: dict[str, float], best_id: str) -> list[dict[str, Any]]:
    best_cost = tcost[best_id]
    arm = f"{selector}/{mode}"
    collab = mode == "collaborative"
    shared = FindingsLog(None)
    private = {c: FindingsLog(None) for c in P.LOAVES}
    readable = {c: [shared] if collab else [private[c]] for c in P.LOAVES}
    models = {c: make_model([], None, cfg, cache) for c in P.LOAVES}
    pooled = models["A"]
    history: list[tuple[str, Any]] = []
    reps: dict[str, int] = {}
    rows = []
    cum = {"redundant": 0, "within_round": 0, "repeats": 0, "messages": 0, "peer_changed": 0}

    def emit(r: int, sel: dict[str, Any] | None, red: dict[str, int] | None, t_round: float):
        agent_m = [model_metrics(models[c], world, tcost, best_cost, cfg.regret_tol) for c in P.LOAVES]
        pool_m = model_metrics(pooled, world, tcost, best_cost, cfg.regret_tol)
        n_exp = 2 * r
        row = {
            "world": world.seed, "variant": "misspecified" if world.misspecified else "well_specified",
            "selector": selector, "mode": mode, "arm": arm, "round": r, "n_experiments": n_exp,
            "choice_A": sel["choice"]["A"] if sel else "", "choice_B": sel["choice"]["B"] if sel else "",
            "lead": (sel or {}).get("lead") or "", "true_best": best_id,
            "rec_A": agent_m[0]["_rec"], "rec_B": agent_m[1]["_rec"], "rec_pooled": pool_m["_rec"],
            "redundant_round": red["redundant"] if red else 0,
            "within_round_dup": red["within_round"] if red else 0,
            "repeats_round": red["repeats"] if red else 0,
            "redundant_cum": cum["redundant"], "within_round_dup_cum": cum["within_round"],
            "repeats_cum": cum["repeats"], "distinct_schedules": len({h[0] for h in history}),
            "messages_cum": cum["messages"], "peer_changed_cum": cum["peer_changed"],
            "seconds": round(t_round, 3), "provenance": PROVENANCE, "label": BENCHMARK_LABEL,
        }
        for k, v in _mean_metrics(agent_m).items():
            row[f"agent_{k}"] = v
        for k, v in pool_m.items():
            if not k.startswith("_"):
                row[f"pooled_{k}"] = v
        ig_a = [models[c].fit.info_gain_nats / max(1, models[c].fit.n_experiments) for c in P.LOAVES]
        row["agent_info_per_experiment"] = float(np.mean(ig_a)) if r else 0.0
        row["pooled_info_per_experiment"] = pool_m["info_gain_nats"] / n_exp if r else 0.0
        rows.append(row)

    emit(0, None, None, 0.0)
    for r in range(1, cfg.n_rounds + 1):
        t0 = time.perf_counter()
        cycle_id = f"bench_w{world.seed}_{selector}_{mode}_r{r}"
        if collab:
            sel = select_collaborative(selector, models["A"], shared, cycle_id, lam)
        else:
            sel = select_independent(selector, models, lam)
        round_choices = [(sel["choice"][c], S.LIBRARY_BY_ID[sel["choice"][c]].points) for c in P.LOAVES]
        red = redundancy(round_choices, history)
        for k in ("redundant", "within_round", "repeats"):
            cum[k] += red[k]
        cum["messages"] += sel["messages"]
        cum["peer_changed"] += int(bool(sel["peer_changed"]))
        for c in P.LOAVES:
            sid = sel["choice"][c]
            exp = run_experiment(world, sid, S.LIBRARY_BY_ID[sid].points, replicate=reps.get(sid, 0))
            reps[sid] = reps.get(sid, 0) + 1
            publish_observation(shared if collab else private[c], c, exp, cycle_id)
        history.extend(round_choices)
        if collab:
            team = make_model(experiments_in([shared]), models["A"], cfg, cache)
            models = {"A": team, "B": team}
            pooled = team
        else:
            models = {c: make_model(experiments_in(readable[c]), models[c], cfg, cache) for c in P.LOAVES}
            pooled = make_model(experiments_in([private["A"], private["B"]]), pooled, cfg, cache)
        emit(r, sel, red, time.perf_counter() - t0)
    return rows


def run_world(seed: int, cfg: BenchConfig | None = None, lam: float | None = None) -> list[dict[str, Any]]:
    """All arms on one hidden world (same truth, same noise stream per schedule/replicate)."""
    cfg = cfg or BenchConfig()
    lam = lam if lam is not None else (cfg.lam if cfg.lam is not None else O.prior_lambda())
    world = draw_world(seed, misspecified=cfg.misspecified)
    ev_true = build_evidence_calibrated(world.true_params, dt_h=cfg.evidence_dt_h)
    tcost = true_costs(ev_true, 1.0)
    best_id = min(sorted(tcost), key=tcost.get)
    cache: dict = {}
    rows = []
    for selector in cfg.selectors:
        for mode in cfg.modes:
            rows.extend(run_arm(world, selector, mode, cfg, lam, cache, tcost, best_id))
    for row in rows:
        row["lam"] = lam
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
HEADLINE_METRICS: dict[str, dict[str, Any]] = {
    "redundant_cum": {"label": "Redundant experiments (cumulative)", "better": "lower"},
    "distinct_schedules": {"label": "Distinct schedules run", "better": "higher"},
    "agent_sd_ratio_mean": {"label": "Param. posterior sd / prior sd (agents' own models)", "better": "lower"},
    "pooled_sd_ratio_mean": {"label": "Param. posterior sd / prior sd (all team data pooled)", "better": "lower"},
    "agent_info_gain_nats": {"label": "Information gained, agents' own models (nats)", "better": "higher"},
    "pooled_info_gain_nats": {"label": "Information gained, all team data (nats)", "better": "higher"},
    "pooled_info_per_experiment": {"label": "Information per experiment, team data (nats/exp)", "better": "higher"},
    "agent_rmse_ph": {"label": "Held-out pH RMSE (agents' models)", "better": "lower"},
    "agent_rmse_expansion": {"label": "Held-out expansion RMSE (agents' models)", "better": "lower"},
    "pooled_rmse_ph": {"label": "Held-out pH RMSE (pooled)", "better": "lower"},
    "pooled_rmse_expansion": {"label": "Held-out expansion RMSE (pooled)", "better": "lower"},
    "agent_param_err_z": {"label": "Param. error |fit-true|/range (agents' models)", "better": "lower"},
    "agent_regret": {"label": "Decision regret of agents' recommendation (true cost)", "better": "lower"},
    "pooled_regret": {"label": "Decision regret, pooled-model recommendation", "better": "lower"},
    "agent_objective_reached": {"label": "Fraction of agents whose recommendation is near-optimal", "better": "higher"},
}


def mean_ci(x: Sequence[float], level: float = 0.95) -> dict[str, float]:
    from scipy import stats

    a = np.asarray([v for v in x if v is not None and np.isfinite(v)], float)
    n = len(a)
    if n == 0:
        return {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "sd": float("nan"), "n": 0}
    m = float(a.mean())
    if n < 2:
        return {"mean": m, "lo": m, "hi": m, "sd": 0.0, "n": n}
    sd = float(a.std(ddof=1))
    h = float(stats.t.ppf(0.5 + level / 2, n - 1) * sd / math.sqrt(n))
    return {"mean": m, "lo": m - h, "hi": m + h, "sd": sd, "n": n}


def paired(diff: Sequence[float]) -> dict[str, Any]:
    from scipy import stats

    d = np.asarray(diff, float)
    out = mean_ci(d)
    out.update(n_pos=int((d > 1e-12).sum()), n_neg=int((d < -1e-12).sum()), n_zero=int((np.abs(d) <= 1e-12).sum()))
    try:
        out["wilcoxon_p"] = float(stats.wilcoxon(d, zero_method="zsplit").pvalue) if np.any(np.abs(d) > 1e-12) else 1.0
    except ValueError:
        out["wilcoxon_p"] = float("nan")
    return out


def time_to_objective(df, rounds: int) -> Any:
    """First round at which the agents' (mean) recommendation is near-optimal for BOTH agents
    (agent_objective_reached == 1); ``rounds + 1`` if never (censored)."""
    import pandas as pd

    out = []
    for (variant, sel, mode, w), g in df.groupby(["variant", "selector", "mode", "world"]):
        g = g.sort_values("round")
        hit = g[g.agent_objective_reached >= 1.0 - 1e-9]["round"]
        out.append({"variant": variant, "selector": sel, "mode": mode, "world": w,
                    "time_to_objective": int(hit.iloc[0]) if len(hit) else rounds + 1,
                    "reached": bool(len(hit))})
    return pd.DataFrame(out)


def summarize(df, cfg: BenchConfig) -> dict[str, Any]:
    """Mean ± 95% CI per arm and round; paired (collaborative - independent) differences per selector."""
    R = int(df["round"].max())
    summary: dict[str, Any] = {"label": BENCHMARK_LABEL, "provenance": PROVENANCE, "config": cfg.to_dict(),
                               "free_params": list(FREE_PARAMS), "variants": {}}
    tto = time_to_objective(df, R)
    for variant, dv in df.groupby("variant"):
        vs: dict[str, Any] = {"n_worlds": int(dv.world.nunique()), "lam": float(dv["lam"].iloc[0]),
                              "arms": {}, "paired_final": {}, "paired_by_round": {}}
        for (sel, mode), g in dv.groupby(["selector", "mode"]):
            arm: dict[str, Any] = {"by_round": {}, "final": {}}
            for r, gr in g.groupby("round"):
                arm["by_round"][int(r)] = {m: mean_ci(gr[m]) for m in HEADLINE_METRICS}
            arm["final"] = arm["by_round"][R]
            t = tto[(tto.variant == variant) & (tto.selector == sel) & (tto["mode"] == mode)]
            arm["time_to_objective"] = mean_ci(t.time_to_objective)
            arm["fraction_reached_objective"] = float(t.reached.mean())
            fin = g[g["round"] == R]
            if mode == "collaborative":
                arm["peer_changed_by_message_rounds"] = mean_ci(fin.peer_changed_cum)
                arm["messages_per_world"] = mean_ci(fin.messages_cum)
            arm["seconds_per_world"] = float(g.seconds.sum() / g.world.nunique())
            vs["arms"][f"{sel}/{mode}"] = arm
        for sel, g in dv.groupby("selector"):
            if set(g["mode"]) != {"independent", "collaborative"}:
                continue
            pf: dict[str, Any] = {}
            pr: dict[str, Any] = {}
            for m in HEADLINE_METRICS:
                piv = g.pivot_table(index=["world", "round"], columns="mode", values=m)
                d = (piv["collaborative"] - piv["independent"]).rename("d").reset_index()
                pf[m] = paired(d.loc[d["round"] == R, "d"].to_numpy())
                pr[m] = {int(r): mean_ci(dd["d"]) for r, dd in d.groupby("round")}
            t = tto[(tto.variant == variant) & (tto.selector == sel)].pivot_table(
                index="world", columns="mode", values="time_to_objective")
            pf["time_to_objective"] = paired((t["collaborative"] - t["independent"]).to_numpy())
            vs["paired_final"][sel] = pf
            vs["paired_by_round"][sel] = pr
        summary["variants"][variant] = vs
    return summary
