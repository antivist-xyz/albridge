
"""Tests for deriving quote risk budgets from market snapshots."""

from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot, OutcomeBook
from albridge.risk.snapshot_budget import (
    calculate_snapshot_risk_budget,
)


def make_book(outcome, instrument_id, asks):
    """Create an external outcome book with synthetic ask depth."""

    return OutcomeBook(
        outcome=outcome,
        symbol=instrument_id,
        bids=(),
        asks=tuple(
            (Decimal(price), Decimal(quantity))
            for price, quantity in asks
        ),
        sequence=1,
        state_time_ms=1000,
    )


def test_snapshot_budget_splits_shared_draw_liquidity():
    """
    Derive token liquidity and hedge ratios directly
    from the snapshot, then split shared DRAW 50/50.
    """

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": make_book(
                "WIN",
                "win-token-123",
                [
                    ("0.89", "400"),
                    ("0.90", "500"),
                ],
            ),
            "DRAW": make_book(
                "DRAW",
                "draw-token-456",
                [
                    ("0.08", "100"),
                    ("0.09", "200"),
                ],
            ),
            "LOSS": make_book(
                "LOSS",
                "loss-token-789",
                [
                    ("0.04", "300"),
                    ("0.05", "600"),
                ],
            ),
        },
    )

    budget = calculate_snapshot_risk_budget(
        snapshot=snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
    )

    # Shared DRAW liquidity must be split evenly.
    assert budget.sell_token_budget["draw-token-456"] == (
        Decimal("150")
    )

    assert budget.buy_token_budget["draw-token-456"] == (
        Decimal("150")
    )

    # Non-shared instruments retain their full capacity.
    assert budget.sell_token_budget["win-token-123"] == (
        Decimal("900")
    )

    assert budget.buy_token_budget["loss-token-789"] == (
        Decimal("900")
    )

    # Antivist maximum quantities after applying hedge ratios.
    assert budget.sell_max_quantity == Decimal("10")
    assert budget.buy_max_quantity == Decimal("5")

    # Shared instrument allocation cannot exceed observed depth.
    assert (
        budget.sell_token_budget["draw-token-456"]
        + budget.buy_token_budget["draw-token-456"]
        == Decimal("300")
    )
