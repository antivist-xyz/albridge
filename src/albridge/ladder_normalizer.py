
"""Normalize theoretical hedge segments into Antivist orders."""

from collections import deque
from dataclasses import replace
from decimal import Decimal

from albridge.hedge_ladder import HedgeLadderLevel, HedgeLeg
from albridge.market_rules import AntivistMarketRules


ZERO = Decimal("0")


def normalize_hedge_ladder(
    *,
    levels: tuple[HedgeLadderLevel, ...],
    rules: AntivistMarketRules,
) -> tuple[HedgeLadderLevel, ...]:
    """
    Convert raw hedge segments into tradable Antivist levels.

    Consume segments FIFO, aggregate small quantities and
    preserve the remaining theoretical hedge capacity.
    """

    if not levels:
        return ()

    side = levels[0].side
    venue = levels[0].venue

    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid ladder side")

    if any(
        level.side != side or level.venue != venue
        for level in levels
    ):
        raise ValueError("Cannot mix sides or venues")

    # Ignore only numerical dust far below one quantity step.
    dust = rules.quantity_step / Decimal("1000000000000")

    pending = deque()
    pending_quantity = ZERO
    result = []

    for level in levels:
        if not level.quantity.is_finite():
            raise ValueError("Non-finite ladder quantity")

        if level.quantity < -dust:
            raise ValueError("Negative ladder quantity")

        if level.quantity <= dust:
            continue

        pending.append([level, level.quantity])
        pending_quantity += level.quantity

        quantity = rules.normalize_quantity(pending_quantity)

        if quantity < rules.min_quantity:
            continue

        # Consume the required quantity from pending segments,
        # retaining any unconsumed remainder for later levels.
        to_consume = quantity
        total_cost = ZERO
        combined_legs = {}

        while to_consume > dust:
            source, available = pending[0]
            taken = min(to_consume, available)

            total_cost += taken * source.price

            fraction = taken / source.quantity

            for leg in source.hedge_legs:
                # Never merge liquidity belonging to different instruments.
                key = (
                    leg.instrument_id,
                    leg.outcome,
                    leg.pm_price,
                )

                consumed_pm = leg.pm_quantity * fraction

                combined_legs[key] = (
                    combined_legs.get(key, ZERO) + consumed_pm
                )

            available -= taken
            to_consume -= taken

            if available <= dust:
                pending.popleft()
            else:
                pending[0][1] = available

        raw_price = total_cost / quantity

        normalized_price = rules.normalize_price(
            raw_price,
            side,
        )

        hedge_legs = tuple(
            HedgeLeg(
                outcome=outcome,
                pm_price=pm_price,
                pm_quantity=pm_quantity,
                instrument_id=instrument_id,
            )
            for (instrument_id, outcome, pm_price), pm_quantity
            in combined_legs.items()
        )

        result.append(
            replace(
                level,
                price=normalized_price,
                quantity=quantity,
                hedge_legs=hedge_legs,
            )
        )

        pending_quantity -= quantity

        if abs(pending_quantity) <= dust:
            pending_quantity = ZERO

    return tuple(result)
