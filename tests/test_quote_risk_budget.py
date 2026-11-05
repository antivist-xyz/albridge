from decimal import Decimal

from albridge.risk.quote_risk_budget import (
    calculate_quote_risk_budget,
)


def test_shared_draw_liquidity_is_split_50_50():
    """
    Shared DRAW liquidity must not be allocated
    twice across Antivist BUY and SELL quotes.
    """

    available = {
        "WIN_YES": Decimal("900"),
        "DRAW_YES": Decimal("300"),
        "LOSS_YES": Decimal("900"),
    }

    budget = calculate_quote_risk_budget(
        available=available,
        sell_ratios={
            "WIN_YES": Decimal("45"),
            "DRAW_YES": Decimal("15"),
        },
        buy_ratios={
            "DRAW_YES": Decimal("30"),
            "LOSS_YES": Decimal("45"),
        },
    )

    assert budget.sell_max_quantity == Decimal("10")
    assert budget.buy_max_quantity == Decimal("5")

    assert budget.sell_token_budget == {
        "WIN_YES": Decimal("900"),
        "DRAW_YES": Decimal("150"),
    }

    assert budget.buy_token_budget == {
        "DRAW_YES": Decimal("150"),
        "LOSS_YES": Decimal("900"),
    }

    # Even if both sides fill completely,
    # total DRAW consumption cannot exceed availability.
    total_draw_required = (
        budget.sell_max_quantity * Decimal("15")
        + budget.buy_max_quantity * Decimal("30")
    )

    assert total_draw_required <= available["DRAW_YES"]