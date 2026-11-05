
"""Emergency halt and cancel Albridge-managed orders."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from antivist.client import AntivistClient

from albridge.order_registry import OrderRegistry
from albridge.order_manager import parse_existing_orders
from albridge.execution import execute_cancellations
from albridge.risk.emergency_halt import activate_emergency_halt


ACTIVE = {"OPEN", "PARTIALLY_FILLED"}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python -m scripts.emergency_cancel MARKET_ID"
        )

    market_id = int(sys.argv[1])

    load_dotenv(".env")

    activate_emergency_halt()
    print("EMERGENCY HALT ACTIVE", flush=True)

    client = AntivistClient(
        base_url=os.environ["ANTIVIST_BASE_URL"],
        api_key=os.environ["ANTIVIST_API_KEY"],
        api_secret=os.environ["ANTIVIST_API_SECRET"],
    )

    registry = OrderRegistry("data/albridge.sqlite3")

    response = client.get_orders(market_id=market_id)
    existing = parse_existing_orders(response=response)

    registered = registry.get_by_market(
        market_id=market_id,
    )

    owned = {
        order.order_id: order
        for order in registered
        if order.order_id is not None
        and order.status in ACTIVE
    }

    def managed_open(orders):
        result = []

        for order in orders:
            registered_order = owned.get(order.order_id)

            if registered_order is None:
                continue

            if (
                order.client_order_id
                != registered_order.client_order_id
            ):
                raise RuntimeError(
                    "Ownership mismatch. HALT remains active."
                )

            result.append(order)

        return result

    pending = [
        order.client_order_id
        for order in registered
        if order.status == "PENDING_SUBMIT"
    ]

    if pending:
        print(
            f"WARNING: {len(pending)} unresolved submissions",
            flush=True,
        )

    targets = managed_open(existing)

    print(
        f"MANAGED OPEN ORDERS: {len(targets)}",
        flush=True,
    )

    # SDK batch cancellation supports up to 200 orders.
    ids = [order.order_id for order in targets]

    for offset in range(0, len(ids), 200):
        batch = tuple(ids[offset:offset + 200])

        canceled = execute_cancellations(
            client=client,
            registry=registry,
            market_id=market_id,
            order_ids=batch,
        )

        if len(canceled) != len(batch):
            raise RuntimeError(
                "PARTIAL CANCELLATION — manual verification required"
            )

    # Verify against a fresh exchange response.
    final_response = client.get_orders(market_id=market_id)
    final_orders = parse_existing_orders(response=final_response)

    remaining_ids = {
        order.order_id
        for order in final_orders
        if order.order_id in owned
    }

    if remaining_ids or pending:
        raise RuntimeError(
            "EMERGENCY HALT INCOMPLETE | "
            f"remaining={sorted(remaining_ids)} "
            f"unresolved_submissions={len(pending)}"
        )

    print(
        "EMERGENCY CANCEL VERIFIED | "
        "No registered active orders remain in the checked snapshot",
        flush=True,
    )


if __name__ == "__main__":
    main()
