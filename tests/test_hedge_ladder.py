
from decimal import Decimal

from albridge.hedge_ladder import build_hedge_ladders
from albridge.market_snapshot import MarketSnapshot, OutcomeBook

from albridge.ladder_normalizer import normalize_hedge_ladder
from albridge.market_rules import AntivistMarketRules


def book(outcome, asks):
    return OutcomeBook(
        outcome=outcome,
        symbol=f"{outcome}_YES",
        bids=(),
        asks=tuple(
            (Decimal(price), Decimal(size))
            for price, size in asks
        ),
        sequence=1,
        state_time_ms=1000,
    )


def test_sell_ladder_consumes_depth_without_reuse():
    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": book("WIN", [
                ("0.89", "450"),
                ("0.90", "900"),
            ]),
            "DRAW": book("DRAW", [
                ("0.08", "300"),
                ("0.09", "150"),
            ]),
            "LOSS": book("LOSS", [
                ("0.04", "900"),
            ]),
        },
    )

    sell, buy = build_hedge_ladders(
        snapshot=snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
    )

    assert sell[0].venue == "polymarket"
    assert sell[0].side == "SELL"
    assert sell[0].price == Decimal("1011.25")
    assert sell[0].quantity == Decimal("10")

    assert sell[1].price == Decimal("1011.70")
    assert sell[1].quantity == Decimal("10")

    # DRAW level 1 is now exhausted.
    assert sell[2].price == Decimal("1011.85")
    assert sell[2].quantity == Decimal("10")

    assert sell[0].hedge_legs[0].pm_quantity == Decimal("450")
    assert sell[0].hedge_legs[1].pm_quantity == Decimal("150")

    # BUY ladder is constructed independently.
    assert len(buy) > 0


def test_buy_ladder_walks_loss_depth():
    snapshot = MarketSnapshot(
        venue="pascal",
        outcomes={
            "WIN": book("WIN", [("0.89", "450")]),
            "DRAW": book("DRAW", [
                ("0.08", "300"),
                ("0.09", "300"),
            ]),
            "LOSS": book("LOSS", [
                ("0.04", "90"),
                ("0.05", "180"),
            ]),
        },
    )

    _, buy = build_hedge_ladders(
        snapshot=snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
    )

    assert buy[0].side == "BUY"
    assert buy[0].quantity == Decimal("2")
    assert buy[0].price == Decimal("1010.80")

    assert buy[1].quantity == Decimal("4")
    assert buy[1].price == Decimal("1010.35")


def test_missing_required_book_returns_empty_side():
    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": book("WIN", [("0.89", "450")]),
            "DRAW": book("DRAW", [("0.08", "300")]),
            "LOSS": book("LOSS", []),
        },
    )

    sell, buy = build_hedge_ladders(
        snapshot=snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
    )

    assert len(sell) == 1
    assert buy == ()

def test_instrument_ids_survive_ladder_normalization():
    """
    Verify that hedge instruments remain correctly associated
    with their outcomes after ladder normalization.
    """

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": book("WIN", [
                ("0.89", "0.270"),
                ("0.90", "0.405"),
            ]),
            "DRAW": book("DRAW", [
                ("0.08", "0.225"),
            ]),
            "LOSS": book("LOSS", [
                ("0.04", "1.000"),
            ]),
        },
    )

    sell, _ = build_hedge_ladders(
        snapshot=snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
    )

    rules = AntivistMarketRules(
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.01"),
    )

    normalized = normalize_hedge_ladder(
        levels=sell,
        rules=rules,
    )

    assert len(normalized) == 1

    level = normalized[0]

    assert level.quantity == Decimal("0.015")
    assert level.price == Decimal("1011.52")

    win_legs = [
        leg for leg in level.hedge_legs
        if leg.outcome == "WIN"
    ]

    draw_legs = [
        leg for leg in level.hedge_legs
        if leg.outcome == "DRAW"
    ]

    assert len(win_legs) == 2
    assert len(draw_legs) == 1

    assert all(
        leg.instrument_id == "WIN_YES"
        for leg in win_legs
    )

    assert draw_legs[0].instrument_id == "DRAW_YES"

    assert {
        (leg.pm_price, leg.pm_quantity)
        for leg in win_legs
    } == {
        (Decimal("0.89"), Decimal("0.270")),
        (Decimal("0.90"), Decimal("0.405")),
    }

    assert draw_legs[0].pm_quantity == Decimal("0.225")

def test_different_fee_rates_per_outcome():
    class Book:
        def __init__(self, price):
            self.asks = ((Decimal(price), Decimal("1000")),)
            self.symbol = "TEST"

    class Snapshot:
        venue = "polymarket"

        def get(self, outcome):
            return Book("0.50")

    sell, buy = build_hedge_ladders(
        snapshot=Snapshot(),
        win_anchor=Decimal("1030"),
        draw_anchor=Decimal("1000"),
        loss_anchor=Decimal("985"),
        max_levels=1,
        fee_rates={
            "WIN": Decimal("0.05"),
            "DRAW": Decimal("0.02"),
            "LOSS": Decimal("0"),
        },
    )

    # SELL: 45 WIN + 15 DRAW
    # Raw cost = 30
    # Fees = 45*0.25*0.05 + 15*0.25*0.02 = 0.6375
    assert sell[0].price == Decimal("1015.6375")

    # BUY: 30 DRAW + 45 LOSS
    # Raw cost = 37.5
    # Fees = 30*0.25*0.02 = 0.15
    assert buy[0].price == Decimal("992.35")