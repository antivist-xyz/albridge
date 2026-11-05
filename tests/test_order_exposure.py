
from decimal import Decimal

import pytest

from albridge.hedge_ladder import HedgeLeg
from albridge.order_manager import ExistingOrder
from albridge.order_registry import ManagedOrder
from albridge.risk.order_exposure import calculate_order_exposure


def test_partial_fill_splits_reserved_and_hedge_due():
    registered = ManagedOrder(
        client_order_id="albridge-test-101",
        order_id=101,
        market_id=3,
        venue="polymarket",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("6"),
        hedge_legs=(
            HedgeLeg(
                outcome="DRAW",
                pm_price=Decimal("0.40"),
                pm_quantity=Decimal("180"),
                instrument_id="draw-token",
            ),
            HedgeLeg(
                outcome="LOSS",
                pm_price=Decimal("0.25"),
                pm_quantity=Decimal("270"),
                instrument_id="loss-token",
            ),
        ),
        status="OPEN",
    )

    existing = ExistingOrder(
        order_id=101,
        client_order_id="albridge-test-101",
        market_id=3,
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("6"),
        filled_quantity=Decimal("2"),
        remaining_quantity=Decimal("4"),
        status="PARTIALLY_FILLED",
    )

    exposures = calculate_order_exposure(
        registered=registered,
        existing=existing,
    )

    assert len(exposures) == 2

    draw, loss = exposures

    assert draw.hedge_ratio == Decimal("30")
    assert draw.reserved_quantity == Decimal("120")
    assert draw.gross_hedge_due == Decimal("60")

    assert loss.hedge_ratio == Decimal("45")
    assert loss.reserved_quantity == Decimal("180")
    assert loss.gross_hedge_due == Decimal("90")

    # No hedge capacity is lost or created by the split.
    assert (
        draw.reserved_quantity + draw.gross_hedge_due
        == Decimal("180")
    )

    assert (
        loss.reserved_quantity + loss.gross_hedge_due
        == Decimal("270")
    )


def test_order_identity_mismatch_is_rejected():
    registered = ManagedOrder(
        client_order_id="albridge-101",
        order_id=101,
        market_id=3,
        venue="pascal",
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("2"),
        hedge_legs=(),
        status="OPEN",
    )

    existing = ExistingOrder(
        order_id=102,
        client_order_id="albridge-101",
        market_id=3,
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("2"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("2"),
        status="OPEN",
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        calculate_order_exposure(
            registered=registered,
            existing=existing,
        )
