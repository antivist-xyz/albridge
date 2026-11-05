
"""Trading rules retrieved from the Antivist public API."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP


@dataclass(frozen=True)
class AntivistMarketRules:
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal

    @classmethod
    def from_market(cls, market: dict):
        rules = cls(
            tick_size=Decimal(str(market["tick_size"])),
            quantity_step=Decimal(str(market["quantity_step"])),
            min_quantity=Decimal(str(market["min_quantity"])),
        )

        if any(
            not value.is_finite() or value <= 0
            for value in (
                rules.tick_size,
                rules.quantity_step,
                rules.min_quantity,
            )
        ):
            raise ValueError("Invalid Antivist trading rules")

        return rules

    def normalize_quantity(self, quantity: Decimal) -> Decimal:
        """Round down to the exchange quantity step."""
        return (
            (quantity / self.quantity_step).to_integral_value(
                rounding=ROUND_DOWN
            )
            * self.quantity_step
        )

    def normalize_price(
        self,
        price: Decimal,
        side: str,
    ) -> Decimal:
        """Round away from the theoretical break-even price."""

        if side not in ("BUY", "SELL"):
            raise ValueError("Invalid order side")

        rounding = (
            ROUND_UP if side == "SELL" else ROUND_DOWN
        )

        return (
            (price / self.tick_size).to_integral_value(
                rounding=rounding
            )
            * self.tick_size
        )

    def is_tradable(self, quantity: Decimal) -> bool:
        return (
            quantity.is_finite()
            and quantity >= self.min_quantity
            and quantity > 0
            and quantity % self.quantity_step == 0
        )
