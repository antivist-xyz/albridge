"""Normalize Polymarket YES order books into common market snapshots."""

from albridge.market_snapshot import MarketSnapshot, OutcomeBook
from albridge.venues.polymarket_order_book import PolymarketOrderBook


def build_polymarket_snapshot(
    *,
    books: dict[str, PolymarketOrderBook],
    outcome_tokens: dict[str, dict[str, str]],
) -> MarketSnapshot | None:
    """Build WIN/DRAW/LOSS from three independent YES books."""

    if set(outcome_tokens) != {"WIN", "DRAW", "LOSS"}:
        raise ValueError("Expected WIN, DRAW and LOSS")

    outcomes = {}
    seen_tokens = set()

    for outcome in ("WIN", "DRAW", "LOSS"):
        tokens = outcome_tokens[outcome]
        token_id = tokens["yes_token_id"]
        condition_id = tokens["condition_id"]

        if token_id in seen_tokens:
            raise ValueError("Duplicate token mapping")

        seen_tokens.add(token_id)

        book = books.get(token_id)

        if book is None or not book.valid_for_quoting:
            return None

        if book.market != condition_id:
            return None

        if book.timestamp_ms is None:
            return None

        outcomes[outcome] = OutcomeBook(
            outcome=outcome,
            symbol=token_id,
            bids=tuple(book.levels("BUY")),
            asks=tuple(book.levels("SELL")),
            sequence=None,
            state_time_ms=book.timestamp_ms,
        )

    return MarketSnapshot(
        venue="polymarket",
        outcomes=outcomes,
    )
