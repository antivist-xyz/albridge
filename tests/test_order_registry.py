
import sqlite3

import pytest

from decimal import Decimal

from albridge.hedge_ladder import HedgeLeg
from albridge.order_registry import ManagedOrder, OrderRegistry


def test_registry_survives_restart(tmp_path):
    database = tmp_path / "orders.sqlite3"

    registry = OrderRegistry(database)

    order = ManagedOrder(
        client_order_id="albridge-test-001",
        order_id=None,
        market_id=3,
        venue="pascal",
        side="BUY",
        price=Decimal("1000.00"),
        quantity=Decimal("2.000"),
        hedge_legs=(
            HedgeLeg(
                outcome="DRAW",
                pm_price=Decimal("0.30"),
                pm_quantity=Decimal("60"),
                instrument_id="pascal-draw",
            ),
            HedgeLeg(
                outcome="LOSS",
                pm_price=Decimal("0.40"),
                pm_quantity=Decimal("90"),
                instrument_id="pascal-loss",
            ),
        ),
        status="PENDING_SUBMIT",
    )

    registry.register(order)

    # Simulate process restart using a new registry instance.
    restarted_registry = OrderRegistry(database)

    recovered = restarted_registry.get(
        "albridge-test-001"
    )

    assert recovered == order
    assert recovered.hedge_legs == order.hedge_legs


def test_registry_rejects_duplicate_client_order_id(tmp_path):
    registry = OrderRegistry(
        tmp_path / "orders.sqlite3"
    )

    order = ManagedOrder(
        client_order_id="albridge-test-002",
        order_id=None,
        market_id=3,
        venue="polymarket",
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("1"),
        hedge_legs=(),
        status="PENDING_SUBMIT",
    )

    registry.register(order)

    with pytest.raises(sqlite3.IntegrityError):
        registry.register(order)

    assert registry.get(order.client_order_id) == order

def test_confirm_open_order(tmp_path):
    registry = OrderRegistry(
        tmp_path / "orders.sqlite3"
    )

    order = ManagedOrder(
        client_order_id="albridge-test-003",
        order_id=None,
        market_id=3,
        venue="pascal",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        hedge_legs=(),
        status="PENDING_SUBMIT",
    )

    registry.register(order)

    registry.confirm_open(
        client_order_id=order.client_order_id,
        order_id=123,
    )

    recovered = registry.get(order.client_order_id)

    assert recovered.order_id == 123
    assert recovered.status == "OPEN"

    # Repeating the same confirmation must be safe.
    registry.confirm_open(
        client_order_id=order.client_order_id,
        order_id=123,
    )

    # A different exchange order ID must be rejected.
    with pytest.raises(ValueError):
        registry.confirm_open(
            client_order_id=order.client_order_id,
            order_id=456,
        )

    assert registry.get(order.client_order_id).order_id == 123

def test_get_by_market(tmp_path):
    registry = OrderRegistry(
        tmp_path / "orders.sqlite3"
    )

    for client_id, market_id in (
        ("albridge-001", 3),
        ("albridge-002", 3),
        ("albridge-003", 4),
    ):
        registry.register(
            ManagedOrder(
                client_order_id=client_id,
                order_id=None,
                market_id=market_id,
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("1"),
                hedge_legs=(),
                status="PENDING_SUBMIT",
            )
        )

    orders = registry.get_by_market(market_id=3)

    assert len(orders) == 2

    assert tuple(
        order.client_order_id
        for order in orders
    ) == (
        "albridge-001",
        "albridge-002",
    )

    assert all(order.market_id == 3 for order in orders)