
"""Tests for splitting shared hedge liquidity before ladder construction."""

from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot, OutcomeBook
from albridge.risk.snapshot_split import split_snapshot_for_quote_sides

from collections import defaultdict

from albridge.hedge_ladder import build_hedge_ladders
from albridge.ladder_normalizer import normalize_hedge_ladder
from albridge.market_rules import AntivistMarketRules


def make_book(outcome, asks):
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


def test_draw_liquidity_is_split_before_ladder_building():
    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": make_book("WIN", [("0.89", "900")]),
            "DRAW": make_book(
                "DRAW",
                [
                    ("0.08", "100"),
                    ("0.09", "200"),
                ],
            ),
            "LOSS": make_book("LOSS", [("0.04", "900")]),
        },
    )

    sell_snapshot, buy_snapshot = split_snapshot_for_quote_sides(
        snapshot
    )

    assert sell_snapshot.get("DRAW").asks == (
        (Decimal("0.08"), Decimal("50")),
        (Decimal("0.09"), Decimal("100")),
    )

    assert buy_snapshot.get("DRAW").asks == (
        (Decimal("0.08"), Decimal("50")),
        (Decimal("0.09"), Decimal("100")),
    )

    assert sell_snapshot.get("WIN").asks == snapshot.get("WIN").asks
    assert buy_snapshot.get("LOSS").asks == snapshot.get("LOSS").asks

    # Preserve instrument identity and venue.
    assert sell_snapshot.venue == "polymarket"
    assert buy_snapshot.venue == "polymarket"

    assert (
        sell_snapshot.get("DRAW").symbol
        == snapshot.get("DRAW").symbol
    )

    # Original snapshot must remain untouched.
    assert snapshot.get("DRAW").asks[0][1] == Decimal("100")

    # No shared DRAW quantity is created.
    for original, sell, buy in zip(
        snapshot.get("DRAW").asks,
        sell_snapshot.get("DRAW").asks,
        buy_snapshot.get("DRAW").asks,
    ):
        assert sell[1] + buy[1] == original[1]


def test_split_preserves_multilevel_hedge_capacity():
    """
    Verify that both ladders advance through multiple
    external prices after splitting DRAW liquidity.

    No instrument/price level may be overallocated
    across Antivist BUY and SELL.
    """

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": make_book(
                "WIN",
                [
                    ("0.89", "450"),
                    ("0.90", "450"),
                    ("0.91", "450"),
                ],
            ),
            "DRAW": make_book(
                "DRAW",
                [
                    ("0.08", "300"),
                    ("0.09", "300"),
                    ("0.10", "300"),
                ],
            ),
            "LOSS": make_book(
                "LOSS",
                [
                    ("0.04", "225"),
                    ("0.05", "225"),
                    ("0.06", "225"),
                ],
            ),
        },
    )

    rules = AntivistMarketRules(
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.01"),
    )

    sell_snapshot, buy_snapshot = (
        split_snapshot_for_quote_sides(snapshot)
    )

    raw_sell, raw_buy = build_hedge_ladders(
        snapshot=snapshot,
        sell_snapshot=sell_snapshot,
        buy_snapshot=buy_snapshot,
        win_anchor=Decimal("1015"),
        draw_anchor=Decimal("985"),
        loss_anchor=Decimal("970"),
        max_levels=5,
    )

    sell = normalize_hedge_ladder(
        levels=raw_sell,
        rules=rules,
    )

    buy = normalize_hedge_ladder(
        levels=raw_buy,
        rules=rules,
    )

    # The builder must advance beyond the first price level.
    assert len(sell) >= 2
    assert len(buy) >= 2

    # Verify multiple external DRAW prices were consumed.
    sell_draw_prices = {
        leg.pm_price
        for level in sell
        for leg in level.hedge_legs
        if leg.outcome == "DRAW"
    }

    buy_draw_prices = {
        leg.pm_price
        for level in buy
        for leg in level.hedge_legs
        if leg.outcome == "DRAW"
    }

    assert len(sell_draw_prices) >= 2
    assert len(buy_draw_prices) >= 2

    # Reconstruct actual external capacity per instrument and price.
    available = defaultdict(Decimal)

    for outcome in ("WIN", "DRAW", "LOSS"):
        book = snapshot.get(outcome)

        for price, quantity in book.asks:
            available[(book.symbol, price)] += quantity

    # Reconstruct combined hedge demand from both Antivist sides.
    consumed = defaultdict(Decimal)

    for level in (*sell, *buy):
        for leg in level.hedge_legs:
            key = (leg.instrument_id, leg.pm_price)
            consumed[key] += leg.pm_quantity

    # No external price level may be allocated twice.
    for key, quantity in consumed.items():
        assert quantity <= available[key], (
            f"Overallocated {key}: "
            f"consumed={quantity}, "
            f"available={available[key]}"
        )

    # Check exact DRAW split for each external price level.
    for price, available_quantity in snapshot.get("DRAW").asks:
        sell_used = sum(
            (
                leg.pm_quantity
                for level in sell
                for leg in level.hedge_legs
                if leg.outcome == "DRAW"
                and leg.pm_price == price
            ),
            Decimal("0"),
        )

        buy_used = sum(
            (
                leg.pm_quantity
                for level in buy
                for leg in level.hedge_legs
                if leg.outcome == "DRAW"
                and leg.pm_price == price
            ),
            Decimal("0"),
        )

        assert sell_used <= available_quantity / Decimal("2")
        assert buy_used <= available_quantity / Decimal("2")
        assert sell_used + buy_used <= available_quantity
