
"""Atomically record Antivist fills and their hedge obligations."""

import sqlite3

from albridge.order_registry import OrderRegistry
from albridge.risk.fill_registry import AntivistFill, FillRegistry
from albridge.risk.hedge_obligation import build_hedge_obligations
from albridge.risk.obligation_registry import ObligationRegistry


def process_antivist_fill(
    *,
    fill: AntivistFill,
    database_path: str,
) -> bool:
    """
    Persist one Antivist fill and its hedge obligations atomically.

    Return True if newly processed, False if already processed.
    Never execute external hedges.
    """

    # Ensure both tables exist before opening the transaction.
    FillRegistry(database_path)
    ObligationRegistry(database_path)

    managed = OrderRegistry(database_path).get(
        fill.client_order_id
    )

    if managed is None:
        raise ValueError("Unknown Albridge order")

    if (
        managed.market_id != fill.market_id
        or managed.side != fill.side
        or (
            managed.order_id is not None
            and managed.order_id != fill.order_id
        )
    ):
        raise ValueError("Fill/order mismatch")

    obligations = build_hedge_obligations(
        fill=fill,
        managed_order=managed,
    )

    with sqlite3.connect(database_path) as connection:
        # Acquire the write lock before checking for duplicates.
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT execution_id
            FROM antivist_fills
            WHERE execution_id = ?
            """,
            (fill.execution_id,),
        ).fetchone()

        if existing is not None:
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
            (
                fill.execution_id,
                fill.trade_id,
                fill.order_id,
                fill.client_order_id,
                fill.market_id,
                fill.side,
                str(fill.quantity),
                str(fill.price),
                fill.created_at,
            ),
        )

        for index, obligation in enumerate(obligations):
            connection.execute(
                """
                INSERT INTO hedge_obligations (
                    execution_id, leg_index,
                    client_order_id, venue,
                    instrument_id, outcome,
                    execution_side, pm_price,
                    quantity, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obligation.execution_id,
                    index,
                    obligation.client_order_id,
                    obligation.venue,
                    obligation.instrument_id,
                    obligation.outcome,
                    obligation.execution_side,
                    str(obligation.pm_price),
                    str(obligation.quantity),
                    "PENDING",
                ),
            )

    return True
