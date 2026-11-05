"""Tests for the multi-venue market-data aggregator."""

from types import SimpleNamespace

import pytest

from albridge.market_data import MarketDataAggregator


def client(snapshot=None):
    return SimpleNamespace(market_snapshot=snapshot)


def test_both_venues_available():
    pascal = object()
    polymarket = object()

    aggregator = MarketDataAggregator(
        pascal_client=client(pascal),
        polymarket_client=client(polymarket),
    )

    result = aggregator.snapshot()

    assert result.available_venues == ("pascal", "polymarket")
    assert result.pascal is pascal
    assert result.polymarket is polymarket


def test_pascal_only():
    aggregator = MarketDataAggregator(
        pascal_client=client(object()),
    )

    assert aggregator.snapshot().available_venues == ("pascal",)


def test_polymarket_only():
    aggregator = MarketDataAggregator(
        polymarket_client=client(object()),
    )

    assert aggregator.snapshot().available_venues == ("polymarket",)


def test_no_market_data():
    aggregator = MarketDataAggregator()

    result = aggregator.snapshot()

    assert result.available_venues == ()
    assert result.pascal is None
    assert result.polymarket is None


def test_snapshot_updates():
    pascal_client = client()

    aggregator = MarketDataAggregator(
        pascal_client=pascal_client,
    )

    assert aggregator.snapshot().pascal is None

    published = object()
    pascal_client.market_snapshot = published

    assert aggregator.snapshot().pascal is published


def test_unknown_venue_raises():
    result = MarketDataAggregator().snapshot()

    with pytest.raises(ValueError):
        result.get("unknown")
