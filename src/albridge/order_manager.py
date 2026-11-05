"""Build desired Antivist orders from risk-allocated hedge ladders."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.hedge_ladder import HedgeLadderLevel, HedgeLeg
from albridge.order_registry import ManagedOrder

from collections import Counter
from enum import Enum

import uuid


class ReconciliationAction(str, Enum):
    KEEP = "KEEP"
    CANCEL = "CANCEL"
    SUBMIT = "SUBMIT"


@dataclass(frozen=True)
class DesiredOrder:
    venue: str
    side: str
    price: Decimal
    quantity: Decimal
    hedge_legs: tuple[HedgeLeg, ...]


def build_desired_orders(
    *,
    risk_ladders_by_venue: dict[
        str,
        dict[str, tuple[HedgeLadderLevel, ...]],
    ],
) -> tuple[DesiredOrder, ...]:
    """
    Convert normalized risk ladders into desired Antivist orders.

    Each ladder level remains an independent desired order.
    Orders are never aggregated across venues or price levels.
    """

    result = []

    for venue in sorted(risk_ladders_by_venue):
        ladders = risk_ladders_by_venue[venue]

        for side_key, expected_side in (
            ("buy", "BUY"),
            ("sell", "SELL"),
        ):
            for level in ladders.get(side_key, ()):
                if level.venue != venue:
                    raise ValueError("Ladder venue mismatch")

                if level.side != expected_side:
                    raise ValueError("Ladder side mismatch")

                if (
                    not level.price.is_finite()
                    or not level.quantity.is_finite()
                    or level.quantity <= 0
                ):
                    raise ValueError("Invalid desired order")

                result.append(
                    DesiredOrder(
                        venue=level.venue,
                        side=level.side,
                        price=level.price,
                        quantity=level.quantity,
                        hedge_legs=level.hedge_legs,
                    )
                )

    return tuple(result)

@dataclass(frozen=True)
class ExistingOrder:
    order_id: int
    client_order_id: str | None
    market_id: int
    side: str
    price: Decimal
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    status: str


def parse_existing_orders(
    *,
    response: dict,
) -> tuple[ExistingOrder, ...]:
    """
    Parse open orders returned by the public Antivist SDK.

    This function performs no network requests and does not
    modify exchange state.
    """

    raw_orders = response["orders"]

    result = []

    for raw in raw_orders:
        status = raw["status"]

        if status not in ("OPEN", "PARTIALLY_FILLED"):
            raise ValueError(
                f"Unexpected open order status: {status}"
            )

        side = raw["side"]

        if side not in ("BUY", "SELL"):
            raise ValueError("Invalid order side")

        price = Decimal(raw["price"])
        quantity = Decimal(raw["quantity"])
        filled_quantity = Decimal(raw["filled_quantity"])
        remaining_quantity = Decimal(raw["remaining_quantity"])

        if not all(
            value.is_finite()
            for value in (
                price,
                quantity,
                filled_quantity,
                remaining_quantity,
            )
        ):
            raise ValueError("Non-finite order value")

        if (
            price <= 0
            or quantity <= 0
            or filled_quantity < 0
            or remaining_quantity <= 0
        ):
            raise ValueError("Invalid order values")

        if filled_quantity + remaining_quantity != quantity:
            raise ValueError("Inconsistent order quantities")

        order_id = raw["order_id"]
        market_id = raw["market_id"]

        if (
            type(order_id) is not int
            or order_id <= 0
            or type(market_id) is not int
            or market_id <= 0
        ):
            raise ValueError("Invalid order identifier")

        result.append(
            ExistingOrder(
                order_id=order_id,
                client_order_id=raw["client_order_id"],
                market_id=market_id,
                side=side,
                price=price,
                quantity=quantity,
                filled_quantity=filled_quantity,
                remaining_quantity=remaining_quantity,
                status=status,
            )
        )

    return tuple(
        sorted(result, key=lambda order: order.order_id)
    )

def select_managed_orders(
    *,
    existing_orders: tuple[ExistingOrder, ...],
    managed_client_order_ids: frozenset[str],
    market_id: int,
) -> tuple[ExistingOrder, ...]:
    """
    Select orders explicitly registered as managed by Albridge.

    Never infer ownership from price, quantity or side.
    """

    if type(market_id) is not int or market_id <= 0:
        raise ValueError("Invalid market_id")

    result = []

    for order in existing_orders:
        if order.market_id != market_id:
            continue

        if order.client_order_id not in managed_client_order_ids:
            continue

        result.append(order)

    return tuple(
        sorted(result, key=lambda order: order.order_id)
    )

@dataclass(frozen=True)
class ReconciliationDecision:
    action: ReconciliationAction
    existing_order: ExistingOrder | None = None
    desired_order: DesiredOrder | None = None

def generate_client_order_id() -> str:
    """
    Generate a unique Antivist client order identifier.

    The identifier must be persisted before submitting an order.
    Retries of the same request must reuse the original identifier.
    """
    return f"albridge-{uuid.uuid4().hex}"

def match_registered_open_orders(
    *,
    registered_orders: tuple[ManagedOrder, ...],
    existing_orders: tuple[ExistingOrder, ...],
) -> tuple[
    tuple[ManagedOrder, ExistingOrder], ...
]:
    """
    Match registered Albridge orders with Antivist open orders.

    Match strictly by client_order_id and market_id.
    Never infer ownership from price, side or quantity.
    """

    existing_by_client_id = {}

    for existing in existing_orders:
        client_id = existing.client_order_id

        if client_id is None:
            continue

        if client_id in existing_by_client_id:
            raise ValueError("Duplicate Antivist client_order_id")

        existing_by_client_id[client_id] = existing

    matches = []
    seen_registered_ids = set()

    for registered in registered_orders:
        client_id = registered.client_order_id

        if client_id in seen_registered_ids:
            raise ValueError("Duplicate registered client_order_id")

        seen_registered_ids.add(client_id)

        existing = existing_by_client_id.get(client_id)

        if existing is None:
            continue

        if existing.market_id != registered.market_id:
            raise ValueError("Registered order market mismatch")

        if (
            registered.order_id is not None
            and existing.order_id != registered.order_id
        ):
            raise ValueError("Registered order ID mismatch")

        if (
            existing.side != registered.side
            or existing.price != registered.price
            or existing.quantity != registered.quantity
        ):
            raise ValueError("Registered order parameters mismatch")

        matches.append((registered, existing))

    return tuple(matches)


class ReconciliationBlocked(Exception):
    """The order state is not safe to reconcile."""


PENDING_STATES = frozenset({
    "PENDING_SUBMIT",
    "PENDING_CANCEL",
})

ACTIVE_STATES = frozenset({
    "OPEN",
    "PARTIALLY_FILLED",
})

TERMINAL_STATES = frozenset({
    "FILLED",
    "CANCELED",
    "REJECTED",
})


def build_reconciliation_plan(
    *,
    desired_orders: tuple[DesiredOrder, ...],
    registered_orders: tuple[ManagedOrder, ...],
    existing_orders: tuple[ExistingOrder, ...],
    market_id: int,
) -> tuple[ReconciliationDecision, ...]:
    """
    Reconcile desired quotes against registered Antivist orders.

    Matching is one-to-one and independent of ladder slots.
    Never execute exchange operations.
    """

    if type(market_id) is not int or market_id <= 0:
        raise ValueError("Invalid market_id")

    registered_ids = [
        order.client_order_id
        for order in registered_orders
    ]

    if len(registered_ids) != len(set(registered_ids)):
        raise ValueError("Duplicate registered client_order_id")

    for registered in registered_orders:
        if registered.market_id != market_id:
            raise ValueError("Registered order market mismatch")

        if registered.status in PENDING_STATES:
            raise ReconciliationBlocked(
                f"Pending order: {registered.client_order_id}"
            )

        if registered.status not in (
            ACTIVE_STATES | TERMINAL_STATES
        ):
            raise ReconciliationBlocked(
                f"Unknown status: {registered.status}"
            )

    # Verify ownership and exchange identity using the
    # previously validated registry-to-exchange matcher.
    matches = match_registered_open_orders(
        registered_orders=registered_orders,
        existing_orders=existing_orders,
    )

    matched_ids = {
        registered.client_order_id
        for registered, _ in matches
    }

    # Active registered orders missing from the open-order
    # response require recovery before reconciliation.
    for registered in registered_orders:
        if (
            registered.status in ACTIVE_STATES
            and registered.client_order_id not in matched_ids
        ):
            raise ReconciliationBlocked(
                "Active registered order missing from Antivist: "
                f"{registered.client_order_id}"
            )

    # Terminal records must never conflict with live orders.
    for registered, _ in matches:
        if registered.status in TERMINAL_STATES:
            raise ReconciliationBlocked(
                "Terminal registered order is still open: "
                f"{registered.client_order_id}"
            )

    def order_key(order):
        return (
            order.venue,
            order.side,
            order.price,
            order.quantity,
            repr(order.hedge_legs),
        )

    # Keep duplicate levels independent, but process them
    # in a deterministic order.
    remaining_desired = sorted(
        desired_orders,
        key=order_key,
    )

    decisions = []

    for registered, existing in sorted(
        matches,
        key=lambda pair: pair[0].client_order_id,
    ):

        candidates = []

        for index, desired in enumerate(remaining_desired):
            if (
                registered.venue != desired.venue
                or existing.side != desired.side
                or existing.price != desired.price
            ):
                continue

            # Never keep more exposure than the current
            # hedge ladder can support.
            if existing.quantity > desired.quantity:
                continue

            candidates.append(
                (
                    desired.quantity - existing.quantity,
                    index,
                )
            )

        if not candidates:
            decisions.append(
                ReconciliationDecision(
                    action=ReconciliationAction.CANCEL,
                    existing_order=existing,
                )
            )
            continue

        # Prefer the closest available quantity.
        # This keeps matching deterministic and one-to-one.
        _, matched_index = min(candidates)

        desired = remaining_desired.pop(matched_index)

        decisions.append(
            ReconciliationDecision(
                action=ReconciliationAction.KEEP,
                existing_order=existing,
                desired_order=desired,
            )
        )

    # Never release budget from a cancellation that has
    # not yet been confirmed by Antivist.
    if any(
        decision.action == ReconciliationAction.CANCEL
        for decision in decisions
    ):
        return tuple(decisions)

    for desired in remaining_desired:
        decisions.append(
            ReconciliationDecision(
                action=ReconciliationAction.SUBMIT,
                desired_order=desired,
            )
        )

    return tuple(decisions)
