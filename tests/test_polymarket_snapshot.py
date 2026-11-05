from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot
from albridge.venues.polymarket_order_book import PolymarketOrderBook
from albridge.venues.polymarket_snapshot import (
    build_polymarket_snapshot,
)


TOKENS = {
    outcome: {
        "condition_id": f"condition-{outcome}",
        "yes_token_id": f"{outcome}-yes",
        "no_token_id": f"{outcome}-no",
    }
    for outcome in ("WIN", "DRAW", "LOSS")
}


def make_books():
    books = {}

    for outcome, tokens in TOKENS.items():
        token_id = tokens["yes_token_id"]
        book = PolymarketOrderBook(token_id)

        book.apply_snapshot({
            "event_type": "book",
            "asset_id": token_id,
            "market": tokens["condition_id"],
            "timestamp": "1000",
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.60", "size": "200"}],
        })

        books[token_id] = book

    return books


def test_normalized_snapshot():
    snapshot = build_polymarket_snapshot(
        books=make_books(),
        outcome_tokens=TOKENS,
    )

    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.venue == "polymarket"
    assert set(snapshot.outcomes) == {"WIN", "DRAW", "LOSS"}
    assert snapshot.get("WIN").best_bid == Decimal("0.40")
    assert snapshot.get("LOSS").best_ask == Decimal("0.60")
    assert snapshot.get("WIN").sequence is None


def test_missing_yes_book_returns_none():
    books = make_books()
    del books["DRAW-yes"]

    assert build_polymarket_snapshot(
        books=books,
        outcome_tokens=TOKENS,
    ) is None


def test_wrong_condition_returns_none():
    books = make_books()
    books["WIN-yes"].market = "wrong-condition"

    assert build_polymarket_snapshot(
        books=books,
        outcome_tokens=TOKENS,
    ) is None


def test_snapshot_remains_immutable():
    books = make_books()

    snapshot = build_polymarket_snapshot(
        books=books,
        outcome_tokens=TOKENS,
    )

    books["WIN-yes"].apply_change({
        "asset_id": "WIN-yes",
        "side": "BUY",
        "price": "0.40",
        "size": "500",
    }, 1100)

    assert snapshot.get("WIN").bids[0][1] == Decimal("100")
