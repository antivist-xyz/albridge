"""Resolve external trading instruments for an Antivist market."""

from dataclasses import dataclass
from decimal import Decimal

import httpx

from albridge.fixture import Fixture, fixture_from_antivist
from albridge.venues.pascal_resolver import (
    PascalMarketResolver,
    ResolvedPascalMarket,
)
from albridge.venues.polymarket_resolver import (
    PolymarketMarketResolver,
    ResolvedPolymarket,
)


@dataclass(frozen=True)
class AntivistPricing:
    oracle_anchor: Decimal | None
    pf_win: Decimal | None
    pf_draw: Decimal | None
    pf_loss: Decimal | None


@dataclass(frozen=True)
class MarketResolution:
    fixture: Fixture
    pricing: AntivistPricing
    pascal: ResolvedPascalMarket | None
    polymarket: ResolvedPolymarket | None
    errors: dict[str, str]

    @property
    def available_venues(self) -> tuple[str, ...]:
        venues = []

        if self.pascal is not None:
            venues.append("pascal")

        if self.polymarket is not None:
            venues.append("polymarket")

        return tuple(venues)


def optional_decimal(value) -> Decimal | None:
    """Parse an optional pricing parameter."""
    if value is None:
        return None

    result = Decimal(str(value))

    if not result.is_finite():
        raise ValueError("Non-finite Antivist pricing parameter")

    return result


class MarketResolver:
    """Discover venues independently using the same Antivist fixture."""

    def __init__(
        self,
        pascal_resolver=None,
        polymarket_resolver=None,
    ):
        self.pascal_resolver = (
            pascal_resolver
            if pascal_resolver is not None
            else PascalMarketResolver()
        )
        self.polymarket_resolver = (
            polymarket_resolver
            if polymarket_resolver is not None
            else PolymarketMarketResolver()
        )

    def resolve(self, antivist_market: dict) -> MarketResolution:
        fixture = fixture_from_antivist(antivist_market)

        source = antivist_market["next_fixture"]

        pricing = AntivistPricing(
            oracle_anchor=optional_decimal(source.get("oracle_anchor")),
            pf_win=optional_decimal(source.get("pf_win")),
            pf_draw=optional_decimal(source.get("pf_draw")),
            pf_loss=optional_decimal(source.get("pf_loss")),
        )

        errors = {}
        pascal = None
        polymarket = None

        # Venue discovery failures must remain independent.
        try:
            pascal = self.pascal_resolver.resolve(fixture)
            if pascal is None:
                errors["pascal"] = "No matching complete market"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            errors["pascal"] = str(exc)

        try:
            polymarket = self.polymarket_resolver.resolve(fixture)
            if polymarket is None:
                errors["polymarket"] = "No matching complete market"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            errors["polymarket"] = str(exc)

        return MarketResolution(
            fixture=fixture,
            pricing=pricing,
            pascal=pascal,
            polymarket=polymarket,
            errors=errors,
        )
