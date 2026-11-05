"""Tests for independent multi-venue resolution."""

from decimal import Decimal
from types import SimpleNamespace

from albridge.market_resolver import MarketResolver


MARKET = {
    "team": {
        "id": 115,
        "name": "Barcelona",
        "slug": "barcelona",
        "aliases": ["Barcelona", "FC Barcelona"],
    },
    "next_fixture": {
        "fixture_label": "Barcelona vs Alaves",
        "kickoff_at": "2026-10-31T20:00:00+00:00",
        "oracle_anchor": "1000.00000",
        "pf_win": "15.00000",
        "pf_draw": "-15.00000",
        "pf_loss": "-30.00000",
    },
}


class FakeResolver:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.received_fixture = None

    def resolve(self, fixture):
        self.received_fixture = fixture

        if self.error:
            raise self.error

        return self.result


def test_both_venues_available():
    pascal = FakeResolver(SimpleNamespace(game_id="game-1"))
    polymarket = FakeResolver(SimpleNamespace(event_slug="event-1"))

    result = MarketResolver(pascal, polymarket).resolve(MARKET)

    assert result.available_venues == ("pascal", "polymarket")
    assert result.pricing.oracle_anchor == Decimal("1000")
    assert result.pricing.pf_win == Decimal("15")
    assert result.pricing.pf_draw == Decimal("-15")
    assert result.pricing.pf_loss == Decimal("-30")
    assert result.errors == {}
    assert pascal.received_fixture is polymarket.received_fixture


def test_pascal_failure_does_not_block_polymarket():
    pascal = FakeResolver(error=ValueError("Invalid Pascal data"))
    polymarket = FakeResolver(SimpleNamespace(event_slug="event-1"))

    result = MarketResolver(pascal, polymarket).resolve(MARKET)

    assert result.available_venues == ("polymarket",)
    assert "pascal" in result.errors


def test_polymarket_failure_does_not_block_pascal():
    pascal = FakeResolver(SimpleNamespace(game_id="game-1"))
    polymarket = FakeResolver(error=ValueError("Invalid Gamma data"))

    result = MarketResolver(pascal, polymarket).resolve(MARKET)

    assert result.available_venues == ("pascal",)
    assert "polymarket" in result.errors


def test_no_available_venues():
    result = MarketResolver(
        FakeResolver(),
        FakeResolver(),
    ).resolve(MARKET)

    assert result.available_venues == ()
    assert len(result.errors) == 2


def test_no_dependency_on_oracle_sources():
    market = {
        **MARKET,
        "oracle_sources": {
            "polymarket": {
                "markets": [],
            }
        },
    }

    result = MarketResolver(
        FakeResolver(SimpleNamespace(game_id="game-1")),
        FakeResolver(SimpleNamespace(event_slug="event-1")),
    ).resolve(market)

    assert result.available_venues == ("pascal", "polymarket")
