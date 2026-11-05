
"""Prevent concurrent Albridge main processes."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlbridgeAlreadyRunning(RuntimeError):
    """Another Albridge process owns the instance lock."""


@contextmanager
def single_instance_lock(
    path: Path = Path("data/albridge.lock"),
):
    """
    Hold an exclusive OS file lock for the process lifetime.

    The operating system releases the lock if the process dies.
    The lock file itself is intentionally not deleted.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(
        path,
        os.O_CREAT | os.O_RDWR,
        0o600,
    )

    acquired = False

    try:
        try:
            fcntl.flock(
                fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            acquired = True

        except BlockingIOError as exc:
            raise AlbridgeAlreadyRunning(
                "Another Albridge instance is already running. "
                f"Lock: {path}"
            ) from exc

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)

        yield

    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)

        os.close(fd)
