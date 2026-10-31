#include "util/TimeUtil.h"

#include <stdio.h>
#include <string.h>

namespace fa {

namespace {
// Howard Hinnant's days_from_civil / civil_from_days (proleptic Gregorian, UTC).
int64_t daysFromCivil(int64_t y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int64_t)doe - 719468;
}
void civilFromDays(int64_t z, int64_t& y, unsigned& m, unsigned& d) {
  z += 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = (unsigned)(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  y = (int64_t)yoe + era * 400;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp + (mp < 10 ? 3 : -9);
  y += (m <= 2);
}
bool digits(const char* s, int n, int& out) {
  out = 0;
  for (int i = 0; i < n; ++i) {
    if (s[i] < '0' || s[i] > '9') return false;
    out = out * 10 + (s[i] - '0');
  }
  return true;
}
}  // namespace

bool parseIso8601Utc(const char* s, int64_t& epoch_ms) {
  if (!s || strlen(s) < 19) return false;
  int Y, M, D, h, mi, se;
  if (!digits(s, 4, Y) || s[4] != '-' || !digits(s + 5, 2, M) || s[7] != '-' ||
      !digits(s + 8, 2, D) || (s[10] != 'T' && s[10] != ' ') || !digits(s + 11, 2, h) ||
      s[13] != ':' || !digits(s + 14, 2, mi) || s[16] != ':' || !digits(s + 17, 2, se))
    return false;
  if (M < 1 || M > 12 || D < 1 || D > 31 || h > 23 || mi > 59 || se > 60) return false;
  const char* p = s + 19;
  int ms = 0;
  if (*p == '.') {
    ++p;
    int nd = 0;
    while (*p >= '0' && *p <= '9') {
      if (nd < 3) ms = ms * 10 + (*p - '0');
      ++nd;
      ++p;
    }
    while (nd > 0 && nd < 3) {
      ms *= 10;
      ++nd;
    }
  }
  // Only UTC designators are accepted; a non-UTC offset is rejected rather than guessed.
  if (*p == 'Z' || *p == 'z') {
    ++p;
  } else if (strncmp(p, "+00:00", 6) == 0 || strncmp(p, "-00:00", 6) == 0) {
    p += 6;
  } else if (*p != '\0') {
    return false;
  }
  if (*p != '\0') return false;
  const int64_t days = daysFromCivil(Y, (unsigned)M, (unsigned)D);
  epoch_ms = ((days * 86400) + h * 3600 + mi * 60 + se) * 1000 + ms;
  return true;
}

void formatIso8601Utc(int64_t epoch_ms, char* buf, size_t n) {
  int64_t secs = epoch_ms / 1000;
  if (epoch_ms < 0 && epoch_ms % 1000) --secs;
  int64_t days = secs / 86400;
  int64_t rem = secs % 86400;
  if (rem < 0) {
    rem += 86400;
    --days;
  }
  int64_t y;
  unsigned m, d;
  civilFromDays(days, y, m, d);
  snprintf(buf, n, "%04d-%02u-%02uT%02d:%02d:%02dZ", (int)y, m, d, (int)(rem / 3600),
           (int)((rem / 60) % 60), (int)(rem % 60));
}

}  // namespace fa
