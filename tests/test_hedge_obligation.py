
from decimal import Decimal

from albridge.hedge_ladder import HedgeLeg
from albridge.order_registry import ManagedOrder
from albridge.risk.fill_registry import AntivistFill
from albridge.risk.hedge_obligation import (
    build_hedge_obligations,
)


def test_partial_fill_generates_proportional_hedges():
    managed = ManagedOrder(
        client_order_id="albridge-test-001",
        order_id=123,
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
        status="PARTIALLY_FILLED",
    )

    fill = AntivistFill(
        execution_id=456,
        trade_id=789,
        order_id=123,
        client_order_id="albridge-test-001",
        market_id=3,
        side="BUY",
        quantity=Decimal("2"),
        price=Decimal("1000"),
        created_at="2026-11-01T10:00:00+00:00",
    )

    obligations = build_hedge_obligations(
        fill=fill,
        managed_order=managed,
    )

    assert len(obligations) == 2

    draw, loss = obligations

    assert draw.quantity == Decimal("60")
    assert loss.quantity == Decimal("90")

    assert draw.outcome == "DRAW"
    assert loss.outcome == "LOSS"

    assert draw.execution_side == "BUY"
    assert loss.execution_side == "BUY"

    assert all(
        obligation.execution_id == 456
        for obligation in obligations
    )
