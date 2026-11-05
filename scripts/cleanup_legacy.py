"""Cancel confirmed Albridge-owned open orders on one market."""

import argparse
import os

from dotenv import load_dotenv
from antivist.client import AntivistClient

from albridge.execution import execute_cancellations
from albridge.order_manager import (
    match_registered_open_orders,
    parse_existing_orders,
)
from albridge.order_registry import OrderRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument("--expect", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.market_id <= 0 or args.expect <= 0:
        raise ValueError("Invalid market ID or expected count")

    load_dotenv()

    client = AntivistClient(
        base_url=os.environ["ANTIVIST_BASE_URL"],
        api_key=os.environ["ANTIVIST_API_KEY"],
        api_secret=os.environ["ANTIVIST_API_SECRET"],
    )

    registry = OrderRegistry("data/albridge.sqlite3")

    registered = registry.get_by_market(
        market_id=args.market_id
    )

    pending = [
        order.client_order_id
        for order in registered
        if order.status in {"PENDING_SUBMIT", "PENDING_CANCEL"}
    ]

    if pending:
        raise RuntimeError(
            f"Unresolved pending orders: {pending}"
        )

    response = client.get_orders(market_id=args.market_id)
    existing = parse_existing_orders(response=response)

    matches = match_registered_open_orders(
        registered_orders=registered,
        existing_orders=existing,
    )

    active_registered = {
        order.client_order_id
        for order in registered
        if order.status in {"OPEN", "PARTIALLY_FILLED"}
    }

    matched_ids = {
        managed.client_order_id
        for managed, _ in matches
    }

    if active_registered != matched_ids:
        raise RuntimeError(
            "Registry and Antivist active orders disagree"
        )

    if any(
        managed.status != "OPEN"
        or exchange.status != "OPEN"
        or exchange.filled_quantity != 0
        for managed, exchange in matches
    ):
        raise RuntimeError(
            "Migration blocked: unexpected or partially filled order"
        )

    order_ids = tuple(
        exchange.order_id
        for _, exchange in matches
    )

    print(f"\nMARKET: {args.market_id}")
    print(f"REGISTERED: {len(registered)}")
    print(f"ACTIVE MANAGED: {len(order_ids)}")

    for managed, exchange in matches:
        print(
            f"{exchange.order_id} | "
            f"{managed.venue} | "
            f"{exchange.side} | "
            f"{exchange.price} | "
            f"{exchange.remaining_quantity}"
        )

    if len(order_ids) != args.expect:
        raise RuntimeError(
            f"Expected {args.expect} orders, found {len(order_ids)}"
        )

    if not args.execute:
        print("\nPREVIEW ONLY — no cancellations sent")
        return

    print("\nEXECUTING MANAGED ORDER CANCELLATIONS")

    execute_cancellations(
        client=client,
        registry=registry,
        market_id=args.market_id,
        order_ids=order_ids,
    )

    refreshed = parse_existing_orders(
        response=client.get_orders(market_id=args.market_id)
    )

    remaining = {
        order.order_id
        for order in refreshed
        if order.order_id in set(order_ids)
    }

    if remaining:
        raise RuntimeError(
            f"Orders still open on Antivist: {remaining}"
        )

    for managed, _ in matches:
        stored = registry.get(managed.client_order_id)
        if stored is None or stored.status != "CANCELED":
            raise RuntimeError(
                f"Registry not synchronized: {managed.client_order_id}"
            )

    print("\nSUCCESS — managed legacy orders canceled")


if __name__ == "__main__":
    main()
