"""PreFormServer lifecycle and connection for the official Formlabs Python SDK.

The PreForm API (https://developer.formlabs.com/api-reference/) is served over HTTP by PreForm or
by PreFormServer, its windowless edition, on ``http://localhost:<port>`` (default 44388). This
module:

* finds a PreFormServer executable (env var, the repo's cache dir, /Applications);
* starts it with ``--port`` and its stdout/stderr going to a log file, and waits for the
  ``READY FOR INPUT`` line the server prints once it accepts requests;
* health-checks ``GET /v1/`` (returns ``{"version": "..."}``);
* opens an SDK handle (``FormlabsClient().preform.connect(base_url=...)``), optionally through a
  :class:`~fermentai.formlabs.replay.RecordingProxy` so every live request is logged, or against
  a :class:`~fermentai.formlabs.replay.MockPreFormServer` replay;
* raises :class:`PreFormUnavailable` with exact download instructions when there is no server.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PORT = 44388
READY_SENTINEL = "READY FOR INPUT"
PORT_IN_USE_SENTINEL = "Address already in use"

#: PreFormServer release the integration was developed and recorded against.
PREFORM_SERVER_VERSION = "3.64.2"
#: Official download page (lists every release; no login required at the time of writing).
DOWNLOAD_PAGE = "https://formlabs.com/support/Formlabs-API-downloads-and-release-notes/"
DOWNLOAD_URLS = {
    "mac-arm64": "https://downloads.formlabs.com/PreFormServer/Release/3.64.2/"
                 "PreForm_Server_mac-arm64_3.64.2_release_releaser_665_161434.zip",
    "mac-x86_64": "https://downloads.formlabs.com/PreFormServer/Release/3.64.2/"
                  "PreForm_Server_mac_3.64.2_release_releaser_665_161433.zip",
}
DOCKER_IMAGE = "formlabs/preform-server:release-latest"
SDK_INSTALL = 'formlabs-sdk @ git+https://github.com/Formlabs/formlabs-api-python@v1.0.0-alpha.post2'

ENV_EXE = "FERMENTAI_PREFORM_SERVER"  # path to the PreFormServer executable (or .app)
CACHE_DIR = Path.home() / ".cache" / "fermentai" / "preform-server"
_MAC_EXE_IN_APP = Path("Contents") / "MacOS" / "PreFormServer"


class PreFormUnavailable(RuntimeError):
    """No PreFormServer is reachable and none could be started."""

    def __init__(self, reason: str):
        super().__init__(f"{reason}\n\n{install_instructions()}")
        self.reason = reason


def install_instructions() -> str:
    """Exactly what a human must download/do to make the integration live."""
    arch = "mac-arm64" if platform.machine() == "arm64" else "mac-x86_64"
    url = DOWNLOAD_URLS[arch]
    dest = CACHE_DIR / PREFORM_SERVER_VERSION
    return (
        "To run the PreForm API live you need PreFormServer (headless PreForm, free download):\n"
        f"  1. Download (macOS, {arch}): {url}\n"
        f"     (all releases: {DOWNLOAD_PAGE})\n"
        f"  2. Unzip it:   mkdir -p {dest} && ditto -x -k <zip> {dest}\n"
        f"     -> {dest}/PreFormServer/PreFormServer.app\n"
        f"     or point {ENV_EXE} at .../PreFormServer.app/Contents/MacOS/PreFormServer\n"
        "  3. Re-run:     uv run python scripts/prepare_print.py prepare --backend live\n"
        "  Alternatives: PreForm desktop (Insider build) > File > Start PreForm Server (port "
        f"{DEFAULT_PORT}); on Linux: docker run --rm -p 127.0.0.1:{DEFAULT_PORT}:{DEFAULT_PORT} "
        f"{DOCKER_IMAGE} --port {DEFAULT_PORT}\n"
        f"  Python SDK (already a project dependency): {SDK_INSTALL}"
    )


def _as_executable(path: Path) -> Path:
    if path.suffix == ".app":
        return path / _MAC_EXE_IN_APP
    return path


def locate_preform_server(explicit: str | os.PathLike | None = None) -> Path | None:
    """Return the PreFormServer executable path, or ``None`` if none is installed.

    Search order: ``explicit`` -> ``$FERMENTAI_PREFORM_SERVER`` -> ``$PREFORM`` (the variable the
    Formlabs docs use) -> ``~/.cache/fermentai/preform-server/<version>/PreFormServer/`` (newest
    first) -> ``/Applications/PreFormServer.app`` -> ``~/Applications/PreFormServer.app``.
    """
    candidates: list[Path] = []
    for value in (explicit, os.environ.get(ENV_EXE), os.environ.get("PREFORM")):
        if value:
            candidates.append(Path(value).expanduser())
    if CACHE_DIR.is_dir():
        for vdir in sorted(CACHE_DIR.iterdir(), reverse=True):
            candidates.append(vdir / "PreFormServer" / "PreFormServer.app")
    candidates += [Path("/Applications/PreFormServer.app"),
                   Path.home() / "Applications" / "PreFormServer.app"]
    for c in candidates:
        exe = _as_executable(c)
        if exe.is_file() and os.access(exe, os.X_OK):
            return exe
    return None


def server_version(base_url: str, timeout: float = 2.0) -> str | None:
    """``GET /v1/`` health check. Returns the server version string, or ``None`` if unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/v1/", timeout=timeout) as r:
            return json.loads(r.read()).get("version")
    except (urllib.error.URLError, OSError, ValueError):
        return None


@dataclass
class PreFormServerProcess:
    """A PreFormServer child process with its output captured to ``log_path``."""

    exe: Path
    port: int = DEFAULT_PORT
    log_path: Path | None = None
    data_dir: Path | None = None
    telemetry: bool = False
    startup_timeout: float = 120.0
    proc: subprocess.Popen | None = None
    started_at: float | None = None
    ready_after_s: float | None = None

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> PreFormServerProcess:
        env = dict(os.environ)
        if not self.telemetry:
            env["DISABLE_PREFORMSERVER_TELEMETRY"] = "1"
        if self.data_dir is not None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            env["PREFORM_ENV_DATA_DIRECTORY"] = str(self.data_dir)
        if self.log_path is None:
            self.log_path = Path(os.devnull)
        else:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "w")  # noqa: SIM115 - closed in stop()
        self.started_at = time.time()
        self.proc = subprocess.Popen([str(self.exe), "--port", str(self.port)], env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)
        self._wait_ready()
        return self

    def _read_log(self) -> str:
        try:
            return Path(self.log_path).read_text(errors="replace")
        except OSError:
            return ""

    def _wait_ready(self) -> None:
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            text = self._read_log()
            if READY_SENTINEL in text and server_version(self.base_url):
                self.ready_after_s = round(time.time() - (self.started_at or time.time()), 2)
                return
            if PORT_IN_USE_SENTINEL in text:
                self.stop()
                raise PreFormUnavailable(f"port {self.port} is already in use")
            if self.proc is not None and self.proc.poll() is not None:
                tail = "\n".join(text.splitlines()[-15:])
                self.stop()
                raise PreFormUnavailable(
                    f"PreFormServer exited with code {self.proc.returncode} before "
                    f"{READY_SENTINEL!r}. Log tail:\n{tail}")
            time.sleep(0.25)
        self.stop()
        raise PreFormUnavailable(f"PreFormServer did not print {READY_SENTINEL!r} within "
                                 f"{self.startup_timeout:.0f} s")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if getattr(self, "_log", None) is not None:
            self._log.close()
            self._log = None


@dataclass
class PreFormHandle:
    """What :func:`open_preform` yields: an SDK handle plus provenance of the server behind it."""

    api: Any  # formlabs_sdk.PreFormApi
    backend: str  # "live" | "mock"
    base_url: str
    server_version: str | None
    server_exe: Path | None = None
    server_started_by_us: bool = False
    server_ready_after_s: float | None = None
    server_log: Path | None = None
    recorder: Any = None  # RecordingProxy (live)
    mock: Any = None  # MockPreFormServer (mock)
    notes: list[str] = field(default_factory=list)

    @property
    def transcript(self) -> list[dict]:
        if self.recorder is not None:
            return self.recorder.transcript()
        return []


def sdk_versions() -> dict[str, str | None]:
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for dist in ("formlabs-sdk", "formlabs-preform-api"):
        try:
            out[dist] = version(dist)
        except PackageNotFoundError:
            out[dist] = None
    try:
        import formlabs_preform_api
        import yaml

        spec = Path(formlabs_preform_api.__file__).with_name("openapi-v1.yml")
        info = yaml.safe_load(spec.read_text()).get("info", {}) if spec.is_file() else {}
        out["preform_api_spec_version"] = info.get("version")
    except Exception:  # noqa: BLE001  # pragma: no cover - spec is optional metadata
        out["preform_api_spec_version"] = None
    return out


def _require_sdk() -> Any:
    try:
        from formlabs_sdk import FormlabsClient
    except ImportError as exc:  # pragma: no cover - SDK is a project dependency
        raise PreFormUnavailable(f"the Formlabs Python SDK is not installed ({exc})") from exc
    return FormlabsClient


@contextlib.contextmanager
def open_preform(
    backend: str = "auto",
    *,
    port: int = DEFAULT_PORT,
    server_exe: str | os.PathLike | None = None,
    server_log: Path | None = None,
    data_dir: Path | None = None,
    fixture: Path | None = None,
    record: bool = True,
    keep_server: bool = False,
    poll_interval: float = 0.25,
    allow_print: bool = False,
) -> Iterator[PreFormHandle]:
    """Yield a :class:`PreFormHandle` bound to a live PreFormServer or to the mock replay.

    ``backend``:
      * ``"live"`` — reuse a server already answering on ``port`` (e.g. the PreForm GUI's), else
        start the located executable; raise :class:`PreFormUnavailable` if neither works.
      * ``"mock"`` — replay ``fixture`` (a recorded live session) with :class:`MockPreFormServer`.
      * ``"auto"`` — live if possible, otherwise mock (the handle's ``notes`` say why).

    ``allow_print`` is False everywhere except :func:`~fermentai.formlabs.prepare.submit_print`
    after both human confirmations; while False the recording proxy refuses ``/v1/scene/print/``.
    """
    FormlabsClient = _require_sdk()
    from .replay import MockPreFormServer, RecordingProxy

    if backend not in ("auto", "live", "mock"):
        raise ValueError(f"unknown backend {backend!r}")

    with contextlib.ExitStack() as stack:
        notes: list[str] = []
        handle_kw: dict[str, Any] = {}
        target: str | None = None
        if backend in ("auto", "live"):
            base = f"http://localhost:{port}"
            version = server_version(base)
            if version:
                notes.append(f"attached to a PreFormServer already running at {base}")
                target = base
                handle_kw.update(server_exe=locate_preform_server(server_exe))
            else:
                exe = locate_preform_server(server_exe)
                if exe is None:
                    if backend == "live":
                        raise PreFormUnavailable(
                            f"no PreFormServer answers on {base} and no executable was found")
                    notes.append("PreFormServer not available on this machine -> mock replay")
                else:
                    proc = PreFormServerProcess(exe=exe, port=port, log_path=server_log,
                                                data_dir=data_dir)
                    proc.start()
                    if not keep_server:
                        stack.callback(proc.stop)
                    target = proc.base_url
                    handle_kw.update(server_exe=exe, server_started_by_us=True,
                                     server_ready_after_s=proc.ready_after_s,
                                     server_log=server_log)
                    notes.append(f"started {exe} --port {port}")
        if target is not None:
            recorder = None
            url = target
            if record or not allow_print:  # the proxy is also the print guard
                recorder = stack.enter_context(RecordingProxy(upstream=target,
                                                                allow_print=allow_print))
                url = recorder.base_url
            client = stack.enter_context(FormlabsClient())
            api = client.preform.connect(base_url=url, poll_interval=poll_interval)
            yield PreFormHandle(api=api, backend="live", base_url=target,
                                server_version=api.version, recorder=recorder, notes=notes,
                                **handle_kw)
            return
        if fixture is None or not Path(fixture).is_file():
            raise PreFormUnavailable(f"mock requested but fixture {fixture} is missing")
        mock = stack.enter_context(MockPreFormServer.from_file(fixture))
        client = stack.enter_context(FormlabsClient())
        api = client.preform.connect(base_url=mock.base_url, poll_interval=0.01)
        yield PreFormHandle(api=api, backend="mock", base_url=mock.base_url,
                            server_version=api.version, mock=mock, notes=notes)
