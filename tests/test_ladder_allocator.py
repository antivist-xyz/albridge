
"""Tests for risk-budget allocation across normalized hedge ladders."""

from decimal import Decimal

from albridge.hedge_ladder import HedgeLadderLevel, HedgeLeg
from albridge.market_rules import AntivistMarketRules
from albridge.risk.ladder_allocator import allocate_ladder_to_budget, allocate_ladder_to_price_budget
from albridge.risk.quote_risk_budget import (
    calculate_quote_risk_budget,
)
from albridge.risk.price_level_budget import (
    calculate_price_level_budget,
)


RULES = AntivistMarketRules(
    tick_size=Decimal("0.01"),
    quantity_step=Decimal("0.001"),
    min_quantity=Decimal("0.01"),
)


def sell_level(price: str, quantity: str) -> HedgeLadderLevel:
    """Build a normalized SELL level with complete hedge quantities."""

    q = Decimal(quantity)

    return HedgeLadderLevel(
        venue="polymarket",
        side="SELL",
        price=Decimal(price),
        quantity=q,
        hedge_legs=(
            HedgeLeg(
                outcome="WIN",
                pm_price=Decimal("0.89"),
                pm_quantity=q * Decimal("45"),
                instrument_id="WIN_YES",
            ),
            HedgeLeg(
                outcome="DRAW",
                pm_price=Decimal("0.08"),
                pm_quantity=q * Decimal("15"),
                instrument_id="DRAW_YES",
            ),
        ),
    )


def test_allocator_partially_consumes_last_level():
    """
    Allocate best levels first and resize the last level
    without exceeding either instrument's token budget.
    """

    levels = (
        sell_level("1011.25", "4"),
        sell_level("1011.70", "8"),
        sell_level("1011.85", "5"),
    )

    result = allocate_ladder_to_budget(
        levels=levels,
        token_budget={
            "WIN_YES": Decimal("450"),
            "DRAW_YES": Decimal("150"),
        },
        rules=RULES,
    )

    # Budget permits 10 Antivist contracts in total.
    assert len(result) == 2

    assert [level.quantity for level in result] == [
        Decimal("4"),
        Decimal("6"),
    ]

    # The allocated levels retain their original quote prices.
    assert [level.price for level in result] == [
        Decimal("1011.25"),
        Decimal("1011.70"),
    ]

    total_win = sum(
        (
            leg.pm_quantity
            for level in result
            for leg in level.hedge_legs
            if leg.instrument_id == "WIN_YES"
        ),
        Decimal("0"),
    )

    total_draw = sum(
        (
            leg.pm_quantity
            for level in result
            for leg in level.hedge_legs
            if leg.instrument_id == "DRAW_YES"
        ),
        Decimal("0"),
    )

    assert total_win == Decimal("450")
    assert total_draw == Decimal("150")

    # Verify each individual quote retains its hedge ratios.
    for level in result:
        legs = {
            leg.instrument_id: leg
            for leg in level.hedge_legs
        }

        assert (
            legs["WIN_YES"].pm_quantity
            == level.quantity * Decimal("45")
        )

        assert (
            legs["DRAW_YES"].pm_quantity
            == level.quantity * Decimal("15")
        )

def test_allocator_rounds_down_without_exceeding_budget():
    """
    Quantity normalization must never increase
    the external hedge liquidity required.
    """

    levels = (
        sell_level("1011.25", "0.020"),
    )

    result = allocate_ladder_to_budget(
        levels=levels,
        token_budget={
            "WIN_YES": Decimal("0.4815"),
            "DRAW_YES": Decimal("0.1605"),
        },
        rules=RULES,
    )

    assert len(result) == 1

    level = result[0]

    # The budget supports 0.0107 contracts,
    # but the Antivist quantity step is 0.001.
    assert level.quantity == Decimal("0.010")

    legs = {
        leg.instrument_id: leg
        for leg in level.hedge_legs
    }

    assert legs["WIN_YES"].pm_quantity == Decimal("0.450")
    assert legs["DRAW_YES"].pm_quantity == Decimal("0.150")

    assert legs["WIN_YES"].pm_quantity <= Decimal("0.4815")
    assert legs["DRAW_YES"].pm_quantity <= Decimal("0.1605")

def test_allocator_rejects_quantity_below_minimum():
    """
    Do not publish an Antivist order when the available
    hedge budget cannot support the minimum quantity.
    """

    levels = (
        sell_level("1011.25", "0.020"),
    )

    result = allocate_ladder_to_budget(
        levels=levels,
        token_budget={
            "WIN_YES": Decimal("0.400"),
            "DRAW_YES": Decimal("0.150"),
        },
        rules=RULES,
    )

    # WIN limits capacity to 0.008888... contracts.
    # This is below Antivist's minimum quantity of 0.01.
    assert result == ()


def test_allocator_never_exceeds_cumulative_token_budget():
    """
    Ensure cumulative hedge allocation never exceeds
    the available liquidity for any instrument.
    """

    levels = (
        sell_level("1011.25", "0.100"),
        sell_level("1011.70", "0.200"),
        sell_level("1011.85", "0.300"),
    )

    token_budget = {
        "WIN_YES": Decimal("12.800"),
        "DRAW_YES": Decimal("4.500"),
    }

    result = allocate_ladder_to_budget(
        levels=levels,
        token_budget=token_budget,
        rules=RULES,
    )

    # WIN limits total capacity to 12.8 / 45.
    # Normalized maximum: 0.284 contracts.
    assert [level.quantity for level in result] == [
        Decimal("0.100"),
        Decimal("0.184"),
    ]

    allocated = {}

    for level in result:
        for leg in level.hedge_legs:
            allocated[leg.instrument_id] = (
                allocated.get(leg.instrument_id, Decimal("0"))
                + leg.pm_quantity
            )

    for instrument_id, quantity in allocated.items():
        assert quantity <= token_budget[instrument_id]

    assert allocated["WIN_YES"] == Decimal("12.780")
    assert allocated["DRAW_YES"] == Decimal("4.260")

    # All published quantities must satisfy Antivist rules.
    for level in result:
        assert level.quantity >= RULES.min_quantity
        assert level.quantity % RULES.quantity_step == 0


def test_buy_and_sell_share_draw_budget_50_50():
    """
    Verify that BUY and SELL allocations together
    cannot consume more DRAW YES than available.
    """

    budget = calculate_quote_risk_budget(
        available={
            "WIN_YES": Decimal("900"),
            "DRAW_YES": Decimal("300"),
            "LOSS_YES": Decimal("900"),
        },
        sell_ratios={
            "WIN_YES": Decimal("45"),
            "DRAW_YES": Decimal("15"),
        },
        buy_ratios={
            "DRAW_YES": Decimal("30"),
            "LOSS_YES": Decimal("45"),
        },
    )

    sell_levels = (
        sell_level("1011.25", "12"),
    )

    buy_levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="BUY",
            price=Decimal("1010.80"),
            quantity=Decimal("8"),
            hedge_legs=(
                HedgeLeg(
                    outcome="DRAW",
                    pm_price=Decimal("0.08"),
                    pm_quantity=Decimal("240"),
                    instrument_id="DRAW_YES",
                ),
                HedgeLeg(
                    outcome="LOSS",
                    pm_price=Decimal("0.04"),
                    pm_quantity=Decimal("360"),
                    instrument_id="LOSS_YES",
                ),
            ),
        ),
    )

    allocated_sell = allocate_ladder_to_budget(
        levels=sell_levels,
        token_budget=budget.sell_token_budget,
        rules=RULES,
    )

    allocated_buy = allocate_ladder_to_budget(
        levels=buy_levels,
        token_budget=budget.buy_token_budget,
        rules=RULES,
    )

    assert len(allocated_sell) == 1
    assert len(allocated_buy) == 1

    assert allocated_sell[0].quantity == Decimal("10")
    assert allocated_buy[0].quantity == Decimal("5")

    total_draw = sum(
        (
            leg.pm_quantity
            for level in (*allocated_sell, *allocated_buy)
            for leg in level.hedge_legs
            if leg.instrument_id == "DRAW_YES"
        ),
        Decimal("0"),
    )

    assert total_draw == Decimal("300")

    # Both sides together must respect shared liquidity.
    assert total_draw <= Decimal("300")


def test_price_level_allocator_prevents_shared_draw_overallocation():
    """
    BUY and SELL must not consume more than their
    allocated share of the same DRAW ask price level.
    """

    sell_levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("4"),
            hedge_legs=(
                HedgeLeg(
                    "WIN", Decimal("0.89"), Decimal("180"),
                    instrument_id="WIN_YES",
                ),
                HedgeLeg(
                    "DRAW", Decimal("0.08"), Decimal("60"),
                    instrument_id="DRAW_YES",
                ),
            ),
        ),
    )

    buy_levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="BUY",
            price=Decimal("1010.80"),
            quantity=Decimal("2"),
            hedge_legs=(
                HedgeLeg(
                    "DRAW", Decimal("0.08"), Decimal("60"),
                    instrument_id="DRAW_YES",
                ),
                HedgeLeg(
                    "LOSS", Decimal("0.04"), Decimal("90"),
                    instrument_id="LOSS_YES",
                ),
            ),
        ),
    )

    # External DRAW liquidity at 0.08 = 100 tokens.
    # Each Antivist side receives exactly 50.
    sell_budget = {
        ("WIN_YES", Decimal("0.89")): Decimal("900"),
        ("DRAW_YES", Decimal("0.08")): Decimal("50"),
    }

    buy_budget = {
        ("DRAW_YES", Decimal("0.08")): Decimal("50"),
        ("LOSS_YES", Decimal("0.04")): Decimal("900"),
    }

    allocated_sell = allocate_ladder_to_price_budget(
        levels=sell_levels,
        price_budget=sell_budget,
        rules=RULES,
    )

    allocated_buy = allocate_ladder_to_price_budget(
        levels=buy_levels,
        price_budget=buy_budget,
        rules=RULES,
    )

    assert len(allocated_sell) == 1
    assert len(allocated_buy) == 1

    # SELL: 50 DRAW / 15 per contract = 3.333...
    assert allocated_sell[0].quantity == Decimal("3.333")

    # BUY: 50 DRAW / 30 per contract = 1.666...
    assert allocated_buy[0].quantity == Decimal("1.666")

    sell_draw = sum(
        (
            leg.pm_quantity
            for level in allocated_sell
            for leg in level.hedge_legs
            if leg.instrument_id == "DRAW_YES"
            and leg.pm_price == Decimal("0.08")
        ),
        Decimal("0"),
    )

    buy_draw = sum(
        (
            leg.pm_quantity
            for level in allocated_buy
            for leg in level.hedge_legs
            if leg.instrument_id == "DRAW_YES"
            and leg.pm_price == Decimal("0.08")
        ),
        Decimal("0"),
    )

    assert sell_draw == Decimal("49.995")
    assert buy_draw == Decimal("49.980")

    # The combined allocation respects the actual price level.
    assert sell_draw + buy_draw <= Decimal("100")


def test_price_budget_respects_shared_liquidity_across_both_sides():
    """
    BUY and SELL allocations must never consume more
    than the observed liquidity at any external price.
    """

    from albridge.market_snapshot import MarketSnapshot, OutcomeBook

    def book(outcome, symbol, asks):
        return OutcomeBook(
            outcome=outcome,
            symbol=symbol,
            bids=(),
            asks=tuple(
                (Decimal(price), Decimal(size))
                for price, size in asks
            ),
            sequence=1,
            state_time_ms=1000,
        )

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": book("WIN", "WIN_YES", [
                ("0.89", "900"),
            ]),
            "DRAW": book("DRAW", "DRAW_YES", [
                ("0.08", "100"),
                ("0.09", "200"),
            ]),
            "LOSS": book("LOSS", "LOSS_YES", [
                ("0.04", "900"),
            ]),
        },
    )

    budget = calculate_price_level_budget(
        snapshot=snapshot,
    )

    sell_levels = (
        sell_level("1011.25", "4"),
        sell_level("1011.70", "8"),
    )

    buy_levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="BUY",
            price=Decimal("1010.80"),
            quantity=Decimal("2"),
            hedge_legs=(
                HedgeLeg(
                    "DRAW", Decimal("0.08"), Decimal("60"),
                    instrument_id="DRAW_YES",
                ),
                HedgeLeg(
                    "LOSS", Decimal("0.04"), Decimal("90"),
                    instrument_id="LOSS_YES",
                ),
            ),
        ),
    )

    allocated_sell = allocate_ladder_to_price_budget(
        levels=sell_levels,
        price_budget=budget.sell,
        rules=RULES,
    )

    allocated_buy = allocate_ladder_to_price_budget(
        levels=buy_levels,
        price_budget=budget.buy,
        rules=RULES,
    )

    # Reconstruct total consumption per instrument and price.
    consumed = {}

    for level in (*allocated_sell, *allocated_buy):
        for leg in level.hedge_legs:
            key = (leg.instrument_id, leg.pm_price)

            consumed[key] = (
                consumed.get(key, Decimal("0"))
                + leg.pm_quantity
            )

    # Check against the actual external ask quantities.
    available = {}

    for outcome in ("WIN", "DRAW", "LOSS"):
        outcome_book = snapshot.get(outcome)

        for price, size in outcome_book.asks:
            key = (outcome_book.symbol, price)

            available[key] = (
                available.get(key, Decimal("0"))
                + size
            )

    for key, quantity in consumed.items():
        assert quantity <= available[key], (
            f"Overallocated {key}: "
            f"consumed={quantity}, available={available[key]}"
        )

    # The shared DRAW ask at 0.08 must respect both halves.
    for levels, side_budget in (
        (allocated_sell, budget.sell),
        (allocated_buy, budget.buy),
    ):
        draw_used = sum(
            (
                leg.pm_quantity
                for level in levels
                for leg in level.hedge_legs
                if leg.instrument_id == "DRAW_YES"
                and leg.pm_price == Decimal("0.08")
            ),
            Decimal("0"),
        )

        assert draw_used <= side_budget[
            ("DRAW_YES", Decimal("0.08"))
        ]
