"""Tests for Polymarket order-book reconstruction."""

from decimal import Decimal

from albridge.venues.polymarket_order_book import (
    PolymarketOrderBook,
)


def make_snapshot(timestamp="1000"):
    return {
        "event_type": "book",
        "asset_id": "token-yes",
        "market": "condition-1",
        "timestamp": timestamp,
        "hash": "hash-1",
        "bids": [
            {"price": "0.01", "size": "100"},
            {"price": "0.07", "size": "200"},
            {"price": "0.06", "size": "300"},
        ],
        "asks": [
            {"price": "0.99", "size": "400"},
            {"price": "0.08", "size": "500"},
        ],
    }


def make_change(
    price,
    size,
    side="BUY",
    asset_id="token-yes",
):
    return {
        "asset_id": asset_id,
        "price": price,
        "size": size,
        "side": side,
    }


def test_snapshot_initializes_book():
    book = PolymarketOrderBook("token-yes")

    assert book.apply_snapshot(make_snapshot())

    assert book.best_bid == Decimal("0.07")
    assert book.best_ask == Decimal("0.08")
    assert book.spread == Decimal("0.01")
    assert book.valid_for_quoting


def test_levels_are_sorted_best_first():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    assert book.levels("BUY")[0][0] == Decimal("0.07")
    assert book.levels("SELL")[0][0] == Decimal("0.08")


def test_change_updates_absolute_quantity():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    applied = book.apply_change(
        make_change("0.07", "596.14"),
        1100,
    )

    assert applied
    assert book.bids[Decimal("0.07")] == Decimal("596.14")


def test_zero_size_removes_level():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    book.apply_change(
        make_change("0.07", "0"),
        1100,
    )

    assert book.best_bid == Decimal("0.06")


def test_sell_change_updates_asks():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    book.apply_change(
        make_change("0.08", "600", side="SELL"),
        1100,
    )

    assert book.asks[Decimal("0.08")] == Decimal("600")


def test_change_before_snapshot_is_ignored():
    book = PolymarketOrderBook("token-yes")

    assert not book.apply_change(
        make_change("0.07", "100"),
        1100,
    )


def test_other_token_is_ignored():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    assert not book.apply_change(
        make_change(
            "0.07",
            "900",
            asset_id="token-no",
        ),
        1100,
    )


def test_older_change_is_ignored():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot("2000"))

    assert not book.apply_change(
        make_change("0.07", "900"),
        1000,
    )


def test_reset_invalidates_book():
    book = PolymarketOrderBook("token-yes")
    book.apply_snapshot(make_snapshot())

    book.reset()

    assert not book.initialized
    assert not book.valid_for_quoting
    assert book.bids == {}
    assert book.asks == {}
