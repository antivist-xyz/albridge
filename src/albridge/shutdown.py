
"""Protective shutdown for Albridge-managed Antivist orders."""

import logging

from albridge.execution import execute_cancellations
from albridge.order_manager import parse_existing_orders


LOGGER = logging.getLogger(__name__)

ACTIVE = frozenset({"OPEN", "PARTIALLY_FILLED"})
PENDING = frozenset({"PENDING_SUBMIT", "PENDING_CANCEL"})


class ShutdownVerificationError(RuntimeError):
    pass


def shutdown_managed_orders(
    *,
    client,
    registry,
    market_id: int,
) -> dict:
    """
    Cancel Albridge-managed resting orders and verify exchange state.

    Uses only public SDK endpoints and the local OrderRegistry.
    Never cancels unregistered orders.
    """

    LOGGER.warning(
        "PROTECTIVE SHUTDOWN START | market=%s",
        market_id,
    )

    response = client.get_orders(market_id=market_id)
    existing = parse_existing_orders(response=response)

    registered = registry.get_by_market(market_id=market_id)

    by_id = {
        order.order_id: order
        for order in registered
        if order.order_id is not None
    }

    targets = []
    unsafe_states = []

    for order in existing:
        registered_order = by_id.get(order.order_id)

        if registered_order is None:
            continue

        if (
            order.client_order_id
            != registered_order.client_order_id
        ):
            unsafe_states.append(
                f"ownership mismatch: {order.order_id}"
            )
            continue

        if registered_order.status not in ACTIVE:
            unsafe_states.append(
                f"registry status mismatch: "
                f"{order.order_id}={registered_order.status}"
            )
            continue

        targets.append(order.order_id)

    LOGGER.warning(
        "PROTECTIVE SHUTDOWN | managed_open=%s",
        len(targets),
    )

    # Cancel in batches supported by the public SDK.
    canceled_count = 0

    for offset in range(0, len(targets), 200):
        batch = tuple(targets[offset:offset + 200])

        canceled = execute_cancellations(
            client=client,
            registry=registry,
            market_id=market_id,
            order_ids=batch,
        )

        canceled_count += len(canceled)

        if len(canceled) != len(batch):
            raise ShutdownVerificationError(
                "Partial cancellation response. "
                "Final exchange state is uncertain."
            )

    # Verify with a new authoritative REST response.
    final_response = client.get_orders(market_id=market_id)
    final_existing = parse_existing_orders(
        response=final_response,
    )
    final_registered = registry.get_by_market(
        market_id=market_id,
    )

    managed_ids = {
        order.client_order_id
        for order in final_registered
    }

    managed_order_ids = {
        order.order_id
        for order in final_registered
        if order.order_id is not None
    }

    remaining = [
        order.order_id
        for order in final_existing
        if (
            order.client_order_id in managed_ids
            or order.order_id in managed_order_ids
        )
    ]

    unresolved = [
        order.client_order_id
        for order in final_registered
        if order.status in PENDING
    ]

    if remaining or unresolved or unsafe_states:
        raise ShutdownVerificationError(
            "PROTECTIVE SHUTDOWN NOT VERIFIED | "
            f"remaining={remaining} "
            f"pending={len(unresolved)} "
            f"inconsistencies={unsafe_states}"
        )

    LOGGER.warning(
        "PROTECTIVE SHUTDOWN VERIFIED | "
        "market=%s canceled=%s remaining=0",
        market_id,
        canceled_count,
    )

    return {
        "canceled": canceled_count,
        "remaining": 0,
        "verified": True,
    }
