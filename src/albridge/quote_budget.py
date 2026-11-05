"""Capital-constrained sizing for hedgeable Antivist quotes."""

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN

from albridge.hedge_ladder import HedgeLeg
from albridge.order_manager import DesiredOrder
from albridge.risk.limits import get_risk_limits


def allocate_quote_budget(
    *,
    orders: tuple[DesiredOrder, ...],
    quantity_step: Decimal,
    min_quantity: Decimal,
    total_notional: Decimal | None = None,
    max_order_notional: Decimal | None = None,
    levels_per_side: int | None = None,
    level_size_decay: Decimal | None = None,
) -> tuple[DesiredOrder, ...]:
    """
    Cap quote sizes without changing hedge-derived prices.

    Keep the most competitive levels per venue/side.
    Never exceed the original hedgeable quantity.
    Scale hedge legs proportionally to the published size.
    """
    limits = get_risk_limits()

    if total_notional is None:
        total_notional = limits.max_total_notional

    if max_order_notional is None:
        max_order_notional = limits.max_order_notional

    if levels_per_side is None:
        levels_per_side = limits.levels_per_side

    if level_size_decay is None:
        level_size_decay = limits.level_size_decay

    if (
        quantity_step <= 0
        or min_quantity <= 0
        or total_notional <= 0
        or max_order_notional <= 0
        or levels_per_side < 1
        or not Decimal("0") < level_size_decay <= Decimal("1")
    ):
        raise ValueError("Invalid quote budget configuration")

    grouped = defaultdict(list)

    for order in orders:
        if order.side not in ("BUY", "SELL"):
            raise ValueError("Invalid order side")
        if order.price <= 0 or order.quantity <= 0:
            raise ValueError("Invalid order price or quantity")

        grouped[(order.venue, order.side)].append(order)

    remaining_budget = total_notional
    result = []

    for venue, side in sorted(grouped):
        levels = sorted(
            grouped[(venue, side)],
            key=lambda order: order.price,
            reverse=(side == "BUY"),
        )

        for rank, order in enumerate(levels[:levels_per_side]):
            if remaining_budget <= 0:
                break

            target_notional = min(
                max_order_notional
                * (level_size_decay ** rank),
                remaining_budget,
            )

            target_quantity = target_notional / order.price

            quantity = min(
                order.quantity,
                target_quantity,
            )

            quantity = (
                quantity / quantity_step
            ).to_integral_value(
                rounding=ROUND_DOWN
            ) * quantity_step

            if quantity < min_quantity:
                continue

            ratio = quantity / order.quantity

            adjusted_legs = tuple(
                HedgeLeg(
                    outcome=leg.outcome,
                    pm_price=leg.pm_price,
                    pm_quantity=leg.pm_quantity * ratio,
                    instrument_id=leg.instrument_id,
                    execution_side=leg.execution_side,
                )
                for leg in order.hedge_legs
            )

            result.append(
                DesiredOrder(
                    venue=order.venue,
                    side=order.side,
                    price=order.price,
                    quantity=quantity,
                    hedge_legs=adjusted_legs,
                )
            )

            remaining_budget -= order.price * quantity

    return tuple(result)
