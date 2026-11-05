
"""Persistent registry for Albridge-managed Antivist orders."""

import json
import sqlite3

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from albridge.hedge_ladder import HedgeLeg


@dataclass(frozen=True)
class ManagedOrder:
    client_order_id: str
    order_id: int | None
    market_id: int
    venue: str
    side: str
    price: Decimal
    quantity: Decimal
    hedge_legs: tuple[HedgeLeg, ...]
    status: str


class OrderRegistry:
    """Store managed orders independently of the exchange."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)

        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS managed_orders (
                    client_order_id TEXT PRIMARY KEY,
                    order_id INTEGER UNIQUE,
                    market_id INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    hedge_legs TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)

    def _connect(self):
        return sqlite3.connect(self.database_path)

    @staticmethod
    def _serialize_legs(legs: tuple[HedgeLeg, ...]) -> str:
        return json.dumps([
            {
                "outcome": leg.outcome,
                "pm_price": str(leg.pm_price),
                "pm_quantity": str(leg.pm_quantity),
                "instrument_id": leg.instrument_id,
                "execution_side": leg.execution_side,
            }
            for leg in legs
        ])

    @staticmethod
    def _deserialize_legs(raw: str) -> tuple[HedgeLeg, ...]:
        return tuple(
            HedgeLeg(
                outcome=item["outcome"],
                pm_price=Decimal(item["pm_price"]),
                pm_quantity=Decimal(item["pm_quantity"]),
                instrument_id=item["instrument_id"],
                execution_side=item["execution_side"],
            )
            for item in json.loads(raw)
        )

    def register(self, order: ManagedOrder) -> None:
        """
        Persist a new order before exchange submission.

        Reject duplicate client IDs instead of overwriting
        an existing order and its hedge allocation.
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_orders (
                    client_order_id,
                    order_id,
                    market_id,
                    venue,
                    side,
                    price,
                    quantity,
                    hedge_legs,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.client_order_id,
                    order.order_id,
                    order.market_id,
                    order.venue,
                    order.side,
                    str(order.price),
                    str(order.quantity),
                    self._serialize_legs(order.hedge_legs),
                    order.status,
                ),
            )

    def get(self, client_order_id: str) -> ManagedOrder | None:
        """Recover a registered order and its hedge legs."""

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row

            row = connection.execute(
                """
                SELECT *
                FROM managed_orders
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()

        if row is None:
            return None

        return ManagedOrder(
            client_order_id=row["client_order_id"],
            order_id=row["order_id"],
            market_id=row["market_id"],
            venue=row["venue"],
            side=row["side"],
            price=Decimal(row["price"]),
            quantity=Decimal(row["quantity"]),
            hedge_legs=self._deserialize_legs(
                row["hedge_legs"]
            ),
            status=row["status"],
        )

    def confirm_open(
        self,
        *,
        client_order_id: str,
        order_id: int,
    ) -> None:
        """
        Associate a confirmed Antivist order ID with its
        previously persisted submission request.

        Reject conflicting confirmations.
        """

        if type(order_id) is not int or order_id <= 0:
            raise ValueError("Invalid order_id")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE managed_orders
                SET order_id = ?,
                    status = 'OPEN'
                WHERE client_order_id = ?
                AND status IN ('PENDING_SUBMIT', 'OPEN')
                AND (order_id IS NULL OR order_id = ?)
                """,
                (
                    order_id,
                    client_order_id,
                    order_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Unknown order or conflicting confirmation"
                )

    def get_by_market(
        self,
        *,
        market_id: int,
    ) -> tuple[ManagedOrder, ...]:
        """
        Return all registered orders for one Antivist market.

        Include terminal and pending orders because they may
        still be relevant for reconciliation and recovery.
        """

        if type(market_id) is not int or market_id <= 0:
            raise ValueError("Invalid market_id")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row

            rows = connection.execute(
                """
                SELECT client_order_id
                FROM managed_orders
                WHERE market_id = ?
                ORDER BY client_order_id
                """,
                (market_id,),
            ).fetchall()

        return tuple(
            self.get(row["client_order_id"])
            for row in rows
        )

    def confirm_submission(
        self,
        *,
        client_order_id: str,
        order_id: int,
        status: str,
    ) -> None:
        """
        Persist a confirmed Antivist submission.

        Preserve the actual exchange order status.
        """

        allowed_statuses = {
            "OPEN",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELED",
        }

        if type(order_id) is not int or order_id <= 0:
            raise ValueError("Invalid order_id")

        if status not in allowed_statuses:
            raise ValueError(f"Unsupported order status: {status}")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE managed_orders
                SET order_id = ?,
                    status = ?
                WHERE client_order_id = ?
                AND status = 'PENDING_SUBMIT'
                AND order_id IS NULL
                """,
                (order_id, status, client_order_id),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Submission is not pending or confirmation conflicts"
                )


    def reject_submission(
        self,
        *,
        client_order_id: str,
    ) -> None:
        """Mark an explicitly rejected submission as terminal."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE managed_orders
                SET status = 'REJECTED'
                WHERE client_order_id = ?
                AND status = 'PENDING_SUBMIT'
                AND order_id IS NULL
                """,
                (client_order_id,),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Submission is not pending or was already resolved"
                )


    def apply_submission_outcomes(
        self,
        outcomes: tuple[tuple[str, bool, int | None, str], ...],
    ) -> None:
        """
        Atomically persist Antivist batch submission outcomes.

        Each outcome contains:
            (client_order_id, accepted, order_id, status)

        Replaying an identical confirmation is safe.
        Conflicting confirmations roll back the entire batch.
        """

        accepted_statuses = {
            "OPEN",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELED",
        }

        seen_ids = set()

        for client_order_id, accepted, order_id, status in outcomes:
            if not isinstance(client_order_id, str) or not client_order_id:
                raise ValueError("Invalid client_order_id")

            if client_order_id in seen_ids:
                raise ValueError("Duplicate client_order_id in batch")

            seen_ids.add(client_order_id)

            if type(accepted) is not bool:
                raise ValueError("Invalid accepted flag")

            if accepted:
                if type(order_id) is not int or order_id <= 0:
                    raise ValueError("Invalid accepted order_id")

                if status not in accepted_statuses:
                    raise ValueError("Invalid accepted order status")
            else:
                if order_id is not None or status != "REJECTED":
                    raise ValueError("Invalid rejected outcome")

        with self._connect() as connection:
            # Explicit transaction ensures rollback on any conflict.
            connection.execute("BEGIN IMMEDIATE")

            for client_order_id, accepted, order_id, status in outcomes:
                row = connection.execute(
                    """
                    SELECT order_id, status
                    FROM managed_orders
                    WHERE client_order_id = ?
                    """,
                    (client_order_id,),
                ).fetchone()

                if row is None:
                    raise ValueError(
                        f"Unknown managed order: {client_order_id}"
                    )

                current_order_id, current_status = row

                # An identical response may safely be replayed.
                if (
                    current_order_id == order_id
                    and current_status == status
                ):
                    continue

                # Only pending submissions may be resolved.
                if (
                    current_status != "PENDING_SUBMIT"
                    or current_order_id is not None
                ):
                    raise ValueError(
                        f"Conflicting submission: {client_order_id}"
                    )

                connection.execute(
                    """
                    UPDATE managed_orders
                    SET order_id = ?,
                        status = ?
                    WHERE client_order_id = ?
                    """,
                    (order_id, status, client_order_id),
                )

    def confirm_cancellations(
        self,
        *,
        order_ids: tuple[int, ...],
    ) -> None:
        """
        Atomically confirm canceled Antivist orders.

        Repeated confirmations are safe.
        """

        if not order_ids:
            return

        if (
            len(set(order_ids)) != len(order_ids)
            or any(
                type(order_id) is not int or order_id <= 0
                for order_id in order_ids
            )
        ):
            raise ValueError("Invalid cancellation IDs")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            for order_id in order_ids:
                row = connection.execute(
                    """
                    SELECT status
                    FROM managed_orders
                    WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()

                if row is None:
                    raise ValueError(
                        f"Unknown managed order: {order_id}"
                    )

                status = row[0]

                if status == "CANCELED":
                    continue

                if status not in {"OPEN", "PARTIALLY_FILLED"}:
                    raise ValueError(
                        f"Cannot confirm cancellation of "
                        f"order {order_id}: {status}"
                    )

                connection.execute(
                    """
                    UPDATE managed_orders
                    SET status = 'CANCELED'
                    WHERE order_id = ?
                    """,
                    (order_id,),
                )


    def update_kept_hedges(self, updates):
        """
        Atomically update hedge allocations of confirmed open orders.

        updates:
            (client_order_id, order_id, hedge_legs)
        """

        if not updates:
            return

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            seen = set()

            for client_order_id, order_id, hedge_legs in updates:
                if client_order_id in seen:
                    raise ValueError("Duplicate KEEP update")

                seen.add(client_order_id)

                cursor = connection.execute(
                    """
                    UPDATE managed_orders
                    SET hedge_legs = ?
                    WHERE client_order_id = ?
                      AND order_id = ?
                      AND status = 'OPEN'
                    """,
                    (
                        self._serialize_legs(hedge_legs),
                        client_order_id,
                        order_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise ValueError(
                        f"Cannot update hedge allocation: {client_order_id}"
                    )
