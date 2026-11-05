
"""Persistent emergency halt for Albridge."""

import os
from pathlib import Path


HALT_FILE = Path("data/EMERGENCY_HALT")


class EmergencyHalt(RuntimeError):
    pass


def activate_emergency_halt() -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Atomic creation; never overwrite an existing halt.
    try:
        fd = os.open(
            HALT_FILE,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return

    with os.fdopen(fd, "w") as file:
        file.write("EMERGENCY HALT ACTIVE\n")
        file.flush()
        os.fsync(file.fileno())


def assert_trading_enabled() -> None:
    if HALT_FILE.exists():
        raise EmergencyHalt(
            "EMERGENCY HALT ACTIVE — trading disabled"
        )
