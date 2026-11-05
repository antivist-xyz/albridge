"""Allocate external hedge capacity between Antivist quote sides."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


ZERO = Decimal("0")
HALF = Decimal("0.5")


@dataclass(frozen=True)
class QuoteRiskBudget:
    sell_max_quantity: Decimal
    buy_max_quantity: Decimal
    sell_token_budget: dict[str, Decimal]
    buy_token_budget: dict[str, Decimal]


def calculate_quote_risk_budget(
    *,
    available: Mapping[str, Decimal],
    sell_ratios: Mapping[str, Decimal],
    buy_ratios: Mapping[str, Decimal],
) -> QuoteRiskBudget:
    """
    Split shared token liquidity 50/50 between SELL and BUY.

    Instruments required by only one side retain
    their full available capacity.
    """

    sell_budget = {}
    buy_budget = {}

    instruments = set(sell_ratios) | set(buy_ratios)

    for instrument_id in instruments:
        quantity = available.get(instrument_id, ZERO)

        if not quantity.is_finite() or quantity < ZERO:
            raise ValueError("Invalid available liquidity")

        used_by_sell = instrument_id in sell_ratios
        used_by_buy = instrument_id in buy_ratios

        if used_by_sell and used_by_buy:
            sell_budget[instrument_id] = quantity * HALF
            buy_budget[instrument_id] = quantity * HALF

        elif used_by_sell:
            sell_budget[instrument_id] = quantity

        elif used_by_buy:
            buy_budget[instrument_id] = quantity

    def max_quantity(
        budget: dict[str, Decimal],
        ratios: Mapping[str, Decimal],
    ) -> Decimal:
        if not ratios:
            return ZERO

        capacities = []

        for instrument_id, ratio in ratios.items():
            if not ratio.is_finite() or ratio <= ZERO:
                raise ValueError("Invalid hedge ratio")

            capacities.append(
                budget.get(instrument_id, ZERO) / ratio
            )

        return min(capacities)

    return QuoteRiskBudget(
        sell_max_quantity=max_quantity(
            sell_budget,
            sell_ratios,
        ),
        buy_max_quantity=max_quantity(
            buy_budget,
            buy_ratios,
        ),
        sell_token_budget=sell_budget,
        buy_token_budget=buy_budget,
    )