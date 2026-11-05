from decimal import Decimal

from albridge.hedge_ladder import HedgeLeg
from albridge.order_manager import DesiredOrder
from albridge.quote_budget import allocate_quote_budget


D = Decimal


def make_order(venue, side, price, quantity="100"):
    return DesiredOrder(
        venue=venue,
        side=side,
        price=D(str(price)),
        quantity=D(quantity),
        hedge_legs=(
            HedgeLeg(
                outcome="DRAW",
                pm_price=D("0.20"),
                pm_quantity=D(quantity),
                instrument_id="test-token",
                execution_side="BUY",
            ),
        ),
    )


def allocate(
    orders,
    *,
    total_notional=D("20000"),
    max_order_notional=D("2500"),
    levels_per_side=2,
    level_size_decay=D("0.80"),
):
    return allocate_quote_budget(
        orders=tuple(orders),
        quantity_step=D("0.001"),
        min_quantity=D("0.01"),
        total_notional=total_notional,
        max_order_notional=max_order_notional,
        levels_per_side=levels_per_side,
        level_size_decay=level_size_decay,
    )

def test_budget_limits_total_exposure():
    orders = [
        make_order(venue, side, price)
        for venue in ("pascal", "polymarket")
        for side, prices in (
            ("BUY", [1000, 999, 998, 997, 996]),
            ("SELL", [1001, 1002, 1003, 1004, 1005]),
        )
        for price in prices
    ]

    result = allocate(orders)

    assert len(result) == 8

    assert sum(
        order.price * order.quantity
        for order in result
    ) <= D("20000")

    assert all(
        order.price * order.quantity <= D("2500")
        for order in result
    )

    assert all(
        len(order.hedge_legs) == 1
        for order in result
    )


def test_hedge_capacity_is_never_exceeded():
    source = make_order("pascal", "BUY", 1000, "0.5")

    result = allocate([source])

    assert len(result) == 1
    assert result[0].quantity == D("0.5")
    assert result[0].hedge_legs == source.hedge_legs


def test_hedge_legs_scale_with_order_quantity():
    source = make_order("pascal", "BUY", 1000, "100")

    result = allocate([source])

    assert len(result) == 1
    assert result[0].quantity == D("2.500")

    assert (
        result[0].hedge_legs[0].pm_quantity
        == D("2.500")
    )


def test_deeper_levels_receive_less_notional():
    result = allocate([
        make_order("pascal", "BUY", 1000),
        make_order("pascal", "BUY", 999),
    ])

    assert len(result) == 2

    notionals = [
        order.price * order.quantity
        for order in result
    ]

    assert notionals[0] <= D("2500")
    assert notionals[1] <= D("2000")
    assert notionals[1] < notionals[0]


def test_only_best_levels_are_selected():
    result = allocate([
        make_order("pascal", "BUY", 998),
        make_order("pascal", "BUY", 1000),
        make_order("pascal", "BUY", 999),
    ])

    assert [order.price for order in result] == [
        D("1000"),
        D("999"),
    ]


def test_budget_below_min_quantity_skips_order():
    result = allocate(
        [make_order("pascal", "BUY", 1000)],
        total_notional=D("5"),
    )

    assert result == ()

def test_five_levels_per_side():
    orders = [
        make_order("pascal", "BUY", price)
        for price in (1000, 999, 998, 997, 996)
    ]

    result = allocate_quote_budget(
        orders=tuple(orders),
        quantity_step=D("0.001"),
        min_quantity=D("0.01"),
        total_notional=D("100000"),
        max_order_notional=D("5000"),
        levels_per_side=5,
        level_size_decay=D("0.80"),
    )

    assert len(result) == 5
    assert [order.price for order in result] == [
        D("1000"),
        D("999"),
        D("998"),
        D("997"),
        D("996"),
    ]