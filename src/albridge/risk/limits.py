
"""Centralized Albridge quoting and risk limits."""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class RiskLimits:
    max_total_notional: Decimal
    max_order_notional: Decimal
    levels_per_side: int
    level_size_decay: Decimal


def _decimal_env(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)

    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid {name}: {raw!r}")

    if not value.is_finite():
        raise ValueError(f"{name} must be finite")

    return value


def get_risk_limits() -> RiskLimits:
    limits = RiskLimits(
        max_total_notional=_decimal_env(
            "ALBRIDGE_MAX_TOTAL_NOTIONAL", "20000"
        ),
        max_order_notional=_decimal_env(
            "ALBRIDGE_MAX_ORDER_NOTIONAL", "2500"
        ),
        levels_per_side=int(
            os.getenv("ALBRIDGE_LEVELS_PER_SIDE", "5")
        ),
        level_size_decay=_decimal_env(
            "ALBRIDGE_LEVEL_SIZE_DECAY", "0.80"
        ),
    )

    if limits.max_total_notional <= 0:
        raise ValueError("Invalid total notional limit")

    if not 0 < limits.max_order_notional <= limits.max_total_notional:
        raise ValueError("Invalid order notional limit")

    if limits.levels_per_side < 1:
        raise ValueError("Invalid levels per side")

    if not 0 < limits.level_size_decay <= 1:
        raise ValueError("Invalid level size decay")

    return limits
