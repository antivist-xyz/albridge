from decimal import Decimal

from albridge.order_manager import (
    DesiredOrder,
    ExistingOrder,
    ReconciliationAction,
    build_reconciliation_plan,
)
from albridge.order_registry import ManagedOrder, OrderRegistry
from albridge.hedge_ladder import HedgeLeg


MARKET_ID = 4


def make_managed(
    *,
    order_id=100,
    quantity="100",
    price="1000",
    client_order_id="albridge-test-100",
    hedge_legs=(),
):
    return ManagedOrder(
        client_order_id=client_order_id,
        order_id=order_id,
        market_id=MARKET_ID,
        venue="pascal",
        side="BUY",
        price=Decimal(price),
        quantity=Decimal(quantity),
        hedge_legs=hedge_legs,
        status="OPEN",
    )


def make_existing(managed):
    return ExistingOrder(
        order_id=managed.order_id,
        client_order_id=managed.client_order_id,
        market_id=managed.market_id,
        side=managed.side,
        price=managed.price,
        quantity=managed.quantity,
        filled_quantity=Decimal("0"),
        remaining_quantity=managed.quantity,
        status="OPEN",
    )


def make_desired(*, quantity="100", price="1000", hedge_legs=()):
    return DesiredOrder(
        venue="pascal",
        side="BUY",
        price=Decimal(price),
        quantity=Decimal(quantity),
        hedge_legs=hedge_legs,
    )


def reconcile(managed_orders, desired_orders):
    return build_reconciliation_plan(
        desired_orders=tuple(desired_orders),
        registered_orders=tuple(managed_orders),
        existing_orders=tuple(
            make_existing(order) for order in managed_orders
        ),
        market_id=MARKET_ID,
    )


def actions(plan):
    return [decision.action for decision in plan]


def test_keep_when_desired_quantity_increases():
    managed = make_managed(quantity="100")
    desired = make_desired(quantity="105")

    plan = reconcile((managed,), (desired,))

    assert actions(plan) == [ReconciliationAction.KEEP]
    assert plan[0].existing_order.quantity == Decimal("100")
    assert plan[0].desired_order.quantity == Decimal("105")


def test_cancel_when_desired_quantity_decreases():
    managed = make_managed(quantity="100")
    desired = make_desired(quantity="95")

    plan = reconcile((managed,), (desired,))

    assert actions(plan) == [ReconciliationAction.CANCEL]
    assert plan[0].existing_order.order_id == 100
    assert all(
        decision.action != ReconciliationAction.SUBMIT
        for decision in plan
    )


def test_keep_when_only_hedge_legs_change():
    old_legs = (
        HedgeLeg(
            outcome="DRAW",
            pm_price=Decimal("0.20"),
            pm_quantity=Decimal("50"),
            instrument_id="old-token",
            execution_side="BUY",
        ),
    )

    new_legs = (
        HedgeLeg(
            outcome="DRAW",
            pm_price=Decimal("0.21"),
            pm_quantity=Decimal("50"),
            instrument_id="new-token",
            execution_side="BUY",
        ),
    )

    managed = make_managed(hedge_legs=old_legs)
    desired = make_desired(hedge_legs=new_legs)

    plan = reconcile((managed,), (desired,))

    assert actions(plan) == [ReconciliationAction.KEEP]
    assert plan[0].desired_order.hedge_legs == new_legs


def test_one_desired_quote_cannot_keep_two_orders():
    first = make_managed(
        order_id=100,
        quantity="60",
        client_order_id="albridge-test-100",
    )

    second = make_managed(
        order_id=101,
        quantity="60",
        client_order_id="albridge-test-101",
    )

    desired = make_desired(quantity="100")

    plan = reconcile((first, second), (desired,))

    assert actions(plan).count(ReconciliationAction.KEEP) == 1
    assert actions(plan).count(ReconciliationAction.CANCEL) == 1
    assert actions(plan).count(ReconciliationAction.SUBMIT) == 0

    kept_ids = [
        decision.existing_order.order_id
        for decision in plan
        if decision.action == ReconciliationAction.KEEP
    ]

    canceled_ids = [
        decision.existing_order.order_id
        for decision in plan
        if decision.action == ReconciliationAction.CANCEL
    ]

    assert set(kept_ids + canceled_ids) == {100, 101}
    assert set(kept_ids).isdisjoint(canceled_ids)


def test_registry_updates_kept_hedge_legs(tmp_path):
    registry = OrderRegistry(tmp_path / "turnover.sqlite3")
    managed = make_managed()
    registry.register(managed)

    new_legs = (
        HedgeLeg(
            outcome="DRAW",
            pm_price=Decimal("0.21"),
            pm_quantity=Decimal("50"),
            instrument_id="test-token",
            execution_side="BUY",
        ),
    )

    registry.update_kept_hedges(
        (
            (managed.client_order_id, managed.order_id, new_legs),
        )
    )

    stored = registry.get(managed.client_order_id)

    assert stored is not None
    assert stored.status == "OPEN"
    assert stored.order_id == 100
    assert stored.quantity == Decimal("100")
    assert stored.hedge_legs == new_legs
