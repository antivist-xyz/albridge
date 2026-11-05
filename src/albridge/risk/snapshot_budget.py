
"""Derive quote risk budgets from external market snapshots."""

from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot
from albridge.risk.quote_risk_budget import (
    QuoteRiskBudget,
    calculate_quote_risk_budget,
)


ZERO = Decimal("0")
OUTCOMES = ("WIN", "DRAW", "LOSS")


def calculate_snapshot_risk_budget(
    *,
    snapshot: MarketSnapshot,
    win_anchor: Decimal,
    draw_anchor: Decimal,
    loss_anchor: Decimal,
) -> QuoteRiskBudget:
    """
    Calculate a 50/50 quote risk budget from observed ask depth.

    Shared instrument liquidity is split between Antivist
    BUY and SELL. Non-shared instruments retain full capacity.

    No external orders or liquidity reservations are made.
    """

    if not loss_anchor < draw_anchor < win_anchor:
        raise ValueError("Expected LOSS < DRAW < WIN")

    available = {}
    instruments = {}

    for outcome in OUTCOMES:
        book = snapshot.get(outcome)

        instrument_id = book.symbol

        if not instrument_id:
            raise ValueError("Missing hedge instrument ID")

        if instrument_id in available:
            raise ValueError("Duplicate hedge instrument ID")

        total_quantity = ZERO

        for price, quantity in book.asks:
            if (
                not price.is_finite()
                or not quantity.is_finite()
                or price < ZERO
                or price > Decimal("1")
                or quantity <= ZERO
            ):
                raise ValueError("Invalid external ask level")

            total_quantity += quantity

        instruments[outcome] = instrument_id
        available[instrument_id] = total_quantity

    sell_ratios = {
        instruments["WIN"]: win_anchor - loss_anchor,
        instruments["DRAW"]: draw_anchor - loss_anchor,
    }

    buy_ratios = {
        instruments["DRAW"]: win_anchor - draw_anchor,
        instruments["LOSS"]: win_anchor - loss_anchor,
    }

    return calculate_quote_risk_budget(
        available=available,
        sell_ratios=sell_ratios,
        buy_ratios=buy_ratios,
    )
