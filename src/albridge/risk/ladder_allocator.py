
"""Allocate normalized hedge ladders within external liquidity budgets."""

from dataclasses import replace
from decimal import Decimal
from typing import Mapping

from albridge.hedge_ladder import HedgeLadderLevel
from albridge.market_rules import AntivistMarketRules


ZERO = Decimal("0")


def allocate_ladder_to_budget(
    *,
    levels: tuple[HedgeLadderLevel, ...],
    token_budget: Mapping[str, Decimal],
    rules: AntivistMarketRules,
) -> tuple[HedgeLadderLevel, ...]:
    """
    Allocate normalized ladder levels FIFO.

    Preserve quote prices and instrument identities.
    Reduce quantities and hedge legs proportionally
    without exceeding the available token budgets.
    """

    if not levels:
        return ()

    remaining = {}

    for instrument_id, quantity in token_budget.items():
        if not quantity.is_finite() or quantity < ZERO:
            raise ValueError("Invalid token budget")

        remaining[instrument_id] = quantity

    result = []

    for level in levels:
        if not level.quantity.is_finite() or level.quantity <= ZERO:
            raise ValueError("Invalid ladder quantity")

        if not level.hedge_legs:
            raise ValueError("Missing hedge legs")

        # Aggregate required hedge quantities per instrument.
        required = {}

        for leg in level.hedge_legs:
            if not leg.instrument_id:
                raise ValueError("Missing hedge instrument ID")

            if not leg.pm_quantity.is_finite() or leg.pm_quantity <= ZERO:
                raise ValueError("Invalid hedge quantity")

            required[leg.instrument_id] = (
                required.get(leg.instrument_id, ZERO)
                + leg.pm_quantity
            )

        # Find the maximum Antivist quantity supported
        # by every required external instrument.
        affordable = level.quantity

        for instrument_id, required_quantity in required.items():
            available = remaining.get(instrument_id, ZERO)

            capacity = (
                available * level.quantity / required_quantity
            )

            affordable = min(affordable, capacity)

        # Round down to the Antivist quantity step.
        allocated_quantity = rules.normalize_quantity(affordable)

        if allocated_quantity < rules.min_quantity:
            break

        # Never increase the original ladder quantity.
        allocated_quantity = min(
            allocated_quantity,
            level.quantity,
        )

        # Scale every hedge leg to the allocated quantity.
        allocated_legs = tuple(
            replace(
                leg,
                pm_quantity=(
                    leg.pm_quantity
                    * allocated_quantity
                    / level.quantity
                ),
            )
            for leg in level.hedge_legs
        )

        # Reserve the allocated hedge quantities internally.
        for leg in allocated_legs:
            remaining[leg.instrument_id] -= leg.pm_quantity

        result.append(
            replace(
                level,
                quantity=allocated_quantity,
                hedge_legs=allocated_legs,
            )
        )

    return tuple(result)


def allocate_ladder_to_price_budget(
    *,
    levels: tuple[HedgeLadderLevel, ...],
    price_budget: Mapping[tuple[str, Decimal], Decimal],
    rules: AntivistMarketRules,
) -> tuple[HedgeLadderLevel, ...]:
    """
    Allocate normalized ladder levels FIFO using liquidity
    budgets indexed by instrument ID and external ask price.

    Partial allocations preserve the original hedge-leg
    composition and quote price.

    No external liquidity is reserved or executed.
    """

    remaining = {}

    for key, quantity in price_budget.items():
        if not quantity.is_finite() or quantity < ZERO:
            raise ValueError("Invalid price-level budget")

        remaining[key] = quantity

    result = []

    for level in levels:
        if not level.quantity.is_finite() or level.quantity <= ZERO:
            raise ValueError("Invalid ladder quantity")

        if not level.hedge_legs:
            raise ValueError("Missing hedge legs")

        required = {}

        for leg in level.hedge_legs:
            if not leg.instrument_id:
                raise ValueError("Missing hedge instrument ID")

            if (
                not leg.pm_price.is_finite()
                or not leg.pm_quantity.is_finite()
                or leg.pm_quantity <= ZERO
            ):
                raise ValueError("Invalid hedge leg")

            key = (leg.instrument_id, leg.pm_price)

            required[key] = (
                required.get(key, ZERO)
                + leg.pm_quantity
            )

        affordable = level.quantity

        for key, required_quantity in required.items():
            available = remaining.get(key, ZERO)

            capacity = (
                available * level.quantity / required_quantity
            )

            affordable = min(affordable, capacity)

        allocated_quantity = min(
            rules.normalize_quantity(affordable),
            level.quantity,
        )

        if allocated_quantity < rules.min_quantity:
            # The next levels are not considered because
            # allocation follows the original ladder FIFO.
            break

        # Preserve each leg's original proportional allocation.
        allocated_legs = tuple(
            replace(
                leg,
                pm_quantity=(
                    leg.pm_quantity
                    * allocated_quantity
                    / level.quantity
                ),
            )
            for leg in level.hedge_legs
        )

        # Verify and consume every price-level budget.
        consumed = {}

        for leg in allocated_legs:
            key = (leg.instrument_id, leg.pm_price)

            consumed[key] = (
                consumed.get(key, ZERO)
                + leg.pm_quantity
            )

        if any(
            quantity > remaining.get(key, ZERO)
            for key, quantity in consumed.items()
        ):
            raise ValueError("Price-level budget exceeded")

        for key, quantity in consumed.items():
            remaining[key] -= quantity

        result.append(
            replace(
                level,
                quantity=allocated_quantity,
                hedge_legs=allocated_legs,
            )
        )

    return tuple(result)
