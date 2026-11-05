from decimal import Decimal

from albridge.hedge_ladder import HedgeLadderLevel, HedgeLeg
from albridge.order_manager import build_desired_orders, select_managed_orders

import pytest

from albridge.order_manager import (
    ExistingOrder,
    parse_existing_orders,
    ReconciliationAction,
    DesiredOrder,
    build_reconciliation_plan,
)

from albridge.order_manager import generate_client_order_id, match_registered_open_orders
from albridge.order_registry import ManagedOrder

def test_desired_orders_preserve_venue_and_hedge_legs():
    pascal_leg = HedgeLeg(
        outcome="DRAW",
        pm_price=Decimal("0.30"),
        pm_quantity=Decimal("30"),
        instrument_id="pascal-draw",
    )

    polymarket_leg = HedgeLeg(
        outcome="LOSS",
        pm_price=Decimal("0.40"),
        pm_quantity=Decimal("40"),
        instrument_id="polymarket-loss",
    )

    ladders = {
        "pascal": {
            "buy": (
                HedgeLadderLevel(
                    venue="pascal",
                    side="BUY",
                    price=Decimal("1000.00"),
                    quantity=Decimal("2.000"),
                    hedge_legs=(pascal_leg,),
                ),
            ),
            "sell": (),
        },
        "polymarket": {
            "buy": (
                HedgeLadderLevel(
                    venue="polymarket",
                    side="BUY",
                    price=Decimal("1000.00"),
                    quantity=Decimal("3.000"),
                    hedge_legs=(polymarket_leg,),
                ),
            ),
            "sell": (),
        },
    }

    desired = build_desired_orders(
        risk_ladders_by_venue=ladders,
    )

    assert len(desired) == 2

    assert desired[0].venue == "pascal"
    assert desired[0].quantity == Decimal("2.000")
    assert desired[0].hedge_legs == (pascal_leg,)

    assert desired[1].venue == "polymarket"
    assert desired[1].quantity == Decimal("3.000")
    assert desired[1].hedge_legs == (polymarket_leg,)

    # Both orders have the same price but remain separate.
    assert desired[0].price == desired[1].price

def test_parse_existing_orders():
    response = {
        "orders": [
            {
                "order_id": 42,
                "client_order_id": "albridge-pascal-test",
                "market_id": 3,
                "side": "BUY",
                "price": "1000.00000000",
                "quantity": "2.00000000",
                "filled_quantity": "0.50000000",
                "remaining_quantity": "1.50000000",
                "status": "PARTIALLY_FILLED",
            },
        ]
    }

    orders = parse_existing_orders(response=response)

    assert len(orders) == 1
    assert isinstance(orders[0], ExistingOrder)

    assert orders[0].order_id == 42
    assert orders[0].market_id == 3
    assert orders[0].price == Decimal("1000")
    assert orders[0].quantity == Decimal("2")
    assert orders[0].filled_quantity == Decimal("0.5")
    assert orders[0].remaining_quantity == Decimal("1.5")
    assert orders[0].status == "PARTIALLY_FILLED"


def test_parse_empty_orders():
    assert parse_existing_orders(
        response={"orders": []}
    ) == ()


def test_reject_inconsistent_order_quantities():
    response = {
        "orders": [
            {
                "order_id": 43,
                "client_order_id": "albridge-pascal-test",
                "market_id": 3,
                "side": "SELL",
                "price": "1000",
                "quantity": "2",
                "filled_quantity": "1",
                "remaining_quantity": "2",
                "status": "PARTIALLY_FILLED",
            },
        ]
    }

    with pytest.raises(ValueError):
        parse_existing_orders(response=response)


def test_select_only_managed_orders():
    def make_order(order_id, client_order_id, market_id=3):
        return ExistingOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            market_id=market_id,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("2"),
            status="OPEN",
        )

    orders = (
        make_order(1, "albridge-pascal-001"),
        make_order(2, "manual-001"),
        make_order(3, "albridge-polymarket-001"),
        make_order(4, "albridge-other-market", market_id=4),
        make_order(5, "albridge-unregistered"),
    )

    managed = select_managed_orders(
        existing_orders=orders,
        managed_client_order_ids=frozenset({
            "albridge-pascal-001",
            "albridge-polymarket-001",
        }),
        market_id=3,
    )

    assert tuple(order.order_id for order in managed) == (1, 3)

def test_generate_client_order_id():
    first = generate_client_order_id()
    second = generate_client_order_id()

    assert first.startswith("albridge-")
    assert second.startswith("albridge-")
    assert first != second
    assert len(first) == len("albridge-") + 32


def test_match_registered_open_orders():
    registered = ManagedOrder(
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

    existing = ExistingOrder(
        order_id=123,
        client_order_id="albridge-test-001",
        market_id=3,
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        filled_quantity=Decimal("0.5"),
        remaining_quantity=Decimal("1.5"),
        status="PARTIALLY_FILLED",
    )

    manual = ExistingOrder(
        order_id=456,
        client_order_id="manual-order",
        market_id=3,
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("2"),
        status="OPEN",
    )

    matches = match_registered_open_orders(
        registered_orders=(registered,),
        existing_orders=(existing, manual),
    )

    assert matches == ((registered, existing),)


def test_missing_registered_order_is_not_assumed_canceled():
    registered = ManagedOrder(
        client_order_id="albridge-test-002",
        order_id=124,
        market_id=3,
        venue="polymarket",
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("1"),
        hedge_legs=(),
        status="OPEN",
    )

    matches = match_registered_open_orders(
        registered_orders=(registered,),
        existing_orders=(),
    )

    assert matches == ()

    # The function must not change persistent order status.
    assert registered.status == "OPEN"



def test_reconciliation_keeps_partial_fill():
    desired = DesiredOrder(
        venue="pascal",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        hedge_legs=(),
    )

    registered = ManagedOrder(
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

    existing = ExistingOrder(
        order_id=123,
        client_order_id="albridge-test-001",
        market_id=3,
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        filled_quantity=Decimal("0.5"),
        remaining_quantity=Decimal("1.5"),
        status="PARTIALLY_FILLED",
    )

    plan = build_reconciliation_plan(
        desired_orders=(desired,),
        registered_orders=(registered,),
        existing_orders=(existing,),
        market_id=3,
    )

    assert len(plan) == 1
    assert plan[0].action == ReconciliationAction.KEEP
    assert plan[0].existing_order.order_id == 123
    assert plan[0].desired_order == desired

def test_reconciliation_cancels_obsolete_order():
    registered = ManagedOrder(
        client_order_id="albridge-test-002",
        order_id=124,
        market_id=3,
        venue="pascal",
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("2"),
        hedge_legs=(),
        status="OPEN",
    )

    existing = ExistingOrder(
        order_id=124,
        client_order_id="albridge-test-002",
        market_id=3,
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("2"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("2"),
        status="OPEN",
    )

    plan = build_reconciliation_plan(
        desired_orders=(),
        registered_orders=(registered,),
        existing_orders=(existing,),
        market_id=3,
    )

    assert len(plan) == 1
    assert plan[0].action == ReconciliationAction.CANCEL
    assert plan[0].existing_order.order_id == 124

def test_reconciliation_submits_new_order():
    desired = DesiredOrder(
        venue="polymarket",
        side="BUY",
        price=Decimal("995"),
        quantity=Decimal("3"),
        hedge_legs=(),
    )

    plan = build_reconciliation_plan(
        desired_orders=(desired,),
        registered_orders=(),
        existing_orders=(),
        market_id=3,
    )

    assert len(plan) == 1
    assert plan[0].action == ReconciliationAction.SUBMIT
    assert plan[0].desired_order == desired

def test_reconciliation_keeps_venues_separate():
    def make_desired(venue):
        return DesiredOrder(
            venue=venue,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
        )

    def make_registered(venue, order_id):
        return ManagedOrder(
            client_order_id=f"albridge-{venue}-001",
            order_id=order_id,
            market_id=3,
            venue=venue,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
            status="OPEN",
        )

    def make_existing(venue, order_id):
        return ExistingOrder(
            order_id=order_id,
            client_order_id=f"albridge-{venue}-001",
            market_id=3,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("2"),
            status="OPEN",
        )

    pascal = make_desired("pascal")
    polymarket = make_desired("polymarket")

    plan = build_reconciliation_plan(
        desired_orders=(pascal, polymarket),
        registered_orders=(
            make_registered("pascal", 101),
            make_registered("polymarket", 102),
        ),
        existing_orders=(
            make_existing("polymarket", 102),
            make_existing("pascal", 101),
        ),
        market_id=3,
    )

    assert len(plan) == 2

    assert all(
        decision.action == ReconciliationAction.KEEP
        for decision in plan
    )

    assert {
        decision.existing_order.order_id:
        decision.desired_order.venue
        for decision in plan
    } == {
        101: "pascal",
        102: "polymarket",
    }

def test_reconciliation_matches_duplicate_orders_one_to_one():
    desired = DesiredOrder(
        venue="pascal",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        hedge_legs=(),
    )

    registered_orders = tuple(
        ManagedOrder(
            client_order_id=f"albridge-{order_id}",
            order_id=order_id,
            market_id=3,
            venue="pascal",
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
            status="OPEN",
        )
        for order_id in (101, 102)
    )

    existing_orders = tuple(
        ExistingOrder(
            order_id=order_id,
            client_order_id=f"albridge-{order_id}",
            market_id=3,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("2"),
            status="OPEN",
        )
        for order_id in (101, 102)
    )

    plan = build_reconciliation_plan(
        desired_orders=(desired,),
        registered_orders=registered_orders,
        existing_orders=existing_orders,
        market_id=3,
    )

    assert len(plan) == 2

    keeps = [
        decision for decision in plan
        if decision.action == ReconciliationAction.KEEP
    ]

    cancels = [
        decision for decision in plan
        if decision.action == ReconciliationAction.CANCEL
    ]

    assert len(keeps) == 1
    assert len(cancels) == 1

    # Deterministic matching: keep the first registered order.
    assert keeps[0].existing_order.order_id == 101
    assert cancels[0].existing_order.order_id == 102

def test_reconciliation_matches_duplicate_orders_one_to_one():
    desired = DesiredOrder(
        venue="pascal",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        hedge_legs=(),
    )

    registered_orders = tuple(
        ManagedOrder(
            client_order_id=f"albridge-{order_id}",
            order_id=order_id,
            market_id=3,
            venue="pascal",
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
            status="OPEN",
        )
        for order_id in (101, 102)
    )

    existing_orders = tuple(
        ExistingOrder(
            order_id=order_id,
            client_order_id=f"albridge-{order_id}",
            market_id=3,
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("2"),
            status="OPEN",
        )
        for order_id in (101, 102)
    )

    plan = build_reconciliation_plan(
        desired_orders=(desired,),
        registered_orders=registered_orders,
        existing_orders=existing_orders,
        market_id=3,
    )

    assert len(plan) == 2

    keeps = [
        decision for decision in plan
        if decision.action == ReconciliationAction.KEEP
    ]

    cancels = [
        decision for decision in plan
        if decision.action == ReconciliationAction.CANCEL
    ]

    assert len(keeps) == 1
    assert len(cancels) == 1

    # Deterministic matching: keep the first registered order.
    assert keeps[0].existing_order.order_id == 101
    assert cancels[0].existing_order.order_id == 102


def test_keep_when_desired_quantity_increases():
    # Use existing OPEN order with quantity=100.
    # Build matching desired quote with quantity=105.
    # Same venue, side and price.
    #
    # Expected:
    # KEEP existing 100
    # No CANCEL
    # No additional SUBMIT
    pass


def test_cancel_when_desired_quantity_decreases():
    # Use existing OPEN order with quantity=100.
    # Build matching desired quote with quantity=95.
    #
    # Expected:
    # CANCEL existing 100
    # No SUBMIT in the same reconciliation plan
    pass
