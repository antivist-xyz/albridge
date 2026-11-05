
from decimal import Decimal

import pytest

from albridge.fees import (
    calculate_taker_fee,
    calculate_hedge_cost,
)

from albridge.hedge_ladder import _build_side
from albridge.market_snapshot import MarketSnapshot


def test_polymarket_sports_fee():
    fee = calculate_taker_fee(
        price=Decimal("0.60"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.05"),
        precision=Decimal("0.00001"),
    )

    assert fee == Decimal("1.20000")


def test_pascal_fee():
    fee = calculate_taker_fee(
        price=Decimal("0.60"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.02"),
        precision=Decimal("0.000001"),
    )

    assert fee == Decimal("0.480000")


def test_fee_adjusted_hedge_cost():
    cost = calculate_hedge_cost(
        price=Decimal("0.60"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.05"),
        precision=Decimal("0.00001"),
    )

    assert cost == Decimal("61.20000")


def test_zero_fee():
    fee = calculate_taker_fee(
        price=Decimal("0.80"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0"),
        precision=Decimal("0.00001"),
    )

    assert fee == Decimal("0")


def test_invalid_price():
    with pytest.raises(ValueError):
        calculate_taker_fee(
            price=Decimal("1.1"),
            quantity=Decimal("100"),
            fee_rate=Decimal("0.05"),
            precision=Decimal("0.00001"),
        )

def test_hedge_ladder_includes_taker_fees():
    class Book:
        def __init__(self):
            self.asks = ((Decimal("0.50"), Decimal("1000")),)
            self.symbol = "TEST_TOKEN"

    class Snapshot:
        venue = "polymarket"

        def get(self, outcome):
            return Book()

    base = dict(
        snapshot=Snapshot(),
        side="SELL",
        base_anchor=Decimal("970"),
        ratios={"WIN": Decimal("10")},
        max_levels=1,
    )

    without_fees = _build_side(
        **base,
        fee_rates={"WIN": Decimal("0")},
    )

    with_fees = _build_side(
        **base,
        fee_rates={"WIN": Decimal("0.05")},
    )

    assert without_fees[0].price == Decimal("975")
    assert with_fees[0].price == Decimal("975.125")

    assert with_fees[0].quantity == without_fees[0].quantity
    assert with_fees[0].hedge_legs == without_fees[0].hedge_legs