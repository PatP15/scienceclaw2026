"""Shared fixtures for the end-to-end integration suite (PLAN §16, PLAN_MASTER §14).

Everything here uses the REAL components: the FastAPI app (`fermentai.api.app:app` under uvicorn,
or `create_app` in-process), the real `simulated_devices` package (as a subprocess running its
CLI), the real agents pipeline and the real fermentation kinetics. Nothing is mocked.

Isolation: every server gets a temp SQLite DB, a temp findings log and a temp data dir whose
read-only artifact folders (predictions, alchemi, stl, demos) are symlinks to the repo's `data/`.
Images and runtime state are written under the temp dir, never into the repo.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "integration-test-token"
ARTIFACT_DIRS = ("predictions", "alchemi", "stl", "demos", "benchmarks", "figures")

# PLAN_MASTER §14, verbatim.
JUDGE_SENTENCE = ("Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, "
                  "but the crumb is too dense. I'd like a more sour loaf that's still fluffy.")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: slow end-to-end test (real server/devices)")


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_data_dir(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_DIRS:
        src = REPO_ROOT / "data" / name
        if src.is_dir() and not (data / name).exists():
            (data / name).symlink_to(src, target_is_directory=True)
    return data


def server_env(root: Path, port: int) -> dict[str, str]:
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("FERMENTAI_") or k == "ANTHROPIC_API_KEY":
            env.pop(k)
    findings = str(root / "findings_log.jsonl")
    env.update({
        "FERMENTAI_API_TOKEN": TOKEN,
        # empty -> both chambers' devices use the operator token; also stops a stray .env from
        # injecting per-device tokens (load_dotenv never overrides an existing variable)
        "FERMENTAI_DEVICE_TOKEN_A": "",
        "FERMENTAI_DEVICE_TOKEN_B": "",
        "FERMENTAI_DB_PATH": str(root / "fermentai.sqlite3"),
        "FERMENTAI_DATA_DIR": str(make_data_dir(root)),
        "FERMENTAI_FINDINGS_PATH": findings,
        # agents/state.py reads FERMENTAI_FINDINGS_LOG, the API reads FERMENTAI_FINDINGS_PATH
        # (see test_agent_state_uses_api_findings_path); set both so they agree.
        "FERMENTAI_FINDINGS_LOG": findings,
        "FERMENTAI_LLM_MODE": "offline",
        "FERMENTAI_PORT": str(port),
        "FERMENTAI_BASE_URL": f"http://127.0.0.1:{port}",
        "PYTHONUNBUFFERED": "1",
    })
    return env


@dataclass
class Stack:
    root: Path
    port: int
    env: dict[str, str]
    audit_log: Path
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)
    logs: dict[str, Path] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self, timeout: float = 30.0) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout)

    def spawn(self, name: str, args: list[str]) -> subprocess.Popen:
        log = self.root / f"{name}.log"
        fh = open(log, "ab")  # noqa: SIM115 - closed with the process
        p = subprocess.Popen([sys.executable, *args], cwd=REPO_ROOT, env=self.env, stdout=fh,
                             stderr=subprocess.STDOUT, start_new_session=True)
        self.procs[name], self.logs[name] = p, log
        return p

    def start_server(self, host: str = "127.0.0.1", timeout: float = 60.0) -> None:
        self.spawn("server", ["-m", "tests.integration.net_audit", "--log", str(self.audit_log),
                              "--host", host, "--port", str(self.port)])
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.procs["server"].poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.tail('server')}")
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{self.base_url}/api/v1/health", timeout=1.0).status_code == 200:
                    return
            time.sleep(0.2)
        raise RuntimeError(f"server did not become healthy:\n{self.tail('server')}")

    def start_devices(self, *extra: str) -> subprocess.Popen:
        return self.spawn("devices", ["-m", "simulated_devices", "--chambers", "A", "B",
                                      "--base-url", self.base_url, *extra])

    def tail(self, name: str, n: int = 4000) -> str:
        p = self.logs.get(name)
        return p.read_text(errors="replace")[-n:] if p and p.is_file() else ""

    def stop(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p is None or p.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)

    def stop_all(self) -> None:
        for name in ("devices", *list(self.procs)):
            self.stop(name)


def make_stack(root: Path) -> Stack:
    root.mkdir(parents=True, exist_ok=True)
    port = free_port()
    return Stack(root=root, port=port, env=server_env(root, port), audit_log=root / "net_audit.jsonl")


def wait_for(pred, timeout: float, interval: float = 0.25, what: str = "condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}; last={last!r}")


@pytest.fixture(scope="session")
def live_stack(tmp_path_factory):
    """uvicorn (under the outbound-network audit hook) + the two virtual chambers, shared by the
    live end-to-end tests. Devices: 1 s telemetry, 1 s desired-control polling, real kinetics."""
    stack = make_stack(tmp_path_factory.mktemp("live"))
    try:
        stack.start_server()
        stack.start_devices("--interval", "1", "--poll-interval", "1", "--no-images")
        yield stack
    finally:
        stack.stop_all()
