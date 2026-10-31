#!/usr/bin/env python3
"""Step-test recorder, FOPDT fit + SIMC PID tuner, and closed-loop simulator. See docs/control/PID_TUNING.md."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np  # noqa: E402

from fermentai.control import tuning as T  # noqa: E402


def cmd_run(a) -> int:
    cfg = T.StepConfig(chamber=a.chamber, t_from=a.t_from, t_to=a.t_to, duration_min=a.duration, settle_min=a.settle_min, out_dir=a.out_dir)
    api = None if a.dry_run else T.HttpApi(a.base_url, T.load_token())
    if a.dry_run:
        orig = None
        try:
            orig = T.HttpApi(a.base_url, T.load_token()).state(a.chamber).get("desired_setpoint_c")
        except Exception:  # noqa: BLE001
            pass
        print(T.plan_text(cfg, orig))
        return 0
    print(T.plan_text(cfg).replace("(DRY RUN - nothing is sent)", "(LIVE)"))
    try:
        res = T.StepTest(api, cfg).run()
    except T.StepAbort as e:
        print(f"REFUSED: {e}"); return 2
    print(f"{res.status}: {res.reason}; {res.n_records} records -> {res.csv_path}; setpoint restored={res.restored}")
    return 0 if res.status == "completed" else 1


def cmd_fit(a) -> int:
    data = T.read_step_csv(a.csv)
    t, y, u = T.select_segment(data, saturated_only=a.saturated_only, cap=a.cap if a.saturated_only else None)
    fit = T.fit_fopdt(t, y, u)
    rec = T.recommend(fit, cap=a.cap)
    chamber = a.chamber or (Path(a.csv).stem.split("_")[1] if "_" in Path(a.csv).stem else "X")
    out = Path(a.out or f"data/control/pid_{chamber}.json")
    doc = T.write_gains_json(out, chamber, fit, rec, source_csv=str(a.csv), cap=a.cap, provenance=a.provenance)
    png = out.with_suffix(".png")
    T.plot_fit(png, t, y, u, fit, rec["pid_conservative"] if a.plot_conservative else rec["pid"], cap=a.cap, title=f"chamber {chamber}: {Path(a.csv).name}")
    print(f"FOPDT: K={fit.K:.3f} degC/fraction  tau={fit.tau:.1f} s  theta={fit.theta:.1f} s  rmse={fit.rmse:.3f} degC  (n={fit.n})")
    for k, g in rec.items():
        print(f"  {k:17s} Kp={g.kp:.5g} /degC  Ki={g.ki:.5g} /(degC*s)  Kd={g.kd:.5g} s/degC   tau_c={g.tau_c:.0f}s  ->  pid {g.kp:.5g} {g.ki:.5g} {g.kd:.5g}")
    print(f"anti-windup: output +/-{a.cap:g}, integral clamp {rec['pid'].anti_windup['integral_state_limit']:.4g} degC*s")
    print(f"wrote {out} and {png}")
    return 0


def cmd_simulate(a) -> int:
    if a.gains_json:
        doc = json.loads(Path(a.gains_json).read_text())
        f = doc["fopdt"]; K, tau, th = f["K"], f["tau"], f["theta"]
        g = doc["recommended"][a.variant]; gains = (g["kp"], g["ki"], g["kd"]); g = T.PidGains(**g)
    else:
        K, tau, th = a.K, a.tau, a.theta
        g = {"pi": T.simc_pi, "pid": T.simc_pid}[a.variant.split("_")[0]](K, tau, th, cap=a.cap)
    r = T.simulate_closed_loop(K, tau, th, g, setpoint=a.to, y0=a.start, dt=a.dt, t_end=a.minutes * 60, cap=a.cap)
    m = T.step_metrics(r["t"], r["y"], a.to, a.start)
    print(f"K={K:.3g} tau={tau:.0f}s theta={th:.0f}s  Kp={g.kp:.4g} Ki={g.ki:.4g} Kd={g.kd:.4g}  cap={a.cap:g}")
    print(f"overshoot {m.get('overshoot_pct', 0):.1f} %  rise(90%) {m.get('rise_time_90_s')} s  settle(5%) {m.get('settling_5pct_s')} s  peak output {np.max(np.abs(r['u'])):.3f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="record a live step test"); r.add_argument("--chamber", default="A")
    r.add_argument("--from", dest="t_from", type=float, required=True); r.add_argument("--to", dest="t_to", type=float, required=True)
    r.add_argument("--duration", type=float, default=45.0, help="minutes after the step"); r.add_argument("--settle-min", type=float, default=20.0)
    r.add_argument("--base-url", default="http://127.0.0.1:8000"); r.add_argument("--out-dir", default="data/control"); r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    f = sub.add_parser("fit", help="fit FOPDT + SIMC gains from a CSV"); f.add_argument("csv"); f.add_argument("--chamber"); f.add_argument("--out")
    f.add_argument("--cap", type=float, default=T.DEFAULT_CAP); f.add_argument("--saturated-only", action="store_true")
    f.add_argument("--plot-conservative", action="store_true"); f.add_argument("--provenance", default="real"); f.set_defaults(fn=cmd_fit)
    s = sub.add_parser("simulate", help="closed-loop FOPDT + PID simulation")
    s.add_argument("--gains-json"); s.add_argument("--variant", default="pid", help="pi|pid|pi_conservative|pid_conservative")
    s.add_argument("--K", type=float, default=20.0); s.add_argument("--tau", type=float, default=600.0); s.add_argument("--theta", type=float, default=30.0)
    s.add_argument("--start", type=float, default=22.0); s.add_argument("--to", type=float, default=26.0); s.add_argument("--minutes", type=float, default=60.0)
    s.add_argument("--dt", type=float, default=1.0); s.add_argument("--cap", type=float, default=T.DEFAULT_CAP); s.set_defaults(fn=cmd_simulate)
    a = p.parse_args(); return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
