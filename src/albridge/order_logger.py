
"""Write Order Manager decisions to a dedicated log."""

import logging
from collections import Counter
from pathlib import Path

from albridge.order_manager import (
    ReconciliationAction,
    ReconciliationDecision,
)

from decimal import Decimal


def format_decimal(value: Decimal) -> str:
    """Remove unnecessary trailing zeros without losing precision."""
    return format(value, "f").rstrip("0").rstrip(".") if value else "0"

class OrderDecisionLogger:
    """Log changes in reconciliation decisions."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("albridge.order_decisions")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        # Prevent duplicate handlers in the same process.
        if not self.logger.handlers:
            handler = logging.FileHandler(path)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(message)s",
                datefmt="%H:%M:%S",
            ))
            self.logger.addHandler(handler)

        self.previous = Counter()

    @staticmethod
    def _identity(decision: ReconciliationDecision):
        desired = decision.desired_order
        existing = decision.existing_order

        if desired is not None:
            return (
                decision.action.value,
                desired.venue,
                desired.side,
                desired.price,
                desired.quantity,
                desired.hedge_legs,
                existing.order_id if existing is not None else None,
            )

        if existing is not None:
            return (
                decision.action.value,
                None,
                existing.side,
                existing.price,
                existing.remaining_quantity,
                (),
                existing.order_id,
            )

        raise ValueError("Decision without order")

    def log_plan(
        self,
        plan: tuple[ReconciliationDecision, ...],
    ) -> None:
        """Log only newly appearing decisions."""

        current = Counter(
            self._identity(decision)
            for decision in plan
        )

        previous = self.previous
        new_decisions = current - previous

        for decision in plan:
            key = self._identity(decision)

            if new_decisions[key] <= 0:
                continue

            new_decisions[key] -= 1

            action, venue, side, price, quantity, _, order_id = key

            # KEEP is intentionally omitted from the event stream.
            if action == ReconciliationAction.KEEP.value:
                continue

            venue_label = (
                venue.upper()
                if venue is not None
                else f"ORDER:{order_id}"
            )

            self.logger.info(
                "DRY RUN | ANTIVIST | %-6s | %-4s %10s @ %8s | hedge: %s",
                action,
                side,
                format_decimal(quantity),
                format_decimal(price),
                venue_label,
            )

        self.previous = current
