
"""Derive external hedge obligations from Antivist fills."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.order_registry import ManagedOrder
from albridge.risk.fill_registry import AntivistFill


@dataclass(frozen=True)
class HedgeObligation:
    execution_id: int
    client_order_id: str
    venue: str
    instrument_id: str | None
    outcome: str
    execution_side: str
    pm_price: Decimal
    quantity: Decimal


def build_hedge_obligations(
    *,
    fill: AntivistFill,
    managed_order: ManagedOrder,
) -> tuple[HedgeObligation, ...]:
    """
    Translate one Antivist fill into external hedge obligations.

    No orders are submitted.
    No persistent state is modified.
    """

    if (
        fill.client_order_id != managed_order.client_order_id
        or fill.market_id != managed_order.market_id
        or fill.side != managed_order.side
        or (
            managed_order.order_id is not None
            and fill.order_id != managed_order.order_id
        )
    ):
        raise ValueError("Fill and managed order mismatch")

    if (
        not managed_order.quantity.is_finite()
        or managed_order.quantity <= 0
        or not fill.quantity.is_finite()
        or fill.quantity <= 0
        or fill.quantity > managed_order.quantity
    ):
        raise ValueError("Invalid fill quantity")

    fraction = fill.quantity / managed_order.quantity

    obligations = []

    for leg in managed_order.hedge_legs:
        # Multiply before dividing to minimize Decimal rounding.
        quantity = (
            leg.pm_quantity * fill.quantity
            / managed_order.quantity
        )

        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("Invalid hedge quantity")

        obligations.append(
            HedgeObligation(
                execution_id=fill.execution_id,
                client_order_id=fill.client_order_id,
                venue=managed_order.venue,
                instrument_id=leg.instrument_id,
                outcome=leg.outcome,
                execution_side=leg.execution_side,
                pm_price=leg.pm_price,
                quantity=quantity,
            )
        )

    return tuple(obligations)