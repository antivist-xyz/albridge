"""Maintain normalized L2 order books from Pascal WebSocket messages."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class PascalOrderBook:
    symbol: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    sequence: int | None = None
    state_time_ms: int | None = None
    server_time_ms: int | None = None
    spec: dict[str, Any] = field(default_factory=dict)

    @property
    def initialized(self) -> bool:
        return self.sequence is not None

    def reset(self) -> None:
        """Invalidate the book when the WebSocket disconnects."""
        self.bids.clear()
        self.asks.clear()
        self.sequence = None
        self.state_time_ms = None
        self.server_time_ms = None
        self.spec.clear()

    @staticmethod
    def _parse_levels(levels):
        """Validate and normalize absolute price-level quantities."""
        result = {}

        for price_raw, quantity_raw in levels:
            price = Decimal(str(price_raw))
            quantity = Decimal(str(quantity_raw))

            if not price.is_finite() or not quantity.is_finite():
                raise ValueError("Non-finite book price or quantity")

            if not Decimal("0") < price <= Decimal("1"):
                raise ValueError("Invalid prediction-market price")

            if quantity < 0:
                raise ValueError("Negative book quantity")

            result[price] = quantity

        return result

    def apply(self, message: dict) -> bool:
        """Apply a snapshot or an incremental update atomically."""
        if message.get("channel") != "book":
            return False

        if message.get("symbol") != self.symbol:
            return False

        event_type = message.get("type")

        if event_type not in {"snapshot", "update"}:
            return False

        sequence = int(message["seq"])

        if event_type == "update" and not self.initialized:
            return False

        if self.initialized and sequence <= self.sequence:
            return False

        data = message["data"]

        # Validate both sides before mutating the local book.
        bids = self._parse_levels(data.get("bids", []))
        asks = self._parse_levels(data.get("asks", []))

        if event_type == "snapshot":
            new_bids = {p: q for p, q in bids.items() if q > 0}
            new_asks = {p: q for p, q in asks.items() if q > 0}
        else:
            new_bids = self.bids.copy()
            new_asks = self.asks.copy()

            for target, changes in (
                (new_bids, bids),
                (new_asks, asks),
            ):
                for price, quantity in changes.items():
                    if quantity == 0:
                        target.pop(price, None)
                    else:
                        target[price] = quantity

        self.bids = new_bids
        self.asks = new_asks
        self.sequence = sequence

        self.state_time_ms = int(message["state_time_ms"])
        self.server_time_ms = int(message["server_time_ms"])

        if "spec" in data:
            self.spec.update(data["spec"])

        return True

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def levels(self, side: str) -> list[tuple[Decimal, Decimal]]:
        """Return price levels in execution priority order."""
        if side == "BUY":
            return sorted(self.bids.items(), reverse=True)
        if side == "SELL":
            return sorted(self.asks.items())
        raise ValueError("side must be BUY or SELL")

    @property
    def valid_for_quoting(self) -> bool:
        return (
            self.initialized
            and self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid < self.best_ask
        )
