# Firmware: PID controller (separate from firmware.md, which the hardware session owns)

Status: implemented as new files only; the hook into existing files is a documented patch, NOT applied.

- New: `firmware/src/control/PidController.h`, `PidMode.h/.cpp`, `firmware/src/app/PidConsole.h/.cpp`,
  `firmware/test/test_pid/test_main.cpp`, `firmware/docs/PID_PATCH.md`, `firmware/docs/pid_hook.patch`.
- Tests: `pio test -e native -f test_pid` 17/17 (repo, no hook); full native suite on a hooked scratch copy
  130/130 (113 existing + 17). Hooked `pio run -e esp32s3cam_a` on `/tmp/fw_pid_check`: SUCCESS.
- Logs: `firmware/build_logs/pid_*.log`.
- Defaults reproduce the current proportional behaviour (kp 0.25, ki 0, kd 0, mode prop).
- Closed-loop test numbers come from the ThermalTestPlant (arbitrary tau 900 s, gain 0.02 C/s); they are
  simulated, not chamber measurements. Real gains must be tuned on chamber A (see PID_PATCH.md).
- Open for the hardware session: apply `git apply firmware/docs/pid_hook.patch`, flash, run the chamber A
  procedure, record tuned gains and real step response (provenance: real).
