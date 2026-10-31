"""Step-test recording logic, FOPDT identification and SIMC (Skogestad) PI/PID tuning.

Conventions
-----------
* Output ``u`` is the SIGNED Peltier output fraction: +fraction while heating, -fraction while cooling, 0 idle.
  Plant gain K is therefore in degC per unit output fraction (positive for a normal plant).
* Controller gains are in output-fraction units: Kp [1/degC], Ki [1/(degC*s)], Kd [s/degC].
* Provenance of everything produced here: ``real`` when fitted from a CSV recorded on the live device,
  otherwise ``simulated``. Nothing here is a physical measurement by itself.
"""
from __future__ import annotations

import csv
import json
import math
import re
import signal  # noqa: F401  (KeyboardInterrupt is the abort path)
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
from scipy.optimize import least_squares

CSV_FIELDS = ["t_s", "iso_time", "phase", "chamber_temp_c", "starter_temp_c", "setpoint_c",
              "peltier_mode", "output_fraction", "output_signed", "source", "seq"]
FAULT_RE = re.compile(r"fault|over_?temp|thermal_runaway|e_?stop|shutdown|safety|trip", re.I)
DEFAULT_CAP = 0.30


# ============================================================================ signal helpers
def signed_output(mode: str | None, fraction: float | None) -> float:
    f = 0.0 if fraction is None or not math.isfinite(fraction) else float(fraction)
    return -abs(f) if mode and str(mode).lower().startswith("cool") else abs(f)


# ============================================================================ FOPDT model
def simulate_fopdt(t: np.ndarray, u: np.ndarray, K: float, tau: float, theta: float, y0: float,
                   u_base: float = 0.0) -> np.ndarray:
    """y' = (K*(u(t-theta)-u_base) + y0 - y)/tau, zero-order-hold input, exact per-sample update."""
    t = np.asarray(t, float); u = np.asarray(u, float)
    y = np.empty_like(t); y[0] = y0
    tau = max(float(tau), 1e-6)
    # linear interpolation of the delayed input keeps the cost smooth in theta (needed by the optimiser)
    dt0 = float(t[1] - t[0]) if len(t) > 1 else 1.0
    ud = np.interp(t - theta, np.concatenate([[t[0] - dt0], t]), np.concatenate([[u_base], u]), left=u_base)
    yss = y0 + K * (ud - u_base)
    a = np.exp(-np.diff(t) / tau)
    for k in range(len(t) - 1):
        y[k + 1] = yss[k] + (y[k] - yss[k]) * a[k]
    return y


@dataclass
class FopdtFit:
    K: float
    tau: float
    theta: float
    y0: float
    u_base: float
    rmse: float
    n: int
    method: str

    def as_dict(self) -> dict:
        return asdict(self)


def fit_fopdt(t: np.ndarray, y: np.ndarray, u: np.ndarray, *, seed_K: float | None = None) -> FopdtFit:
    """Least-squares FOPDT fit of y(t) to the (measured) input u(t).

    K is initialised from delta-temperature / delta-output; tau and theta by least squares.
    If u is constant over the record (open-loop step) the step is assumed at t[0] from u_base = 0.
    """
    t = np.asarray(t, float) - float(t[0]); y = np.asarray(y, float); u = np.asarray(u, float)
    if len(t) < 8:
        raise ValueError("need at least 8 samples to fit")
    y0 = float(np.mean(y[: max(1, min(5, len(y) // 10))]))
    constant = float(np.ptp(u)) < 1e-9
    u_base = 0.0 if constant else float(u[0])
    du = float(u[-1] - u_base) if constant else float(np.max(np.abs(u - u_base)) * np.sign(u[np.argmax(np.abs(u - u_base))] - u_base))
    if abs(du) < 1e-6:
        raise ValueError("output never moved away from its baseline; nothing to identify")
    dy = float(np.mean(y[-max(3, len(y) // 10):]) - y0)
    K0 = seed_K if seed_K is not None else dy / du
    if not math.isfinite(K0) or K0 == 0:
        K0 = 10.0
    span = float(t[-1])
    dts = np.diff(t)
    dt_med = float(np.median(dts[dts > 0])) if np.any(dts > 0) else 1.0
    best = None
    for tau0 in (span / 8, span / 3, span / 15):
        for th0 in (dt_med * 2, span / 40):
            def resid(p, _u=u):
                return simulate_fopdt(t, _u, p[0], p[1], p[2], y0, u_base) - y
            lo = [-abs(K0) * 20 - 1, dt_med, 0.0]; hi = [abs(K0) * 20 + 1, span * 5, span / 2]
            x0 = [K0, min(max(tau0, 2 * dt_med), span * 4), min(max(th0, dt_med), span / 3)]
            try:
                r = least_squares(resid, x0, bounds=(lo, hi), x_scale=[max(abs(K0), 1), max(span / 5, 1), max(span / 20, 1)], diff_step=1e-3)
            except Exception:  # noqa: BLE001
                continue
            if best is None or r.cost < best.cost:
                best = r
    if best is None:
        raise RuntimeError("FOPDT fit failed")
    K, tau, theta = (float(v) for v in best.x)
    rmse = math.sqrt(2 * best.cost / len(t))
    return FopdtFit(K, tau, theta, y0, u_base, rmse, len(t), "constant-input step" if constant else "measured-input least squares")


# ============================================================================ SIMC tuning
@dataclass
class PidGains:
    kp: float
    ki: float
    kd: float
    tau_c: float
    tau_i: float
    tau_d: float
    variant: str
    anti_windup: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _anti_windup(ki: float, kp: float, tau_i: float, tau_d: float, cap: float) -> dict:
    tt = math.sqrt(tau_i * tau_d) if tau_d > 0 else tau_i / 2
    return {"output_min": -cap, "output_max": cap, "integral_state_limit": (cap / ki) if ki > 0 else None,
            "integral_state_unit": "degC*s", "back_calculation_tt_s": tt,
            "note": "clamp output to +/-cap; clamp integral of error to +/-limit; or back-calculate with Tt"}


def simc_pi(K: float, tau: float, theta: float, tau_c: float | None = None, cap: float = DEFAULT_CAP, variant: str = "simc_pi") -> PidGains:
    """SIMC PI: Kc = tau/(K (tau_c+theta)), tau_I = min(tau, 4 (tau_c+theta)); default tau_c = theta."""
    tc = theta if tau_c is None else tau_c
    kc = tau / (K * (tc + theta))
    ti = min(tau, 4 * (tc + theta))
    ki = kc / ti
    return PidGains(kc, ki, 0.0, tc, ti, 0.0, variant, _anti_windup(ki, kc, ti, 0.0, cap))


def simc_pid(K: float, tau: float, theta: float, tau_c: float | None = None, cap: float = DEFAULT_CAP, variant: str = "simc_pid") -> PidGains:
    """SIMC PID for FOPDT (half-rule, ideal form): Kc = (tau+theta/2)/(K (tau_c+theta/2)),
    tau_I = min(tau+theta/2, 4 (tau_c+theta/2)), tau_D = tau*theta/(2 tau+theta)."""
    tc = theta if tau_c is None else tau_c
    kc = (tau + theta / 2) / (K * (tc + theta / 2))
    ti = min(tau + theta / 2, 4 * (tc + theta / 2))
    td = tau * theta / (2 * tau + theta)
    ki, kd = kc / ti, kc * td
    return PidGains(kc, ki, kd, tc, ti, td, variant, _anti_windup(ki, kc, ti, td, cap))


def recommend(fit: FopdtFit, cap: float = DEFAULT_CAP) -> dict[str, PidGains]:
    th = max(fit.theta, 1e-6)
    return {
        "pi": simc_pi(fit.K, fit.tau, th, cap=cap, variant="simc_pi (tau_c = theta)"),
        "pid": simc_pid(fit.K, fit.tau, th, cap=cap, variant="simc_pid (tau_c = theta)"),
        "pi_conservative": simc_pi(fit.K, fit.tau, th, tau_c=3 * th, cap=cap, variant="simc_pi conservative (tau_c = 3 theta)"),
        "pid_conservative": simc_pid(fit.K, fit.tau, th, tau_c=3 * th, cap=cap, variant="simc_pid conservative (tau_c = 3 theta)"),
    }


# ============================================================================ closed-loop sim
def simulate_closed_loop(K: float, tau: float, theta: float, gains: PidGains | tuple[float, float, float], *,
                         setpoint: float, y0: float, dt: float = 1.0, t_end: float = 3600.0, cap: float = DEFAULT_CAP,
                         u_base: float = 0.0) -> dict[str, np.ndarray]:
    """FOPDT plant + PID (derivative on measurement, clamped output, back-calculation anti-windup)."""
    kp, ki, kd = (gains.kp, gains.ki, gains.kd) if isinstance(gains, PidGains) else gains
    tt = (gains.anti_windup.get("back_calculation_tt_s") if isinstance(gains, PidGains) else None) or 60.0
    n = int(t_end / dt) + 1
    t = np.arange(n) * dt
    y = np.empty(n); u = np.empty(n); y[0] = y0
    nd = int(round(theta / dt))
    buf = [u_base] * (nd + 1)
    integ = 0.0
    a = math.exp(-dt / max(tau, 1e-6))
    prev = y0
    for k in range(n):
        e = setpoint - y[k]
        d = -(y[k] - prev) / dt if k else 0.0
        prev = y[k]
        v = kp * e + integ + kd * d
        uk = min(max(v, -cap), cap)
        integ += dt * (ki * e + (uk - v) / tt)
        u[k] = uk
        buf.append(uk); ud = buf.pop(0)
        if k + 1 < n:
            y[k + 1] = (y0 + K * (ud - u_base)) + (y[k] - (y0 + K * (ud - u_base))) * a
    return {"t": t, "y": y, "u": u}


def step_metrics(t: np.ndarray, y: np.ndarray, setpoint: float, y0: float) -> dict:
    span = setpoint - y0
    if abs(span) < 1e-9:
        return {}
    frac = (y - y0) / span
    over = max(0.0, float(np.max(frac)) - 1.0) * 100
    rise = next((float(t[i]) for i, f in enumerate(frac) if f >= 0.9), None)
    out = np.where(np.abs(frac - 1.0) > 0.05)[0]
    settle = None if len(out) and out[-1] == len(t) - 1 else (float(t[out[-1] + 1]) if len(out) else 0.0)
    return {"overshoot_pct": over, "rise_time_90_s": rise, "settling_5pct_s": settle}


# ============================================================================ CSV IO
def read_step_csv(path: str | Path) -> dict[str, np.ndarray]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: empty CSV")
    def col(name, cast=float, default=float("nan")):
        out = []
        for r in rows:
            try:
                out.append(cast(r[name]))
            except (KeyError, TypeError, ValueError):
                out.append(default)
        return out
    u = col("output_signed") if "output_signed" in rows[0] else [signed_output(r.get("peltier_mode"), _f(r.get("output_fraction"))) for r in rows]
    return {"t": np.array(col("t_s")), "y": np.array(col("chamber_temp_c")), "u": np.array(u),
            "sp": np.array(col("setpoint_c")), "phase": np.array([r.get("phase", "") for r in rows])}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def select_segment(data: dict[str, np.ndarray], *, saturated_only: bool = False, cap: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows of the step phase (all rows if no phase column); optionally only while output sits at its maximum."""
    t, y, u, ph = data["t"], data["y"], data["u"], data["phase"]
    m = np.isfinite(t) & np.isfinite(y) & np.isfinite(u)
    if np.any(ph == "step"):
        m &= ph == "step"
    t, y, u = t[m], y[m], u[m]
    if saturated_only and len(u):
        top = cap if cap is not None else float(np.max(np.abs(u)))
        sat = np.abs(u) >= 0.98 * top
        if sat.any():
            first = int(np.argmax(sat))
            end = len(u) if sat[first:].all() else first + int(np.argmin(sat[first:]))
            t, y, u = t[first:end], y[first:end], u[first:end]
    return t, y, u


def write_gains_json(path: str | Path, chamber: str, fit: FopdtFit, rec: dict[str, PidGains], *, source_csv: str, cap: float,
                     provenance: str = "real") -> dict:
    doc = {"chamber_id": chamber, "provenance": provenance, "source_csv": source_csv, "output_cap": cap,
           "units": {"K": "degC per unit output fraction", "tau": "s", "theta": "s", "kp": "fraction/degC",
                     "ki": "fraction/(degC*s)", "kd": "fraction*s/degC"},
           "fopdt": fit.as_dict(), "recommended": {k: v.as_dict() for k, v in rec.items()},
           "firmware_console_hint": {k: f"pid {v.kp:.5g} {v.ki:.5g} {v.kd:.5g}" for k, v in rec.items()},
           "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, indent=2))
    return doc


def plot_fit(path: str | Path, t, y, u, fit: FopdtFit, gains: PidGains, *, cap: float, title: str = "") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t0 = np.asarray(t) - t[0]
    ym = simulate_fopdt(t0, u, fit.K, fit.tau, fit.theta, fit.y0, fit.u_base)
    sp = fit.y0 + fit.K * (float(np.max(np.abs(u))) * np.sign(u[np.argmax(np.abs(u))]) - fit.u_base) * 0.5
    sp = float(np.mean(y[-max(3, len(y) // 10):])) if abs(sp - fit.y0) < 0.1 else sp
    cl = simulate_closed_loop(fit.K, fit.tau, fit.theta, gains, setpoint=sp, y0=fit.y0, dt=max(1.0, fit.tau / 500), t_end=float(t0[-1]) * 1.5, cap=cap)
    fig, ax = plt.subplots(2, 1, figsize=(9, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax[0].plot(t0 / 60, y, ".", ms=3, label="measured air temp")
    ax[0].plot(t0 / 60, ym, "-", label=f"FOPDT fit K={fit.K:.1f} tau={fit.tau:.0f}s theta={fit.theta:.0f}s")
    ax[0].plot(cl["t"] / 60, cl["y"], "--", label=f"simulated closed loop ({gains.variant})")
    ax[0].axhline(sp, color="grey", lw=.8, ls=":")
    ax[0].set_ylabel("degC"); ax[0].legend(fontsize=8); ax[0].set_title(title or "step test + fit")
    ax[1].plot(t0 / 60, u, label="measured output (signed)"); ax[1].plot(cl["t"] / 60, cl["u"], "--", label="simulated PID output")
    ax[1].set_xlabel("minutes"); ax[1].set_ylabel("fraction"); ax[1].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


# ============================================================================ live step test
class Api(Protocol):
    def state(self, chamber: str) -> dict: ...
    def set_setpoint(self, chamber: str, c: float) -> dict: ...


class HttpApi:
    """Thin httpx client for the FermentAI API (operator token for writes)."""
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        import httpx
        self.c = httpx.Client(base_url=base_url, timeout=timeout)
        self.h = {"Authorization": f"Bearer {token}"}

    def state(self, chamber: str) -> dict:
        r = self.c.get(f"/api/v1/chambers/{chamber}/state"); r.raise_for_status(); return r.json()

    def set_setpoint(self, chamber: str, c: float) -> dict:
        r = self.c.post(f"/api/v1/chambers/{chamber}/setpoint", headers=self.h,
                        json={"setpoint_c": c, "source": "operator", "rationale": "pid_step_test"})
        r.raise_for_status(); return r.json()


def load_token(env_path: str | Path = ".env") -> str:
    import os
    tok = os.environ.get("FERMENTAI_API_TOKEN", "").strip()
    if not tok and Path(env_path).exists():
        for line in Path(env_path).read_text().splitlines():
            m = re.match(r"\s*FERMENTAI_API_TOKEN\s*=\s*([^#\s]*)", line)
            if m and m.group(1):
                tok = m.group(1).strip("'\""); break
    return tok or "change-me-local-device-token"


class StepAbort(Exception):
    pass


@dataclass
class StepConfig:
    chamber: str
    t_from: float
    t_to: float
    duration_min: float = 45.0
    settle_min: float = 20.0
    settle_band_c: float = 0.3
    settle_hold_s: float = 120.0
    arm_check_s: float = 60.0
    margin_c: float = 6.0
    poll_s: float = 1.0
    out_dir: str = "data/control"


@dataclass
class StepResult:
    status: str                 # completed | aborted
    reason: str
    csv_path: str | None
    n_records: int
    restored: bool
    original_setpoint: float | None


def plan_text(cfg: StepConfig, original: float | None = None) -> str:
    lo, hi = cfg.t_from - cfg.margin_c, cfg.t_to + cfg.margin_c
    return "\n".join([
        f"Step-test plan for chamber {cfg.chamber}  (DRY RUN - nothing is sent)",
        f"  0. Read telemetry; remember the current setpoint ({'?' if original is None else f'{original:g}'} C) to restore at the end.",
        f"  1. Set setpoint -> {cfg.t_from:g} C; wait until |T - sp| < {cfg.settle_band_c:g} C for {cfg.settle_hold_s/60:g} min (give up waiting after {cfg.settle_min:g} min).",
        f"  2. Step setpoint -> {cfg.t_to:g} C (API step limit is applied; larger steps are sent in allowed increments >= 5 s apart).",
        f"  3. Refuse/abort if Peltier is disarmed or output stays 0 for {cfg.arm_check_s:g} s after the step.",
        f"  4. Log every telemetry record for {cfg.duration_min:g} min to {cfg.out_dir}/step_{cfg.chamber}_<UTC timestamp>.csv",
        f"  5. Abort (and restore the setpoint) on Ctrl-C, a fault flag, or air temperature outside [{lo:g}, {hi:g}] C.",
        "  6. Restore the original setpoint.",
        "  Safety: output is capped by the device (~30 %); the Peltier must be armed on the board by an operator; stay in the room."])


class StepTest:
    def __init__(self, api: Api, cfg: StepConfig, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print,
                 now_iso: Callable[[], str] | None = None):
        self.api, self.cfg, self.clock, self.sleep, self.log = api, cfg, clock, sleep, log
        self.now_iso = now_iso or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.t0 = clock(); self._seen: set = set(); self.n = 0; self.fh = None; self.writer = None
        self.phase = "settle"; self.csv_path: str | None = None

    # ---- telemetry
    def _tel(self) -> dict | None:
        st = self.api.state(self.cfg.chamber)
        tel = st.get("latest_telemetry")
        if not tel or st.get("telemetry_stale"):
            return None
        return tel

    def _record(self, tel: dict) -> dict | None:
        key = (tel.get("sequence_number"), tel.get("id"), tel.get("received_at_utc"))
        if key in self._seen:
            return None
        self._seen.add(key)
        frac = tel.get("peltier_output_fraction")
        frac = None if frac is None else float(frac)
        mode = tel.get("peltier_mode")
        rec = {"t_s": f"{self.clock() - self.t0:.2f}", "iso_time": tel.get("timestamp_utc") or tel.get("received_at_utc") or self.now_iso(),
               "phase": self.phase, "chamber_temp_c": tel.get("chamber_temperature_c"), "starter_temp_c": tel.get("starter_temperature_c"),
               "setpoint_c": tel.get("temperature_setpoint_c"), "peltier_mode": mode,
               "output_fraction": frac, "output_signed": signed_output(mode, frac), "source": tel.get("source"), "seq": tel.get("sequence_number")}
        if self.writer:
            self.writer.writerow(rec); self.fh.flush()
        self.n += 1
        return rec

    def _check_safety(self, tel: dict, lo: float, hi: float) -> None:
        flags = [f for f in (tel.get("quality_flags") or []) if FAULT_RE.search(str(f))]
        if flags:
            raise StepAbort(f"fault flag(s) from device: {flags}")
        T = tel.get("chamber_temperature_c")
        if T is not None and not (lo <= float(T) <= hi):
            raise StepAbort(f"air temperature {float(T):.2f} C left the allowed window [{lo:g}, {hi:g}] C")

    def _poll(self, lo: float, hi: float) -> dict | None:
        tel = self._tel()
        if tel is None:
            return None
        self._check_safety(tel, lo, hi)
        self._record(tel)
        return tel

    def _command(self, target: float) -> float:
        """Send a setpoint honouring API step/rate limits; returns the applied value."""
        applied = None
        for _ in range(6):
            r = self.api.set_setpoint(self.cfg.chamber, target)
            applied = r.get("applied_c")
            if r.get("rejected") and "rate limit" not in str(r.get("reason", "")):
                raise StepAbort(f"setpoint rejected: {r.get('reason')}")
            if applied is not None and abs(applied - target) < 1e-6:
                return applied
            self.sleep(5.5)
        raise StepAbort(f"could not reach setpoint {target:g} C (last applied {applied})")

    # ---- run
    def run(self) -> StepResult:
        cfg = self.cfg
        lo, hi = cfg.t_from - cfg.margin_c, cfg.t_to + cfg.margin_c
        st0 = self.api.state(cfg.chamber)
        original = st0.get("desired_setpoint_c")
        if not st0.get("latest_telemetry") or st0.get("telemetry_stale"):
            raise StepAbort("no fresh telemetry for this chamber; is the device/bridge running?")
        Path(cfg.out_dir).mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.csv_path = str(Path(cfg.out_dir) / f"step_{cfg.chamber}_{stamp}.csv")
        self.fh = open(self.csv_path, "w", newline="")
        self.writer = csv.DictWriter(self.fh, fieldnames=CSV_FIELDS); self.writer.writeheader()
        status, reason, restored = "completed", "duration elapsed", False
        try:
            self._command(cfg.t_from)
            self.log(f"settling at {cfg.t_from:g} C ...")
            t_start = self.clock(); in_band_since = None
            while self.clock() - t_start < cfg.settle_min * 60:
                tel = self._poll(lo, hi)
                if tel and tel.get("chamber_temperature_c") is not None and abs(float(tel["chamber_temperature_c"]) - cfg.t_from) < cfg.settle_band_c:
                    in_band_since = in_band_since if in_band_since is not None else self.clock()
                    if self.clock() - in_band_since >= cfg.settle_hold_s:
                        break
                else:
                    in_band_since = None
                self.sleep(cfg.poll_s)
            else:
                self.log("settle timeout reached; stepping anyway")
            self.phase = "step"
            t_step = self.clock()
            self.log(f"step -> {cfg.t_to:g} C")
            self._command(cfg.t_to)
            armed_seen = False
            while self.clock() - t_step < cfg.duration_min * 60:
                tel = self._poll(lo, hi)
                if tel:
                    mode = str(tel.get("peltier_mode") or "").lower()
                    frac = tel.get("peltier_output_fraction")
                    if frac and float(frac) > 0 and "disarm" not in mode:
                        armed_seen = True
                if not armed_seen and self.clock() - t_step >= cfg.arm_check_s:
                    raise StepAbort(f"Peltier disarmed / zero output for {cfg.arm_check_s:g} s after the step - arm it on the board and retry")
                self.sleep(cfg.poll_s)
        except StepAbort as e:
            status, reason = "aborted", str(e)
        except KeyboardInterrupt:
            status, reason = "aborted", "interrupted (Ctrl-C)"
        finally:
            if original is not None:
                try:
                    self._command(float(original)); restored = True
                    self.log(f"restored setpoint {float(original):g} C")
                except Exception as e:  # noqa: BLE001
                    self.log(f"WARNING: could not restore setpoint {original}: {e}")
            if self.fh:
                self.fh.close()
        return StepResult(status, reason, self.csv_path, self.n, restored, original)
