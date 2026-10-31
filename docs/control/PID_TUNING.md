# PID tuning from a step test

Tools: `scripts/pid_step_test.py` (`run`, `fit`, `simulate`), library `src/fermentai/control/tuning.py`.
Units everywhere: output = signed Peltier output fraction (+ heat, - cool), gains in output-fraction units:
Kp [1/°C], Ki [1/(°C·s)], Kd [s/°C]. Plant: first-order plus dead time, gain K [°C per unit fraction], τ and θ in seconds.

## Procedure
1. **Arm the Peltier on the board** (the bridge never arms anything). Put the heatsink probes on (recommended) and stay in the room.
2. Preview: `uv run python scripts/pid_step_test.py run --chamber A --from 22 --to 26 --dry-run`
3. Record: `uv run python scripts/pid_step_test.py run --chamber A --from 22 --to 26 --duration 45`
   - settles at `--from` (|T−sp| < 0.3 °C for 2 min, or gives up after `--settle-min`), steps, logs every telemetry record to
     `data/control/step_<chamber>_<UTC>.csv`, then restores the original setpoint.
   - Aborts (and restores) on Ctrl-C, a device fault flag, or air temperature outside [from−6, to+6] °C. Refuses/aborts if the Peltier is
     disarmed or output stays 0 for 60 s after the step. The API's 3 °C step limit is honoured: a +4 °C step is sent as 3 °C then 1 °C (~6 s apart).
   - Token: `FERMENTAI_API_TOKEN` env, else `.env`, else the dev default.
4. Fit: `uv run python scripts/pid_step_test.py fit data/control/step_A_<ts>.csv` → prints K, τ, θ and SIMC gains, writes
   `data/control/pid_A.json` (+ `.png`: data, fitted model, simulated closed loop). The fit uses the measured output as the model input, so a
   saturated closed-loop-P step (output at the cap) is effectively an open-loop step; `--saturated-only` restricts to that window.
5. Sanity: `uv run python scripts/pid_step_test.py simulate --gains-json data/control/pid_A.json --variant pid_conservative`
6. Start with a **conservative** variant (τc = 3θ), then try the tight one (τc = θ) if the simulated and real responses agree.

## Formulas (SIMC / Skogestad)
- PI: Kp = τ / (K (τc+θ)), τI = min(τ, 4(τc+θ)).
- PID (half-rule, ideal form): Kp = (τ+θ/2) / (K (τc+θ/2)), τI = min(τ+θ/2, 4(τc+θ/2)), τD = τθ/(2τ+θ). Ki = Kp/τI, Kd = Kp·τD.
- Anti-windup in the JSON: output limits ±cap (default 0.30, matching the device cap), integral-of-error clamp ±cap/Ki, back-calculation Tt = √(τI·τD).
- Caveats: K is only identified in the explored operating range; the plant is nonlinear (heating vs cooling asymmetry, ambient drift). Use one step per
  direction if the firmware controller treats heating and cooling separately. Provenance of the JSON is `real` only if the CSV came from the physical device.

## Mapping to the firmware PID (written concurrently)
Console: `mode pid|prop|hyst` and `pid <kp> <ki> <kd>` with values in output-fraction per °C (same units as above).
`pid.json` contains `firmware_console_hint` per variant, e.g. `pid 0.20285 0.0003891 3.9788`. The existing proportional mode uses
`kProportionalGainPerC = 0.25` (firmware/include/device_config.h), a useful reference for the order of magnitude of Kp.
Push gains manually from the console once PID mode exists, with the device cap still active; the dashboard/API never sends gains, and nothing
here can bypass the firmware thermal/electrical safety checks.

## Safety
Output stays capped (~30 %) by the firmware; keep heatsink probes attached; do not leave a running test unattended; Ctrl-C restores the setpoint.
