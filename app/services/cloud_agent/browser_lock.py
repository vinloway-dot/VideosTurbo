from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Literal

if os.name == "nt":
    import msvcrt
else:
    import fcntl


BrowserService = Literal["google_flow", "canva"]
_SUPPORTED_SERVICES = frozenset({"google_flow", "canva"})


class ProfileLock:
    """Cross-process advisory lock for persistent browser profiles."""

    def __init__(
        self,
        lock_dir: str | os.PathLike[str],
        *,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.lock_dir = Path(lock_dir)
        self.poll_interval_seconds = float(poll_interval_seconds)

    @contextmanager
    def acquire(self, service: str, *, timeout_seconds: float) -> Iterator[None]:
        if service not in _SUPPORTED_SERVICES:
            raise ValueError(f"unsupported browser service: {service}")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")

        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.lock_dir / f"{service}.lock"
        handle = lock_path.open("a+b")
        acquired = False
        try:
            _prepare_lock_file(handle)
            deadline = time.monotonic() + float(timeout_seconds)
            while True:
                if _try_acquire(handle):
                    acquired = True
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out acquiring browser profile lock for {service}"
                    )
                time.sleep(min(self.poll_interval_seconds, remaining))

            yield
        finally:
            if acquired:
                _release(handle)
            handle.close()


def _prepare_lock_file(handle: BinaryIO) -> None:
    if os.name != "nt":
        return

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()


def _try_acquire(handle: BinaryIO) -> bool:
    if os.name == "nt":
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release(handle: BinaryIO) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
