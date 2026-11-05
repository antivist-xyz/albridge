
"""Centralized logging configuration for Albridge."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-7s | "
    "%(name)s | %(message)s"
)


def configure_logging() -> None:
    log_dir = Path(
        os.getenv("ALBRIDGE_LOG_DIR", "logs")
    )

    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "albridge.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Prevent duplicate handlers if initialization repeats.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    # Complete technical log.
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Operational console.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # HTTP request logs remain available in the file.
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.INFO)
