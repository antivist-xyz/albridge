
"""Split shared hedge liquidity before building quote ladders."""

from dataclasses import replace
from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot


HALF = Decimal("0.5")


def split_snapshot_for_quote_sides(
    snapshot: MarketSnapshot,
) -> tuple[MarketSnapshot, MarketSnapshot]:
    """
    Split DRAW YES ask liquidity 50/50 between SELL and BUY.

    Preserve WIN and LOSS books, instrument identities,
    venue metadata and the original snapshot.

    The resulting snapshots represent theoretical
    liquidity allocations, not external reservations.
    """

    draw_book = snapshot.get("DRAW")

    sell_asks = []
    buy_asks = []

    for price, quantity in draw_book.asks:
        if (
            not price.is_finite()
            or not quantity.is_finite()
            or quantity <= 0
            or price < 0
            or price > 1
        ):
            raise ValueError("Invalid DRAW ask level")

        sell_quantity = quantity * HALF
        buy_quantity = quantity - sell_quantity

        sell_asks.append((price, sell_quantity))
        buy_asks.append((price, buy_quantity))

    sell_draw = replace(
        draw_book,
        asks=tuple(sell_asks),
    )

    buy_draw = replace(
        draw_book,
        asks=tuple(buy_asks),
    )

    sell_outcomes = dict(snapshot.outcomes)
    buy_outcomes = dict(snapshot.outcomes)

    sell_outcomes["DRAW"] = sell_draw
    buy_outcomes["DRAW"] = buy_draw

    return (
        MarketSnapshot(
            venue=snapshot.venue,
            outcomes=sell_outcomes,
        ),
        MarketSnapshot(
            venue=snapshot.venue,
            outcomes=buy_outcomes,
        ),
    )
