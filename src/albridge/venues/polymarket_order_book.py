"""Local Polymarket CLOB order-book reconstruction."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


def parse_decimal(value) -> Decimal:
    """Parse a finite decimal value."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid decimal: {value}") from exc

    if not result.is_finite():
        raise ValueError("Non-finite decimal")

    return result


def parse_level(price_value, size_value):
    price = parse_decimal(price_value)
    size = parse_decimal(size_value)

    if not Decimal("0") < price <= Decimal("1"):
        raise ValueError("Price outside prediction-market range")

    if size < 0:
        raise ValueError("Negative order-book size")

    return price, size


@dataclass
class PolymarketOrderBook:
    asset_id: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)

    timestamp_ms: int | None = None
    market: str | None = None
    book_hash: str | None = None

    @property
    def initialized(self) -> bool:
        return self.timestamp_ms is not None

    def reset(self):
        """Invalidate the book after a disconnection."""
        self.bids.clear()
        self.asks.clear()
        self.timestamp_ms = None
        self.market = None
        self.book_hash = None

    @staticmethod
    def _parse_snapshot_levels(levels):
        result = {}

        for level in levels:
            price, size = parse_level(
                level["price"],
                level["size"],
            )

            if price in result:
                raise ValueError("Duplicate snapshot price")

            if size > 0:
                result[price] = size

        return result

    def apply_snapshot(self, message: dict) -> bool:
        """Replace the entire local book."""
        if message.get("event_type") != "book":
            return False

        if str(message.get("asset_id")) != self.asset_id:
            return False

        timestamp = int(message["timestamp"])

        if timestamp < 0:
            raise ValueError("Negative timestamp")

        if self.initialized and timestamp < self.timestamp_ms:
            return False

        bids = self._parse_snapshot_levels(message["bids"])
        asks = self._parse_snapshot_levels(message["asks"])

        self.bids = bids
        self.asks = asks
        self.timestamp_ms = timestamp
        self.market = message.get("market")
        self.book_hash = message.get("hash")

        return True

    def apply_change(
        self,
        change: dict,
        timestamp_ms: int,
    ) -> bool:
        """Apply one absolute-size price-level update."""
        if str(change.get("asset_id")) != self.asset_id:
            return False

        if not self.initialized:
            return False

        timestamp_ms = int(timestamp_ms)

        if timestamp_ms < self.timestamp_ms:
            return False

        side = change.get("side")

        if side == "BUY":
            levels = self.bids
        elif side == "SELL":
            levels = self.asks
        else:
            raise ValueError(f"Invalid side: {side}")

        price, size = parse_level(
            change["price"],
            change["size"],
        )

        if size == 0:
            levels.pop(price, None)
        else:
            levels[price] = size

        self.timestamp_ms = timestamp_ms
        self.book_hash = None

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

    def levels(self, side: str):
        """Return best-to-worst price levels."""
        if side == "BUY":
            return sorted(
                self.bids.items(),
                reverse=True,
            )

        if side == "SELL":
            return sorted(self.asks.items())

        raise ValueError(f"Invalid book side: {side}")

    @property
    def valid_for_quoting(self) -> bool:
        """Structural validity only, not a freshness guarantee."""
        return (
            self.initialized
            and self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid < self.best_ask
        )
