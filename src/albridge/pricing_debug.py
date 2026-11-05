
"""Standalone terminal for monitoring Antivist hedge pricing changes."""

import json
import time

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEPTH_PATH = Path("recordings/live_depth.json")

POLL_INTERVAL = 0.25
MAX_SNAPSHOT_AGE = 2.0

VENUES = ("pascal", "polymarket")

# Display precision only. Does not affect exchange pricing.
PRICE_PRECISION = Decimal("0.01")
FAIR_PRECISION = Decimal("0.00001")


def read_snapshot():
    """Read the latest published market snapshot."""

    try:
        return json.loads(
            DEPTH_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None


def format_timestamp(timestamp):
    """Format snapshot timestamp with millisecond precision."""

    return datetime.fromtimestamp(
        timestamp
    ).strftime("%H:%M:%S.%f")[:-3]


def calculate_pre_fee_entry(*, side, anchors, outcomes, quote):
    """
    Reconstruct the pre-fee FairEntry from published market data.

    SELL uses WIN + DRAW YES contracts.
    BUY uses DRAW + LOSS YES contracts.

    This function is diagnostic only.
    """

    required_outcomes = (
        ("WIN", "DRAW")
        if side == "SELL"
        else ("DRAW", "LOSS")
    )

    quantities = quote["hedge_quantities"]

    prices = {}

    for outcome in required_outcomes:
        book = outcomes.get(outcome, {})
        asks = book.get("asks") or []

        if not asks:
            return None

        prices[outcome] = Decimal(str(asks[0][0]))

    raw_hedge_cost = sum(
        (
            Decimal(quantities[outcome]) * prices[outcome]
            for outcome in required_outcomes
        ),
        Decimal("0"),
    )

    if side == "SELL":
        pre_fee = (
            Decimal(anchors["loss"]) + raw_hedge_cost
        )
    else:
        pre_fee = (
            Decimal(anchors["win"]) - raw_hedge_cost
        )

    return pre_fee, prices


def build_fair_trace(
    *,
    timestamp,
    venue,
    side,
    anchors,
    outcomes,
    quote,
):
    """
    Build a FairEntry trace and its comparison signature.

    The signature contains only pre-fee and final FairEntry.
    """

    result = calculate_pre_fee_entry(
        side=side,
        anchors=anchors,
        outcomes=outcomes,
        quote=quote,
    )

    if result is None:
        return None

    pre_fee, prices = result

    fair_entry = Decimal(quote["fair_entry"])

    required_outcomes = (
        ("WIN", "DRAW")
        if side == "SELL"
        else ("DRAW", "LOSS")
    )

    quantities = quote["hedge_quantities"]

    anchor_label = (
        f"W={Decimal(anchors['win']):.2f} "
        f"D={Decimal(anchors['draw']):.2f} "
        f"L={Decimal(anchors['loss']):.2f}"
    )

    price_label = " ".join(
        f"{outcome[0]}Y_ask={prices[outcome]:.4f}"
        for outcome in required_outcomes
    )

    hedge_label = " ".join(
        f"H{outcome[0]}={Decimal(quantities[outcome]):.2f}"
        for outcome in required_outcomes
    )

    # Compare only the values displayed in the FairEntry fields.
    signature = (
        f"{pre_fee:.5f}",
        f"{fair_entry:.5f}",
    )

    line = (
        f"{format_timestamp(timestamp)} | "
        f"{venue.upper():10} | "
        f"{side:4} | "
        f"{anchor_label} | "
        f"{price_label} | "
        f"{hedge_label} | "
        f"FAIR_PRE_FEE={pre_fee:.5f} | "
        f"FAIR={fair_entry:.5f}"
    )

    return signature, line


def build_ladder_trace(
    *,
    timestamp,
    venue,
    side,
    index,
    level,
    anchors,
):
    """Show pre-fee and final ladder prices only when prices change."""

    final_price = Decimal(level["price"])
    quantity = Decimal(level["quantity"])

    if quantity <= 0 or anchors is None:
        return None

    legs_data = level.get("hedge_legs", [])

    raw_hedge_cost = sum(
        (
            Decimal(leg["pm_quantity"])
            * Decimal(leg["pm_price"])
            for leg in legs_data
        ),
        Decimal("0"),
    ) / quantity

    if side == "SELL":
        fair_pre_fee = Decimal(anchors["loss"]) + raw_hedge_cost
    else:
        fair_pre_fee = Decimal(anchors["win"]) - raw_hedge_cost

    # Compare only the two displayed prices.
    signature = (
        f"{fair_pre_fee:.2f}",
        f"{final_price:.2f}",
    )

    legs = " + ".join(
        f"{leg['outcome']}"
        f"[{str(leg.get('instrument_id'))[:8]}] "
        f"{Decimal(leg['pm_price']):.4f}"
        f"×{Decimal(leg['pm_quantity']):.3f}"
        for leg in legs_data
    )

    line = (
        f"{format_timestamp(timestamp)} | "
        f"{venue.upper():10} | "
        f"{side:4} | "
        f"L{index} | "
        f"{legs} | "
        f"QTY={quantity:.3f} | "
        f"FAIR_PRE_FEE={fair_pre_fee:.2f} | "
        f"FINAL_PRICE={final_price:.2f}"
    )

    return signature, line

def main():
    """Monitor FairEntry and hedge ladder price changes."""

    print(
        "ALBRIDGE PRICING DEBUG | "
        "Monitoring price changes only",
        flush=True,
    )

    last_timestamp = None

    # Independent cache for FairEntry and ladder levels.
    last_traces = {}

    try:
        while True:
            data = read_snapshot()

            if data is None:
                time.sleep(POLL_INTERVAL)
                continue

            timestamp = data.get("published_at")

            if timestamp is None or timestamp == last_timestamp:
                time.sleep(POLL_INTERVAL)
                continue

            last_timestamp = timestamp

            # Ignore old market snapshots.
            if time.time() - timestamp > MAX_SNAPSHOT_AGE:
                time.sleep(POLL_INTERVAL)
                continue

            anchors = data.get("anchors")

            for venue in VENUES:
                payload = data.get(
                    "venues", {}
                ).get(venue, {})

                if payload.get("status") != "READY":
                    # Clear state so recovered venues print fresh values.
                    last_traces = {
                        key: value
                        for key, value in last_traces.items()
                        if key[0] != venue
                    }
                    continue

                pricing = payload.get("pricing") or {}
                outcomes = payload.get("outcomes") or {}
                ladders = payload.get("ladders") or {}

                for side, ladder_key, pricing_key in (
                    ("SELL", "sell", "ask"),
                    ("BUY", "buy", "bid"),
                ):


                    # -----------------------------------------
                    # Hedge ladder monitoring
                    # -----------------------------------------

                    levels = ladders.get(ladder_key) or []

                    current_keys = set()

                    for index, level in enumerate(levels, start=1):
                        trace_key = (venue, side, index)

                        try:
                            result = build_ladder_trace(
                                timestamp=timestamp,
                                venue=venue,
                                side=side,
                                index=index,
                                level=level,
                                anchors=anchors,
                            )

                            if result is None:
                                continue

                            signature, line = result
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            InvalidOperation,
                            ArithmeticError,
                        ):
                            continue

                        current_keys.add(trace_key)

                        if last_traces.get(trace_key) != signature:
                            print(line, flush=True)
                            last_traces[trace_key] = signature

                    # Remove only disappeared ladder levels.
                    # Never remove the FairEntry cache here.
                    for key in list(last_traces):
                        if (
                            key[0] == venue
                            and key[1] == side
                            and isinstance(key[2], int)
                            and key not in current_keys
                        ):
                            del last_traces[key]

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print(
            "\nALBRIDGE PRICING DEBUG STOPPED",
            flush=True,
        )


if __name__ == "__main__":
    main()
