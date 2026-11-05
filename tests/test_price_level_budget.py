
"""Tests for price-level hedge liquidity allocation."""

from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot, OutcomeBook
from albridge.risk.price_level_budget import (
    calculate_price_level_budget,
)


def make_book(outcome, instrument_id, asks):
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


def test_shared_draw_liquidity_split_at_each_price():
    """
    Shared DRAW liquidity must be split 50/50
    independently at each external ask price.
    """

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": make_book(
                "WIN",
                "WIN_YES",
                [("0.89", "900")],
            ),
            "DRAW": make_book(
                "DRAW",
                "DRAW_YES",
                [
                    ("0.08", "100"),
                    ("0.09", "200"),
                    ("0.10", "400"),
                ],
            ),
            "LOSS": make_book(
                "LOSS",
                "LOSS_YES",
                [("0.04", "900")],
            ),
        },
    )

    budget = calculate_price_level_budget(snapshot=snapshot)

    assert budget.sell == {
        ("WIN_YES", Decimal("0.89")): Decimal("900"),
        ("DRAW_YES", Decimal("0.08")): Decimal("50"),
        ("DRAW_YES", Decimal("0.09")): Decimal("100"),
        ("DRAW_YES", Decimal("0.10")): Decimal("200"),
    }

    assert budget.buy == {
        ("DRAW_YES", Decimal("0.08")): Decimal("50"),
        ("DRAW_YES", Decimal("0.09")): Decimal("100"),
        ("DRAW_YES", Decimal("0.10")): Decimal("200"),
        ("LOSS_YES", Decimal("0.04")): Decimal("900"),
    }

    for price, available in snapshot.get("DRAW").asks:
        key = ("DRAW_YES", price)

        assert (
            budget.sell[key] + budget.buy[key]
            == available
        )
