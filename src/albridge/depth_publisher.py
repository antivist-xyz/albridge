"""Publish market depth for the local terminal viewer."""

import json
import os
import time
from pathlib import Path

from albridge.market_data_health import assess_feed_health


DEPTH_PATH = Path("recordings/live_depth.json")
DISPLAY_DEPTH = 5

def serialize_ladders(venue_ladders):
    """Serialize normalized or risk-allocated hedge ladders."""

    return {
        side: [
            {
                "venue": level.venue,
                "price": str(level.price),
                "quantity": str(level.quantity),
                "hedge_legs": [
                    {
                        "instrument_id": leg.instrument_id,
                        "outcome": leg.outcome,
                        "pm_price": str(leg.pm_price),
                        "pm_quantity": str(leg.pm_quantity),
                    }
                    for leg in level.hedge_legs
                ],
            }
            for level in venue_ladders.get(side, ())
        ]
        for side in ("buy", "sell")
    }

def publish_depth(
    *,
    market_data,
    clients,
    fixture_label,
    quotes_by_venue=None,
    ladders_by_venue=None,
    anchors=None,
    risk_ladders_by_venue=None,
    public_demo=False,
):
    """Publish market depth, hedge pricing and ladders atomically."""

    quotes_by_venue = quotes_by_venue or {}
    ladders_by_venue = ladders_by_venue or {}

    risk_ladders_by_venue = risk_ladders_by_venue or {}

    payload = {
        "fixture": fixture_label,
        "mode": "PUBLIC DEMO" if public_demo else "STANDARD",
        "published_at": time.time(),
        "anchors": (
            {
                "win": str(anchors[0]),
                "draw": str(anchors[1]),
                "loss": str(anchors[2]),
            }
            if anchors is not None
            else None
        ),
        "venues": {},
    }

    for venue in ("pascal", "polymarket"):
        client = clients.get(venue)
        snapshot = market_data.get(venue)

        # Determine venue feed health.
        status = (
            assess_feed_health(
                venue=venue,
                client=client,
            ).status
            if client is not None
            else "UNAVAILABLE"
        )

        # Serialize external order books.
        outcomes = {}

        if snapshot is not None:
            for outcome in ("WIN", "DRAW", "LOSS"):
                book = snapshot.get(outcome)

                outcomes[outcome] = {
                    "bids": [
                        [str(price), str(size)]
                        for price, size in book.bids[:DISPLAY_DEPTH]
                    ],
                    "asks": [
                        [str(price), str(size)]
                        for price, size in book.asks[:DISPLAY_DEPTH]
                    ],
                }

        # Serialize theoretical hedge pricing and capacities.
        hedge_data = quotes_by_venue.get(venue)
        quotes = hedge_data["quotes"] if hedge_data else None
        capacities = hedge_data["capacities"] if hedge_data else {}

        pricing = None

        if quotes is not None:
            ask_quote, bid_quote = quotes

            pricing = {
                "ask": {
                    "fair_entry": str(ask_quote.fair_entry),
                    "hedge_quantities": {
                        outcome: str(quantity)
                        for outcome, quantity
                        in ask_quote.hedge_quantities.items()
                    },
                },
                "bid": {
                    "fair_entry": str(bid_quote.fair_entry),
                    "hedge_quantities": {
                        outcome: str(quantity)
                        for outcome, quantity
                        in bid_quote.hedge_quantities.items()
                    },
                },
            }

            for side in ("ask", "bid"):
                capacity = capacities.get(side)

                pricing[side]["max_quantity"] = (
                    str(capacity.max_antivist_quantity)
                    if capacity is not None
                    else None
                )

                pricing[side]["limiting_outcome"] = (
                    capacity.limiting_outcome
                    if capacity is not None
                    else None
                )

        # Serialize theoretical and risk-allocated ladders independently.
        serialized_ladders = serialize_ladders(
            ladders_by_venue.get(venue, {})
        )

        serialized_risk_ladders = serialize_ladders(
            risk_ladders_by_venue.get(venue, {})
        )

        # Store all data for this venue.
        payload["venues"][venue] = {
            "status": status,
            "outcomes": outcomes,
            "pricing": pricing,
            "ladders": serialized_ladders,
            "risk_ladders": serialized_risk_ladders,
        }

    # Atomically replace the published snapshot.
    DEPTH_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = DEPTH_PATH.with_name(
        f"{DEPTH_PATH.name}.{os.getpid()}.tmp"
    )

    temporary.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    os.replace(temporary, DEPTH_PATH)
