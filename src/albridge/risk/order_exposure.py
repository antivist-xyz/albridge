
"""Calculate hedge exposure for individual Antivist orders."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.order_manager import ExistingOrder
from albridge.order_registry import ManagedOrder


ZERO = Decimal("0")


@dataclass(frozen=True)
class HedgeLegExposure:
    venue: str
    outcome: str
    instrument_id: str | None
    execution_side: str
    pm_price: Decimal
    hedge_ratio: Decimal
    reserved_quantity: Decimal
    gross_hedge_due: Decimal


def calculate_order_exposure(
    *,
    registered: ManagedOrder,
    existing: ExistingOrder,
) -> tuple[HedgeLegExposure, ...]:
    """
    Calculate hedge requirements for an open Antivist order.

    Reserved quantity covers the remaining open order.
    Gross hedge due covers fills, before deducting actual
    external hedge executions.

    This function does not reserve or execute anything.
    """

    if (
        registered.client_order_id != existing.client_order_id
        or registered.market_id != existing.market_id
        or (
            registered.order_id is not None
            and registered.order_id != existing.order_id
        )
    ):
        raise ValueError("Order identity mismatch")

    if (
        registered.side != existing.side
        or registered.price != existing.price
        or registered.quantity != existing.quantity
    ):
        raise ValueError("Order parameters mismatch")

    if existing.status not in ("OPEN", "PARTIALLY_FILLED"):
        raise ValueError("Expected active order")

    if (
        existing.quantity <= ZERO
        or existing.remaining_quantity <= ZERO
        or existing.filled_quantity < ZERO
        or (
            existing.remaining_quantity
            + existing.filled_quantity
            != existing.quantity
        )
    ):
        raise ValueError("Invalid order quantities")

    result = []

    for leg in registered.hedge_legs:
        if (
            not leg.pm_quantity.is_finite()
            or not leg.pm_price.is_finite()
            or leg.pm_quantity <= ZERO
            or leg.pm_price < ZERO
            or leg.pm_price > Decimal("1")
        ):
            raise ValueError("Invalid hedge leg")

        ratio = leg.pm_quantity / registered.quantity

        result.append(
            HedgeLegExposure(
                venue=registered.venue,
                outcome=leg.outcome,
                instrument_id=leg.instrument_id,
                execution_side=leg.execution_side,
                pm_price=leg.pm_price,
                hedge_ratio=ratio,
                reserved_quantity=(
                    leg.pm_quantity
                    * existing.remaining_quantity
                    / registered.quantity
                ),
                gross_hedge_due=(
                    leg.pm_quantity
                    * existing.filled_quantity
                    / registered.quantity
                ),
            )
        )

    return tuple(result)
