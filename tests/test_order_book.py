from decimal import Decimal

from albridge.venues.order_book import PascalOrderBook


SYMBOL = "LALIGA_26OCT31_BARALA_ML.BAR"


def message(event_type, seq, bids=None, asks=None):
    return {
        "channel": "book",
        "symbol": SYMBOL,
        "type": event_type,
        "seq": str(seq),
        "state_time_ms": "1793266857246",
        "server_time_ms": "1793266857264",
        "data": {
            "bids": bids or [],
            "asks": asks or [],
        },
    }


def test_snapshot_initializes_book():
    book = PascalOrderBook(SYMBOL)

    assert book.apply(message(
        "snapshot",
        100,
        bids=[["0.888", "10503"], ["0.875", "10876"]],
        asks=[["0.902", "13928"], ["0.914", "26077"]],
    ))

    assert book.best_bid == Decimal("0.888")
    assert book.best_ask == Decimal("0.902")
    assert book.spread == Decimal("0.014")
    assert book.valid_for_quoting


def test_update_changes_absolute_quantity():
    book = PascalOrderBook(SYMBOL)
    book.apply(message("snapshot", 100, bids=[["0.888", "100"]]))

    book.apply(message("update", 110, bids=[["0.888", "200"]]))

    assert book.bids[Decimal("0.888")] == Decimal("200")


def test_zero_quantity_removes_level():
    book = PascalOrderBook(SYMBOL)
    book.apply(message("snapshot", 100, bids=[["0.888", "100"]]))

    book.apply(message("update", 110, bids=[["0.888", "0"]]))

    assert not book.bids


def test_old_update_is_ignored():
    book = PascalOrderBook(SYMBOL)
    book.apply(message("snapshot", 100, bids=[["0.888", "100"]]))

    assert not book.apply(message(
        "update", 99, bids=[["0.900", "200"]]
    ))

    assert book.best_bid == Decimal("0.888")


def test_update_before_snapshot_is_ignored():
    book = PascalOrderBook(SYMBOL)

    assert not book.apply(message(
        "update", 101, bids=[["0.888", "100"]]
    ))

    assert not book.initialized


def test_global_sequence_can_skip_numbers():
    book = PascalOrderBook(SYMBOL)
    book.apply(message("snapshot", 100))

    assert book.apply(message(
        "update", 500, bids=[["0.888", "100"]]
    ))
    assert book.sequence == 500


def test_reset_invalidates_book():
    book = PascalOrderBook(SYMBOL)
    book.apply(message("snapshot", 100, bids=[["0.888", "100"]]))

    book.reset()

    assert not book.initialized
    assert not book.valid_for_quoting
