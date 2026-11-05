from decimal import Decimal

from albridge.market_snapshot import build_pascal_snapshot
from albridge.venues.order_book import PascalOrderBook


SYMBOLS = {
    "WIN": "BAR",
    "DRAW": "DRAW",
    "LOSS": "ALA",
}


def create_book(symbol, bid, ask):
    book = PascalOrderBook(symbol)

    book.apply({
        "channel": "book",
        "symbol": symbol,
        "type": "snapshot",
        "seq": "100",
        "state_time_ms": "1000",
        "server_time_ms": "1001",
        "data": {
            "bids": [[str(bid), "100"]],
            "asks": [[str(ask), "200"]],
        },
    })

    return book


def test_complete_snapshot():
    books = {
        "BAR": create_book("BAR", "0.888", "0.902"),
        "DRAW": create_book("DRAW", "0.068", "0.082"),
        "ALA": create_book("ALA", "0.033", "0.039"),
    }

    snapshot = build_pascal_snapshot(
        books=books,
        symbols=SYMBOLS,
    )

    assert snapshot is not None
    assert snapshot.venue == "pascal"
    assert snapshot.get("WIN").best_bid == Decimal("0.888")
    assert snapshot.get("DRAW").best_ask == Decimal("0.082")
    assert snapshot.get("LOSS").asks[0][1] == Decimal("200")


def test_missing_outcome_skips():
    books = {
        "BAR": create_book("BAR", "0.888", "0.902"),
        "DRAW": create_book("DRAW", "0.068", "0.082"),
    }

    assert build_pascal_snapshot(
        books=books,
        symbols=SYMBOLS,
    ) is None


def test_pascal_snapshot_accepts_one_sided_book():
    from albridge.market_snapshot import build_pascal_snapshot
    from albridge.venues.order_book import PascalOrderBook

    symbols = {
        "WIN": "WIN",
        "DRAW": "DRAW",
        "LOSS": "LOSS",
    }

    books = {
        symbol: PascalOrderBook(symbol)
        for symbol in symbols.values()
    }

    # Feed the same native Pascal snapshot format used
    # in the existing PascalOrderBook tests before asserting.
    # The LOSS snapshot must have bids=[] and nonempty asks.
