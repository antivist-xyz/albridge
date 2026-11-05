
"""Persist and deduplicate Antivist executions."""

import sqlite3

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from albridge.order_registry import OrderRegistry


@dataclass(frozen=True)
class AntivistFill:
    execution_id: int
    trade_id: int | None
    order_id: int
    client_order_id: str
    market_id: int
    side: str
    quantity: Decimal
    price: Decimal
    created_at: str


def parse_execution(raw: dict) -> AntivistFill:
    """Parse one execution from the public Antivist API."""

    try:
        fill = AntivistFill(
            execution_id=raw["execution_id"],
            trade_id=raw["trade_id"],
            order_id=raw["order_id"],
            client_order_id=raw["client_order_id"],
            market_id=raw["market_id"],
            side=raw["side"],
            quantity=Decimal(raw["quantity"]),
            price=Decimal(raw["price"]),
            created_at=raw["created_at"],
        )
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid execution response") from exc

    for value in (
        fill.execution_id,
        fill.order_id,
        fill.market_id,
    ):
        if type(value) is not int or value <= 0:
            raise ValueError("Invalid execution identifier")

    if (
        type(fill.trade_id) is not int
        and fill.trade_id is not None
    ):
        raise ValueError("Invalid trade_id")

    if not isinstance(fill.client_order_id, str) or not fill.client_order_id:
        raise ValueError("Invalid client_order_id")

    if fill.side not in ("BUY", "SELL"):
        raise ValueError("Invalid execution side")

    if (
        not fill.quantity.is_finite()
        or not fill.price.is_finite()
        or fill.quantity <= 0
        or fill.price <= 0
    ):
        raise ValueError("Invalid execution values")

    if not isinstance(fill.created_at, str) or not fill.created_at:
        raise ValueError("Invalid execution timestamp")

    return fill


class FillRegistry:
    """Store Antivist fills once, including across restarts."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)

        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS antivist_fills (
                    execution_id INTEGER PRIMARY KEY,
                    trade_id INTEGER,
                    order_id INTEGER NOT NULL,
                    client_order_id TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        return sqlite3.connect(self.database_path)

    def register(self, fill: AntivistFill) -> bool:
        """
        Persist a fill belonging to a registered Albridge order.

        Return True for a new fill and False for an exact duplicate.
        Reject conflicting data with the same execution ID.
        """

        managed = OrderRegistry(
            self.database_path
        ).get(fill.client_order_id)

        if managed is None:
            raise ValueError("Execution does not belong to Albridge")

        if (
            managed.market_id != fill.market_id
            or managed.side != fill.side
            or (
                managed.order_id is not None
                and managed.order_id != fill.order_id
            )
        ):
            raise ValueError("Execution order mismatch")

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT trade_id, order_id, client_order_id,
                       market_id, side, quantity, price, created_at
                FROM antivist_fills
                WHERE execution_id = ?
                """,
                (fill.execution_id,),
            ).fetchone()

            values = (
                fill.trade_id,
                fill.order_id,
                fill.client_order_id,
                fill.market_id,
                fill.side,
                str(fill.quantity),
                str(fill.price),
                fill.created_at,
            )

            if existing is not None:
                if existing != values:
                    raise ValueError(
                        "Conflicting execution_id"
                    )
                return False

            connection.execute(
                """
                INSERT INTO antivist_fills (
                    execution_id, trade_id, order_id,
                    client_order_id, market_id, side,
                    quantity, price, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fill.execution_id, *values),
            )

        return True
