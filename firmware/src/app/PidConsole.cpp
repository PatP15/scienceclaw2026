#include "app/PidConsole.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace fa {

static bool parseNum(const char* s, float& out) {
  if (!s || !*s) return false;
  char* end = nullptr;
  out = strtof(s, &end);
  return end && *end == 0 && isfinite(out);
}

bool handlePidConsole(int argc, char** argv, PidMode& pid, IKeyValueStore* kv, char* reply, size_t n) {
  if (argc < 1 || !argv[0] || !reply || n == 0) return false;
  const char* cmd = argv[0];
  if (!strcmp(cmd, "mode")) {
    ControlSel s;
    if (argc != 2 || !parseControlSel(argv[1], s)) {
      snprintf(reply, n, "ERR usage: mode pid|prop|hyst (now %s)", controlSelName(pid.selected()));
      return true;
    }
    pid.select(s);
    pid.reset();  // clean start for the newly selected mode
    const bool saved = pid.save(kv);
    snprintf(reply, n, "OK mode %s%s", controlSelName(s), saved ? "" : " (NOT persisted)");
    return true;
  }
  if (strcmp(cmd, "pid") != 0) return false;

  if (argc == 1) {
    const PidGains& g = pid.gains();
    const PidController& c = pid.pid();
    snprintf(reply, n,
             "pid kp=%.4g ki=%.4g kd=%.4g alpha=%.3g mode=%s | P=%.3f I=%.3f D=%.3f u=%.3f%s "
             "(kp frac/C, ki frac/(C*s), kd frac*s/C)",
             (double)g.kp, (double)g.ki, (double)g.kd, (double)g.alpha, controlSelName(pid.selected()),
             (double)c.pTerm(), (double)c.iTerm(), (double)c.dTerm(), (double)c.output(),
             pid.inDeadTimeHold() ? " [dead-time hold]" : "");
    return true;
  }
  if (argc == 2 && !strcmp(argv[1], "reset")) {
    pid.reset();
    snprintf(reply, n, "OK pid reset (I and D cleared)");
    return true;
  }
  if (argc == 4 || argc == 5) {
    PidGains g = pid.gains();  // alpha keeps its value when omitted
    if (!parseNum(argv[1], g.kp) || !parseNum(argv[2], g.ki) || !parseNum(argv[3], g.kd) ||
        (argc == 5 && !parseNum(argv[4], g.alpha)) || !pidGainsValid(g)) {
      snprintf(reply, n,
               "ERR pid out of range: kp 0..%.3g, ki 0..%.3g, kd 0..%.3g, alpha 0..%.3g",
               (double)PidLimits::kKpMax, (double)PidLimits::kKiMax, (double)PidLimits::kKdMax,
               (double)PidLimits::kAlphaMax);
      return true;
    }
    pid.setGains(g);
    pid.reset();
    const bool saved = pid.save(kv);
    snprintf(reply, n, "OK pid kp=%.4g ki=%.4g kd=%.4g alpha=%.3g%s", (double)g.kp, (double)g.ki,
             (double)g.kd, (double)g.alpha, saved ? "" : " (NOT persisted)");
    return true;
  }
  snprintf(reply, n, "ERR usage: pid | pid <kp> <ki> <kd> [alpha] | pid reset");
  return true;
}

}  // namespace fa
