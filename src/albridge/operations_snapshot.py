
"""Atomic publication of read-only operations snapshots."""

import json
import os
import tempfile
import time
from pathlib import Path

from albridge.risk.limits import get_risk_limits
from decimal import Decimal

from albridge.order_manager import parse_existing_orders


DEFAULT_PATH = Path("recordings/live_operations.json")


def collect_exchange_state(
    *,
    client,
    registry,
    market_id: int,
) -> dict:
    """
    Read actual resting orders from Antivist.

    Read-only. Does not submit, cancel or reconcile.
    Raise on uncertain exchange state.
    """

    response = client.get_orders(market_id=market_id)
    existing = parse_existing_orders(response=response)

    registered = registry.get_by_market(
        market_id=market_id,
    )

    pending_statuses = {
        "PENDING_SUBMIT",
        "PENDING_CANCEL",
    }

    pending = [
        order
        for order in registered
        if order.status in pending_statuses
    ]

    def counts(orders):
        orders = tuple(orders)
        return {
            "buy": sum(o.side == "BUY" for o in orders),
            "sell": sum(o.side == "SELL" for o in orders),
            "total": len(orders),
        }

    resting_notional = sum(
        (
            order.price * order.remaining_quantity
            for order in existing
        ),
        Decimal("0"),
    )

    return {
        "orders": counts(existing),
        "pending": counts(pending),
        "resting_notional": resting_notional,
    }


def publish_operations_snapshot(
    *,
    market_id: int,
    fixture_label: str,
    desired_orders,
    execute_live: bool,
    exchange_state: dict | None = None,
    reconciliation: dict | None = None,
    venues: dict | None = None,
    exchange_verified_at: float | None = None,
    path: Path = DEFAULT_PATH,
    public_demo: bool = False,
) -> None:
    """Publish current runtime state without changing execution."""

    limits = get_risk_limits()
    desired_orders = tuple(desired_orders)

    buy = sum(o.side == "BUY" for o in desired_orders)
    sell = sum(o.side == "SELL" for o in desired_orders)

    desired_notional = sum(
        (o.price * o.quantity for o in desired_orders),
        start=0,
    )


    # Exchange state is verified independently from desired quotes.
    verified = exchange_state is not None

    resting_notional = (
        exchange_state["resting_notional"]
        if verified
        else None
    )

    available_budget = (
        limits.max_total_notional - resting_notional
        if verified
        else None
    )

    if available_budget is not None:
        available_budget = max(
            available_budget,
            Decimal("0"),
        )

    def money(amount):
        if amount is None:
            return None
        return f"{amount:,.2f} USDC"


    snapshot = {
        "updated_at": time.time(),
        "mode": "PUBLIC DEMO" if public_demo else ("LIVE" if execute_live else "DRY RUN"),
        "state": "RUNNING",
        "market": str(market_id),
        "fixture": fixture_label,
        "risk": {
            "resting_notional": money(resting_notional),
            "notional_limit": money(limits.max_total_notional),
            "available_budget": money(available_budget),
            "net_position": None,
            "unhedged_exposure": None,
            "worst_case_loss": None,
        },
        "orders": {
            "desired": {
                "buy": buy,
                "sell": sell,
                "total": len(desired_orders),
            },
            "exchange": (
                exchange_state["orders"]
                if verified
                else {"buy": None, "sell": None, "total": None}
            ),
            "pending": (
                exchange_state["pending"]
                if verified
                else {"buy": None, "sell": None, "total": None}
            ),
        },
        "venues": venues or {},
        "pnl": {},
        "reconciliation": reconciliation or {},
        "exchange_verified_at": exchange_verified_at,
        "alerts": (
            [
                "EXCHANGE ORDERS NOT VERIFIED",
                "External hedge execution disabled",
            ]
            if not verified and not public_demo
            else [
                "External hedge execution disabled",
            ]
        )
        + (
            ["RESTING NOTIONAL EXCEEDS CONFIGURED LIMIT"]
            if verified
            and resting_notional > limits.max_total_notional
            else []
        ),
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write a complete JSON file before replacing the visible one.
    # Readers never receive a partially written snapshot.
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".operations-",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(snapshot, file, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)