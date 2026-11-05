"""Tests for pure Antivist translation."""

from decimal import Decimal

import pytest

from albridge.translation import (
    calculate_binary_entry,
    calculate_synthetic_no_anchor,
    translate_yes_price,
)


D = Decimal

ANCHORS = {
    "WIN": D("1015"),
    "DRAW": D("985"),
    "LOSS": D("970"),
}

PRICES = {
    "WIN": D("0.60"),
    "DRAW": D("0.25"),
    "LOSS": D("0.15"),
}


def test_binary_entry():
    result = calculate_binary_entry(
        probability=D("0.5"),
        start_anchor=D("970"),
        target_anchor=D("1015"),
    )

    assert result == D("992.5")


def test_win_synthetic_anchor():
    result = calculate_synthetic_no_anchor(
        outcome="WIN",
        anchors=ANCHORS,
        representative_prices=PRICES,
    )

    assert result == D("979.375")


def test_translate_win():
    result = translate_yes_price(
        outcome="WIN",
        price=D("0.60"),
        anchors=ANCHORS,
        representative_prices=PRICES,
    )

    assert result.entry_price == D("1000.7500")


def test_translate_draw():
    result = translate_yes_price(
        outcome="DRAW",
        price=D("0.25"),
        anchors=ANCHORS,
        representative_prices=PRICES,
    )

    assert result.entry_price == D("1000.75")


def test_translate_loss():
    result = translate_yes_price(
        outcome="LOSS",
        price=D("0.15"),
        anchors=ANCHORS,
        representative_prices=PRICES,
    )

    assert result.entry_price == D("1000.750000000000000000000000")


def test_missing_representative_price():
    with pytest.raises(ValueError):
        translate_yes_price(
            outcome="WIN",
            price=D("0.60"),
            anchors=ANCHORS,
            representative_prices={"DRAW": D("0.25")},
        )


def test_invalid_probability():
    with pytest.raises(ValueError):
        translate_yes_price(
            outcome="WIN",
            price=D("1.1"),
            anchors=ANCHORS,
            representative_prices=PRICES,
        )
