"""`uv run python -m simulated_devices --chambers A B --base-url http://127.0.0.1:8000 --interval 5`"""

from simulated_devices.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
