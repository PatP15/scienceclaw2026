// secrets.example.h — copy to include/secrets.h (gitignored) and fill in, OR leave secrets.h
// absent and set everything at runtime over the serial console (stored in NVS):
//   wifi <ssid> <password>
//   backend http://192.168.8.10:8000
//   token <FERMENTAI_DEVICE_TOKEN_A or FERMENTAI_API_TOKEN>
// NEVER commit real values. NEVER put cloud/LLM credentials on the device.
#pragma once

#define FERMENTAI_WIFI_SSID "FermentAI-Lab"
#define FERMENTAI_WIFI_PASS "change-me"
#define FERMENTAI_BACKEND_URL "http://192.168.8.10:8000"
#define FERMENTAI_API_TOKEN "change-me"

// Optional: pin DS18B20 ROM addresses at build time (NVS assignments made with the console
// command `assign` take precedence). 16 hex chars, from the console `scan` output.
// #define DS18B20_ROM_STARTER           "28ff641e0f123456"
// #define DS18B20_ROM_INTERNAL_HEATSINK "28ff641e0f123457"
// #define DS18B20_ROM_EXTERNAL_HEATSINK "28ff641e0f123458"
