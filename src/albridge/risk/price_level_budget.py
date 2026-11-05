
"""Allocate external hedge liquidity by instrument and price level."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot


ZERO = Decimal("0")
ONE = Decimal("1")
HALF = Decimal("0.5")

PriceLevelKey = tuple[str, Decimal]


@dataclass(frozen=True)
class PriceLevelBudget:
    sell: dict[PriceLevelKey, Decimal]
    buy: dict[PriceLevelKey, Decimal]


def calculate_price_level_budget(
    *,
    snapshot: MarketSnapshot,
) -> PriceLevelBudget:
    """
    Split shared DRAW YES ask liquidity 50/50 at every price.

    WIN YES is allocated exclusively to Antivist SELL.
    LOSS YES is allocated exclusively to Antivist BUY.

    All allocations are theoretical, not venue reservations.
    """

    sell_budget = {}
    buy_budget = {}
    seen_instruments = set()

    for outcome in ("WIN", "DRAW", "LOSS"):
        book = snapshot.get(outcome)
        instrument_id = book.symbol

        if not instrument_id:
            raise ValueError("Missing hedge instrument ID")

        if instrument_id in seen_instruments:
            raise ValueError("Duplicate hedge instrument ID")

        seen_instruments.add(instrument_id)

        for price, quantity in book.asks:
            if (
                not price.is_finite()
                or not quantity.is_finite()
                or price < ZERO
                or price > ONE
                or quantity <= ZERO
            ):
                raise ValueError("Invalid external ask level")

            key = (instrument_id, price)

            if outcome == "DRAW":
                sell_quantity = quantity * HALF
                buy_quantity = quantity - sell_quantity

                sell_budget[key] = (
                    sell_budget.get(key, ZERO) + sell_quantity
                )
                buy_budget[key] = (
                    buy_budget.get(key, ZERO) + buy_quantity
                )

            elif outcome == "WIN":
                sell_budget[key] = (
                    sell_budget.get(key, ZERO) + quantity
                )

            elif outcome == "LOSS":
                buy_budget[key] = (
                    buy_budget.get(key, ZERO) + quantity
                )

    return PriceLevelBudget(
        sell=sell_budget,
        buy=buy_budget,
    )
