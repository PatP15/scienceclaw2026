// PidConsole — serial console commands for the PID controller. No Arduino dependency: the caller
// prints `reply`. Units: kp fraction/degC, ki fraction/(degC*s), kd fraction*s/degC.
//
//   pid                         print kp ki kd alpha mode and live terms P/I/D/u
//   pid <kp> <ki> <kd> [alpha]  validate, store in NVS key "pid", reply "OK pid kp=.. ki=.. kd=.."
//   pid reset                   clear the integral / derivative state
//   mode pid|prop|hyst          select controller mode (stored), reply "OK mode <m>"
#pragma once

#include <stddef.h>

#include "control/PidMode.h"
#include "hal_interfaces.h"

namespace fa {

// Returns true when the line was a pid/mode command (reply holds the one-line answer, ERR... on
// bad input). Returns false for anything else so the caller's dispatcher can continue.
bool handlePidConsole(int argc, char** argv, PidMode& pid, IKeyValueStore* kv, char* reply, size_t n);

}  // namespace fa
