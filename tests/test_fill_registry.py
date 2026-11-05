
from decimal import Decimal

import pytest

from albridge.order_registry import ManagedOrder, OrderRegistry
from albridge.risk.fill_registry import (
    AntivistFill,
    FillRegistry,
    parse_execution,
)


def test_fill_registry_deduplicates_across_restart(tmp_path):
    database = tmp_path / "albridge.sqlite3"

    order_registry = OrderRegistry(database)

    order_registry.register(
        ManagedOrder(
            client_order_id="albridge-test-001",
            order_id=123,
            market_id=3,
            venue="pascal",
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
            status="OPEN",
        )
    )

    raw_execution = {
        "execution_id": 456,
        "trade_id": 789,
        "order_id": 123,
        "client_order_id": "albridge-test-001",
        "market_id": 3,
        "side": "BUY",
        "quantity": "0.50000000",
        "price": "1000.00000000",
        "created_at": "2026-11-01T10:00:00+00:00",
    }

    fill = parse_execution(raw_execution)

    registry = FillRegistry(database)

    assert registry.register(fill) is True

    # Simulate Albridge restarting.
    restarted_registry = FillRegistry(database)

    assert restarted_registry.register(fill) is False


def test_fill_registry_rejects_unmanaged_execution(tmp_path):
    registry = FillRegistry(
        tmp_path / "albridge.sqlite3"
    )

    fill = AntivistFill(
        execution_id=999,
        trade_id=1000,
        order_id=888,
        client_order_id="manual-order",
        market_id=3,
        side="SELL",
        quantity=Decimal("1"),
        price=Decimal("1010"),
        created_at="2026-11-01T10:00:00+00:00",
    )

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        registry.register(fill)
