# Vendored OneWire 2.3.8 (paulstoffregen/OneWire) with one patch

`util/OneWire_direct_gpio.h`: the ESP32 direct-GPIO helpers limited the second GPIO bank to
`pin < 46` (true for the original ESP32). On the ESP32-S3 GPIO46-48 are valid and live in the same
`in1/out1/enable1` registers, so with the stock library GPIO47 (our DS18B20 bus) always read 0 and
`search()`/`reset()` never saw a probe, although a scope-style test showed a presence pulse.
Second fix in the same file: `directModeOutput()` refused pins above 33 (input-only on the
original ESP32), so the reset pulse was never driven on GPIO47. On the S3 all GPIOs are
bidirectional.

Found during chamber A bring-up 2026-10-31. Upstream license unchanged (see `OneWire.h`).
