
from decimal import Decimal

from albridge.market_rules import AntivistMarketRules


def test_antivist_market_rules():
    rules = AntivistMarketRules.from_market({
        "tick_size": "0.01000000",
        "quantity_step": "0.00100000",
        "min_quantity": "0.01000000",
    })

    assert rules.normalize_quantity(
        Decimal("62.844444444")
    ) == Decimal("62.844")

    assert rules.normalize_quantity(
        Decimal("0.009999")
    ) == Decimal("0.009")

    assert rules.normalize_price(
        Decimal("1011.256"),
        "SELL",
    ) == Decimal("1011.26")

    assert rules.normalize_price(
        Decimal("1010.756"),
        "BUY",
    ) == Decimal("1010.75")

    assert rules.is_tradable(Decimal("0.010"))
    assert not rules.is_tradable(Decimal("0.009"))
    assert not rules.is_tradable(Decimal("0.0105"))
    assert not rules.is_tradable(Decimal("3.33E-26"))
