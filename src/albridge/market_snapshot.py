"""Venue-independent three-outcome market snapshot."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from albridge.venues.order_book import PascalOrderBook


@dataclass(frozen=True)
class OutcomeBook:
    outcome: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    sequence: int | None
    state_time_ms: int

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0][0] if self.asks else None


@dataclass(frozen=True)
class MarketSnapshot:
    venue: str
    outcomes: Mapping[str, OutcomeBook]

    def get(self, outcome: str) -> OutcomeBook:
        return self.outcomes[outcome]



def build_pascal_snapshot(
    *,
    books: dict[str, PascalOrderBook],
    symbols: dict[str, str],
) -> MarketSnapshot | None:
    """
    Build a WIN/DRAW/LOSS market-data snapshot.

    A one-sided order book is valid market data.
    Trading eligibility is evaluated separately.
    """

    if set(symbols) != {"WIN", "DRAW", "LOSS"}:
        raise ValueError("Expected WIN, DRAW and LOSS mappings")

    if len(set(symbols.values())) != 3:
        raise ValueError("Outcome symbols must be distinct")

    outcomes = {}

    for outcome, symbol in symbols.items():
        book = books.get(symbol)

        if book is None:
            return None

        # Require an initialized book, not a two-sided book.
        if not book.initialized:
            return None

        if book.state_time_ms is None or book.sequence is None:
            return None

        outcomes[outcome] = OutcomeBook(
            outcome=outcome,
            symbol=symbol,
            bids=tuple(book.levels("BUY")),
            asks=tuple(book.levels("SELL")),
            sequence=book.sequence,
            state_time_ms=book.state_time_ms,
        )

    return MarketSnapshot(
        venue="pascal",
        outcomes=outcomes,
    )