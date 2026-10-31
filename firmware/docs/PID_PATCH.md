# PID controller: hook patch (for the hardware session to apply)

New files (already in the repo, nothing else changes until the hook is applied):

| File | Role |
|---|---|
| `src/control/PidController.h` | discrete PID, output-fraction units, anti-windup, D on measurement, bumpless `engage()` |
| `src/control/PidMode.h/.cpp` | adapter: deadband + hysteresis, heat<->cool dead-time hold, NVS key `"pid"`, mode selection |
| `src/app/PidConsole.h/.cpp` | `pid` / `mode` console commands (`handlePidConsole`), no Arduino dependency |
| `test/test_pid/test_main.cpp` | 17 native Unity tests |
| `docs/pid_hook.patch` | the unified diff below, ready for `git apply` |

Apply (from the repo root): `git apply firmware/docs/pid_hook.patch`. It touches 4 existing files with
about 60 added lines. Verified on a scratch copy (`/tmp/fw_pid_check`): `pio run -e esp32s3cam_a`
SUCCESS (flash 27.9%, RAM 19.1%) and `pio test -e native` 130/130 (113 existing + 17 new). Logs:
`build_logs/pid_hooked_scratch_esp32s3cam_a.log`, `build_logs/pid_hooked_scratch_native_test.log`.
`pio test -e native -f test_pid` in the repo itself (no hook): `build_logs/pid_native_test.log`.

## Units and defaults
`kp` fraction/degC, `ki` fraction/(degC*s), `kd` fraction*s/degC, `alpha` derivative filter memory
(0 = none). Defaults kp 0.25, ki 0, kd 0, alpha 0.8, mode `prop` = today's behaviour exactly, so
applying the patch changes nothing until `mode pid` is typed. Ranges: kp 0..2, ki 0..0.05,
kd 0..500, alpha 0..0.99. NVS key `"pid"` holds the string `"kp ki kd alpha mode"` (mode 0 hyst,
1 prop, 2 pid); a missing or corrupt entry falls back to defaults.

## What the hook changes
1. `main.cpp`: includes; a global `PidMode pidMode(...)` built from `kPeltierMinFraction`,
   `kPeltierMaxFraction`, `kHeatCoolDeadTimeMs`; in `setup()` after `nvs.begin()`:
   `pidMode.load(&nvs); controller.attachPid(&pidMode);`; in `handleLine()` an extra
   `else if (!strcmp(cmd,"pid") || !strcmp(cmd,"mode"))` branch that calls `handlePidConsole` and prints
   the reply; one help-text line. (Console and control tick run on the same cooperative loop, so no
   extra locking.)
2. `TemperatureController`: a `compute(temp, sp, now_ms)` overload that follows
   `PidMode::selected()`: `hyst`/`prop` set the existing `cfg_.mode` (prop also takes kp from the PID
   gains), `pid` returns `PidMode::compute()` as the same `ControlDemand{dir, fraction}`. Bumpless
   hand-over when switching into PID. `reset()` also resets the PID (I and D cleared).
   (The method in this tree is `compute()`, there is no `update()`.)
3. `ChamberStateMachine::controlTick`: one line, passes `now_ms` to `compute`. The existing
   `may_control` gate still calls `ctl_.reset()` on FAULT, door open, idle, invalid sensor, so the
   integral is cleared in all those cases.

## Safety is unchanged
The PID only proposes a signed demand (+ heat, - cool). The demand goes through
`peltier_.request()` and `PeltierDriver::update()` as before: local setpoint clamp 4-35 C, FAULT
and door idle, heat<->cool dead time, soft-start ramp, `FERMENTAI_PELTIER_MAX_FRACTION` cap (0.30 on
chamber A), arm flags. `PidMode` additionally holds demand at 0 (integral frozen) for the dead time
after the last demand in the other direction, and clamps its output to [min, max] fraction. Deadband:
starts when |u| >= 0.15, stops when |u| < 0.075, so with P-only it idles around 0.3 C from the
setpoint rather than at the setpoint.

## Console
```
pid                       -> pid kp=0.25 ki=0 kd=0 alpha=0.8 mode=prop | P=.. I=.. D=.. u=..
pid 0.5 0.0004 0 [0.8]    -> OK pid kp=0.5 ki=0.0004 kd=0 alpha=0.8      (validated, stored in NVS)
pid reset                 -> OK pid reset (I and D cleared)
mode pid|prop|hyst        -> OK mode pid                                (stored in NVS)
```
Bad values answer `ERR pid out of range: ...` and change nothing.

## Flash and test on chamber A (Peltier armed, 30% cap, stay in the room)
1. Apply the patch, `cd firmware && pio test -e native` (all green), then
   `pio run -e esp32s3cam_a -t upload` (env already has `ALLOW_PELTIER_OUTPUT=1`, cap 0.30).
2. Open the monitor, `status`: confirm `armed(peltier=1)`, chamber temperature valid, door closed,
   thermostats on both heatsinks present (no heatsink probes: they are the only hard protection).
3. Baseline: `pid` shows `mode=prop`; `setpoint 25` (or room + 3 C) and watch `status` for a few
   minutes. Nothing differs from before.
4. `mode pid` (still kp 0.25, ki 0): behaviour about the same as prop, plus the deadband.
5. Add the integral: `pid 0.25 0.0003 0` then `pid` once a minute; expect I to rise slowly and
   the steady offset to shrink. Keep ki small: the plant time constant is minutes, and with a 0.30 cap
   the output saturates for large steps (anti-windup keeps I bounded). Try `pid 0.4 0.0005 0`
   only after the first run is clean.
6. Leave kd at 0 on the first runs (the DS18B20/AHT21 reading is quantised and noisy); try
   `pid 0.4 0.0005 20 0.9` later and watch for output chatter.
7. Direction change test (optional, stay present): change the setpoint across the current temperature;
   the output must go to 0 and stay there for 60 s (`status` shows switching), then the other side.
8. Abort at any time: `mode prop` (or `disarm peltier`). `pid 0.25 0 0` restores defaults.
   Reboot keeps the stored gains and mode.
