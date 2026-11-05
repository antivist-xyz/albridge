"""Aggregate independent venue market-data snapshots."""

from dataclasses import dataclass

from albridge.market_snapshot import MarketSnapshot


@dataclass(frozen=True)
class VenueMarketData:
    """Latest published snapshots from available venues."""

    pascal: MarketSnapshot | None = None
    polymarket: MarketSnapshot | None = None

    @property
    def available_venues(self) -> tuple[str, ...]:
        venues = []

        if self.pascal is not None:
            venues.append("pascal")

        if self.polymarket is not None:
            venues.append("polymarket")

        return tuple(venues)

    def get(self, venue: str) -> MarketSnapshot | None:
        if venue == "pascal":
            return self.pascal

        if venue == "polymarket":
            return self.polymarket

        raise ValueError(f"Unknown venue: {venue}")


class MarketDataAggregator:
    """Read snapshots without merging independent venue liquidity."""

    def __init__(
        self,
        *,
        pascal_client=None,
        polymarket_client=None,
    ):
        self.pascal_client = pascal_client
        self.polymarket_client = polymarket_client

    def snapshot(self) -> VenueMarketData:
        """Read the latest snapshot published by each client."""

        pascal = (
            self.pascal_client.market_snapshot
            if self.pascal_client is not None
            else None
        )

        polymarket = (
            self.polymarket_client.market_snapshot
            if self.polymarket_client is not None
            else None
        )

        return VenueMarketData(
            pascal=pascal,
            polymarket=polymarket,
        )
