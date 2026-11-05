"""Market-data feed health assessment."""

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedHealth:
    venue: str
    status: str
    connected: bool
    snapshot_ready: bool
    last_message_age_seconds: float | None

    @property
    def ready(self) -> bool:
        """Basic connection and snapshot readiness."""
        return self.connected and self.snapshot_ready


def assess_feed_health(
    *,
    venue: str,
    client,
    silence_warning_seconds: float = 30.0,
    now_monotonic: float | None = None,
) -> FeedHealth:
    """Assess feed status without assuming that silence means stale prices."""

    if silence_warning_seconds <= 0:
        raise ValueError("Silence warning threshold must be positive")

    now = (
        time.monotonic()
        if now_monotonic is None
        else now_monotonic
    )

    connected = bool(client.connected)
    snapshot_ready = bool(client.market_data_ready)
    books = getattr(client, "books", {})

    books_initialized = (
        bool(books)
        and all(book.initialized for book in books.values())
    )

    books_empty = (
        books_initialized
        and all(
            not book.bids and not book.asks
            for book in books.values()
        )
    )

    last_message = client.last_message_monotonic

    age = None

    if last_message is not None:
        age = max(0.0, now - last_message)

    if not connected:
        status = "DISCONNECTED"

    elif books_empty:
        status = "EMPTY"

    elif not snapshot_ready:
        status = "INITIALIZING"

    elif age is None or age >= silence_warning_seconds:
        status = "SILENT"

    else:
        status = "READY"

    return FeedHealth(
        venue=venue,
        status=status,
        connected=connected,
        snapshot_ready=snapshot_ready,
        last_message_age_seconds=age,
    )
