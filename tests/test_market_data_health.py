"""Tests for market-data feed health monitoring."""

from types import SimpleNamespace

import pytest

from albridge.market_data_health import assess_feed_health


def make_client(
    *,
    connected=True,
    ready=True,
    last_message=100.0,
):
    return SimpleNamespace(
        connected=connected,
        market_data_ready=ready,
        last_message_monotonic=last_message,
    )


def test_ready_feed():
    health = assess_feed_health(
        venue="pascal",
        client=make_client(),
        now_monotonic=101.0,
    )

    assert health.status == "READY"
    assert health.ready
    assert health.last_message_age_seconds == 1.0


def test_disconnected_feed():
    health = assess_feed_health(
        venue="polymarket",
        client=make_client(connected=False, ready=False),
        now_monotonic=101.0,
    )

    assert health.status == "DISCONNECTED"
    assert not health.ready


def test_initializing_feed():
    health = assess_feed_health(
        venue="pascal",
        client=make_client(ready=False),
        now_monotonic=101.0,
    )

    assert health.status == "INITIALIZING"
    assert not health.ready


def test_silent_feed():
    health = assess_feed_health(
        venue="pascal",
        client=make_client(),
        now_monotonic=145.0,
    )

    assert health.status == "SILENT"

    # Silence is diagnostic, not automatic book invalidation.
    assert health.ready


def test_recent_message_recovers_from_silence():
    client = make_client(last_message=100.0)

    silent = assess_feed_health(
        venue="pascal",
        client=client,
        now_monotonic=145.0,
    )

    client.last_message_monotonic = 144.0

    recovered = assess_feed_health(
        venue="pascal",
        client=client,
        now_monotonic=145.0,
    )

    assert silent.status == "SILENT"
    assert recovered.status == "READY"


def test_missing_message_timestamp():
    health = assess_feed_health(
        venue="polymarket",
        client=make_client(last_message=None),
        now_monotonic=145.0,
    )

    assert health.status == "SILENT"
    assert health.last_message_age_seconds is None


def test_invalid_warning_threshold():
    with pytest.raises(ValueError):
        assess_feed_health(
            venue="pascal",
            client=make_client(),
            silence_warning_seconds=0,
        )
