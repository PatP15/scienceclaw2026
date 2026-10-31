#include "simulation/MockActuators.h"

#include <string.h>

namespace fa {

bool MockKeyValueStore::getString(const char* key, char* out, size_t n) {
  auto it = s_.find(key);
  if (it == s_.end() || n == 0) return false;
  strncpy(out, it->second.c_str(), n - 1);
  out[n - 1] = 0;
  return true;
}
bool MockKeyValueStore::putString(const char* key, const char* value) {
  s_[key] = value ? value : "";
  return true;
}
bool MockKeyValueStore::getFloat(const char* key, float& out) {
  auto it = f_.find(key);
  if (it == f_.end()) return false;
  out = it->second;
  return true;
}
bool MockKeyValueStore::putFloat(const char* key, float value) {
  f_[key] = value;
  return true;
}
bool MockKeyValueStore::getI64(const char* key, int64_t& out) {
  auto it = i_.find(key);
  if (it == i_.end()) return false;
  out = it->second;
  return true;
}
bool MockKeyValueStore::putI64(const char* key, int64_t value) {
  i_[key] = value;
  return true;
}
bool MockKeyValueStore::remove(const char* key) {
  return (s_.erase(key) + f_.erase(key) + i_.erase(key)) > 0;
}

}  // namespace fa
