"""Byte-level serial link + async line channel (POSIX: macOS / Linux).

`SerialLink` wraps a file descriptor in raw mode: a real USB CDC port opened by path
(`/dev/cu.usbmodem*`, termios raw, 8N1, given baud, exclusive access) or any fd such as one end of
a pty pair (tests and the virtual devices). No pyserial dependency is needed: pyserial itself is
termios underneath on POSIX.

Unplug / far-end close shows up as EOF (read returns b"" while the fd is readable) or EIO/ENXIO;
both raise `LinkClosed`. `AsyncLineChannel` reads continuously in its own task (so slow HTTP
calls never stall the serial buffer — a pty buffers only ~1 KB) and writes whole lines under a
lock, retrying partial writes / EAGAIN.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import glob
import logging
import os
import termios
import tty

from .protocol import MAX_LINE_BYTES, LineFramer

log = logging.getLogger("fermentai.bridge.link")

DEFAULT_PORT_PATTERNS = ("/dev/cu.usbmodem*", "/dev/ttyACM*")


class LinkClosed(Exception):
    """The far end went away (unplug, reset, pty closed)."""


def configure_raw(fd: int, baud: int | None = None) -> None:
    # TCSANOW: TCSAFLUSH (tty.setraw's default) blocks until pending output drains, which can
    # deadlock an event loop whose other task is the only reader of the far end.
    tty.setraw(fd, termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD           # ignore modem lines, enable receiver
    attrs[2] &= ~termios.HUPCL                           # don't drop DTR on close (avoids resets)
    if baud:
        speed = getattr(termios, f"B{baud}", None)
        if speed is None:
            raise ValueError(f"unsupported baud rate {baud}")
        attrs[4] = attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


class SerialLink:
    def __init__(self, fd: int, *, path: str | None = None, owns_fd: bool = True):
        self.fd = fd
        self.path = path
        self.owns_fd = owns_fd
        self.closed = False
        os.set_blocking(fd, False)

    @classmethod
    def open(cls, path: str, baud: int = 115200, *, exclusive: bool = True) -> SerialLink:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if exclusive and hasattr(termios, "TIOCEXCL"):
                with contextlib.suppress(OSError):   # some pseudo-devices refuse; not fatal
                    fcntl.ioctl(fd, termios.TIOCEXCL)
            configure_raw(fd, baud)
            with contextlib.suppress(termios.error):
                termios.tcflush(fd, termios.TCIFLUSH)   # drop stale bytes from before we opened
        except Exception:
            os.close(fd)
            raise
        return cls(fd, path=path)

    @classmethod
    def from_fd(cls, fd: int, *, raw: bool = True, owns_fd: bool = False, path: str | None = None) -> SerialLink:
        if raw:
            with contextlib.suppress(termios.error):
                configure_raw(fd)
        return cls(fd, path=path, owns_fd=owns_fd)

    def read_available(self, n: int = 65536) -> bytes:
        """Non-blocking read; b"" means 'nothing right now'. Raises LinkClosed on EOF/EIO."""
        if self.closed:
            raise LinkClosed("link closed")
        try:
            data = os.read(self.fd, n)
        except BlockingIOError:
            return b""
        except OSError as e:
            if e.errno in (errno.EIO, errno.ENXIO, errno.EBADF, errno.ENODEV):
                raise LinkClosed(f"{self.path or self.fd}: {os.strerror(e.errno)}") from e
            raise
        if data == b"":
            raise LinkClosed(f"{self.path or self.fd}: EOF")
        return data

    def write_some(self, data: bytes) -> int:
        try:
            return os.write(self.fd, data)
        except BlockingIOError:
            return 0
        except OSError as e:
            if e.errno in (errno.EIO, errno.ENXIO, errno.EBADF, errno.ENODEV, errno.EPIPE):
                raise LinkClosed(f"{self.path or self.fd}: {os.strerror(e.errno)}") from e
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            if self.owns_fd:
                with contextlib.suppress(OSError):
                    os.close(self.fd)


class AsyncLineChannel:
    """Line-oriented async wrapper around a SerialLink."""

    def __init__(self, link: SerialLink, *, max_line: int = MAX_LINE_BYTES, queue_size: int = 2000,
                 poll_s: float = 0.01, write_timeout_s: float = 5.0):
        self.link = link
        self.framer = LineFramer(max_line)
        self.lines: asyncio.Queue[bytes | None] = asyncio.Queue(queue_size)
        self.poll_s = poll_s
        self.write_timeout_s = write_timeout_s
        self._wlock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.closed_reason: str | None = None
        self.dropped_lines = 0
        self.rx_bytes = 0
        self.tx_bytes = 0

    def start(self) -> AsyncLineChannel:
        self._task = asyncio.create_task(self._reader())
        return self

    async def _reader(self) -> None:
        try:
            while True:
                data = self.link.read_available()
                if not data:
                    await asyncio.sleep(self.poll_s)
                    continue
                self.rx_bytes += len(data)
                for line in self.framer.feed(data):
                    if self.lines.full():          # backpressure: drop oldest, never block reads
                        with contextlib.suppress(asyncio.QueueEmpty):
                            self.lines.get_nowait()
                        self.dropped_lines += 1
                    self.lines.put_nowait(line)
        except LinkClosed as e:
            self.closed_reason = str(e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            self.closed_reason = f"{type(e).__name__}: {e}"
            log.exception("serial reader crashed")
        with contextlib.suppress(asyncio.QueueFull):
            self.lines.put_nowait(None)            # sentinel: link closed

    async def readline(self, timeout: float | None = None) -> bytes | None:
        """Next line, None when the link closed. Raises TimeoutError on timeout."""
        if timeout is None:
            return await self.lines.get()
        return await asyncio.wait_for(self.lines.get(), timeout)

    async def write_line(self, data: bytes) -> None:
        async with self._wlock:
            view = memoryview(data)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.write_timeout_s
            while view:
                n = self.link.write_some(view)
                if n:
                    view = view[n:]
                    self.tx_bytes += n
                    continue
                if loop.time() > deadline:
                    raise TimeoutError("serial write timed out (device not reading?)")
                await asyncio.sleep(0.002)

    @property
    def is_closed(self) -> bool:
        return self.closed_reason is not None

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self.link.close()


def discover_ports(patterns: tuple[str, ...] | list[str] = DEFAULT_PORT_PATTERNS,
                   exclude: set[str] | None = None) -> list[str]:
    found: list[str] = []
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            if p not in found and p not in (exclude or set()):
                found.append(p)
    return found
