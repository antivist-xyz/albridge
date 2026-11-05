
from decimal import Decimal

from albridge.hedge_ladder import HedgeLadderLevel
from albridge.ladder_normalizer import normalize_hedge_ladder
from albridge.market_rules import AntivistMarketRules
from albridge.hedge_ladder import HedgeLeg


RULES = AntivistMarketRules(
    tick_size=Decimal("0.01"),
    quantity_step=Decimal("0.001"),
    min_quantity=Decimal("0.01"),
)


def segment(side, price, quantity):
    return HedgeLadderLevel(
        venue="polymarket",
        side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        hedge_legs=(),
    )


def test_aggregates_small_segments():
    levels = (
        segment("SELL", "1011.25", "0.006"),
        segment("SELL", "1011.70", "0.009"),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    assert len(result) == 1
    assert result[0].quantity == Decimal("0.015")
    assert result[0].price == Decimal("1011.52")


def test_normalizes_price_and_quantity():
    result = normalize_hedge_ladder(
        levels=(
            segment("BUY", "1010.756", "1.23456"),
        ),
        rules=RULES,
    )

    assert len(result) == 1
    assert result[0].quantity == Decimal("1.234")
    assert result[0].price == Decimal("1010.75")


def test_ignores_numerical_dust():
    result = normalize_hedge_ladder(
        levels=(
            segment("SELL", "1011.25", "3.33E-26"),
            segment("SELL", "1011.70", "0.020"),
        ),
        rules=RULES,
    )

    assert len(result) == 1
    assert result[0].quantity == Decimal("0.020")
    assert result[0].price == Decimal("1011.70")


def test_does_not_publish_below_minimum():
    result = normalize_hedge_ladder(
        levels=(
            segment("SELL", "1011.25", "0.004"),
            segment("SELL", "1011.70", "0.005"),
        ),
        rules=RULES,
    )

    assert result == ()


def test_aggregation_preserves_hedge_quantities():
    levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.006"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.89"), Decimal("0.270")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.090")),
            ),
        ),
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.70"),
            quantity=Decimal("0.009"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.90"), Decimal("0.405")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.135")),
            ),
        ),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    assert len(result) == 1

    quote = result[0]

    assert quote.quantity == Decimal("0.015")
    assert quote.price == Decimal("1011.52")

    win_quantity = sum(
        (leg.pm_quantity for leg in quote.hedge_legs
         if leg.outcome == "WIN"),
        Decimal("0"),
    )

    draw_quantity = sum(
        (leg.pm_quantity for leg in quote.hedge_legs
         if leg.outcome == "DRAW"),
        Decimal("0"),
    )

    # Required hedge ratios per Antivist contract.
    assert win_quantity == quote.quantity * Decimal("45")
    assert draw_quantity == quote.quantity * Decimal("15")

    # Ensure hedge execution cost is covered by the quote.
    hedge_cost = sum(
        (leg.pm_quantity * leg.pm_price
         for leg in quote.hedge_legs),
        Decimal("0"),
    )

    assert (
        quote.price * quote.quantity
        >= Decimal("970") * quote.quantity + hedge_cost
    )

def test_partial_consumption_preserves_hedge_quantities():
    """
    Verify that partially consuming a hedge segment
    does not duplicate or overallocate PM liquidity.
    """

    levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.0105"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.89"), Decimal("0.4725")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.1575")),
            ),
        ),
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.70"),
            quantity=Decimal("0.0105"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.90"), Decimal("0.4725")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.1575")),
            ),
        ),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    assert len(result) == 2

    assert [level.quantity for level in result] == [
        Decimal("0.010"),
        Decimal("0.011"),
    ]

    total_antivist = sum(
        (level.quantity for level in result),
        Decimal("0"),
    )

    assert total_antivist == Decimal("0.021")

    total_win = sum(
        (
            leg.pm_quantity
            for level in result
            for leg in level.hedge_legs
            if leg.outcome == "WIN"
        ),
        Decimal("0"),
    )

    total_draw = sum(
        (
            leg.pm_quantity
            for level in result
            for leg in level.hedge_legs
            if leg.outcome == "DRAW"
        ),
        Decimal("0"),
    )

    assert total_win == total_antivist * Decimal("45")
    assert total_draw == total_antivist * Decimal("15")

    # Check coverage independently for every published level.
    for level in result:
        win = sum(
            (
                leg.pm_quantity
                for leg in level.hedge_legs
                if leg.outcome == "WIN"
            ),
            Decimal("0"),
        )

        draw = sum(
            (
                leg.pm_quantity
                for leg in level.hedge_legs
                if leg.outcome == "DRAW"
            ),
            Decimal("0"),
        )

        assert win == level.quantity * Decimal("45")
        assert draw == level.quantity * Decimal("15")

def test_repeating_decimal_preserves_hedge_ratios():
    """
    Verify hedge ratios after partial consumption
    involving non-terminating Decimal divisions.
    """

    levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.0107"),
            hedge_legs=(
                HedgeLeg(
                    "WIN",
                    Decimal("0.89"),
                    Decimal("0.4815"),
                ),
                HedgeLeg(
                    "DRAW",
                    Decimal("0.08"),
                    Decimal("0.1605"),
                ),
            ),
        ),
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.70"),
            quantity=Decimal("0.0103"),
            hedge_legs=(
                HedgeLeg(
                    "WIN",
                    Decimal("0.90"),
                    Decimal("0.4635"),
                ),
                HedgeLeg(
                    "DRAW",
                    Decimal("0.08"),
                    Decimal("0.1545"),
                ),
            ),
        ),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    assert len(result) == 2

    assert [level.quantity for level in result] == [
        Decimal("0.010"),
        Decimal("0.011"),
    ]

    for level in result:
        win = sum(
            (
                leg.pm_quantity
                for leg in level.hedge_legs
                if leg.outcome == "WIN"
            ),
            Decimal("0"),
        )

        draw = sum(
            (
                leg.pm_quantity
                for leg in level.hedge_legs
                if leg.outcome == "DRAW"
            ),
            Decimal("0"),
        )

        assert win == level.quantity * Decimal("45")
        assert draw == level.quantity * Decimal("15")

def test_normalizer_never_overallocates_hedge_liquidity():
    """
    Ensure normalized orders never consume more
    hedge liquidity than the original segments provide.
    """

    levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.0107"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.89"), Decimal("0.4815")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.1605")),
            ),
        ),
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.70"),
            quantity=Decimal("0.0103"),
            hedge_legs=(
                HedgeLeg("WIN", Decimal("0.90"), Decimal("0.4635")),
                HedgeLeg("DRAW", Decimal("0.08"), Decimal("0.1545")),
            ),
        ),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    def aggregate_legs(ladder):
        totals = {}

        for level in ladder:
            for leg in level.hedge_legs:
                key = (level.venue, leg.outcome, leg.pm_price)

                totals[key] = (
                    totals.get(key, Decimal("0"))
                    + leg.pm_quantity
                )

        return totals

    available = aggregate_legs(levels)
    allocated = aggregate_legs(result)

    for key, quantity in allocated.items():
        assert quantity <= available[key], (
            f"Overallocated hedge liquidity: {key}, "
            f"allocated={quantity}, available={available[key]}"
        )

def test_normalizer_preserves_instrument_identity():
    """
    Different instruments at the same price must
    remain separate after aggregation.
    """

    levels = (
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.006"),
            hedge_legs=(
                HedgeLeg(
                    outcome="WIN",
                    pm_price=Decimal("0.89"),
                    pm_quantity=Decimal("0.270"),
                    instrument_id="win-token-a",
                ),
                HedgeLeg(
                    outcome="DRAW",
                    pm_price=Decimal("0.08"),
                    pm_quantity=Decimal("0.090"),
                    instrument_id="draw-token",
                ),
            ),
        ),
        HedgeLadderLevel(
            venue="polymarket",
            side="SELL",
            price=Decimal("1011.25"),
            quantity=Decimal("0.009"),
            hedge_legs=(
                HedgeLeg(
                    outcome="WIN",
                    pm_price=Decimal("0.89"),
                    pm_quantity=Decimal("0.405"),
                    instrument_id="win-token-b",
                ),
                HedgeLeg(
                    outcome="DRAW",
                    pm_price=Decimal("0.08"),
                    pm_quantity=Decimal("0.135"),
                    instrument_id="draw-token",
                ),
            ),
        ),
    )

    result = normalize_hedge_ladder(
        levels=levels,
        rules=RULES,
    )

    assert len(result) == 1

    quote = result[0]

    assert quote.quantity == Decimal("0.015")

    legs_by_instrument = {
        leg.instrument_id: leg
        for leg in quote.hedge_legs
    }

    assert len(legs_by_instrument) == 3

    assert (
        legs_by_instrument["win-token-a"].pm_quantity
        == Decimal("0.270")
    )

    assert (
        legs_by_instrument["win-token-b"].pm_quantity
        == Decimal("0.405")
    )

    assert (
        legs_by_instrument["draw-token"].pm_quantity
        == Decimal("0.225")
    )