#!/usr/bin/env python3
"""USB-serial bridge between the ESP32 chambers and the local backend (contracts/serial_v1.md).

Same as `uv run python -m fermentai.bridge ...`; all arguments are passed through (-h for help).
"""

import sys

from fermentai.bridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
