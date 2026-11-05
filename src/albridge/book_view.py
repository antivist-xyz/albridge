"""Standalone terminal viewer for Albridge order books."""

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console
from rich.live import Live
from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from albridge.market_data import VenueMarketData
from albridge.tui import build_dashboard

import os

VIDEO_MODE = os.getenv("ALBRIDGE_VIDEO_MODE") == "1"

SNAPSHOT_STALE_SECONDS = (
    15.0 if VIDEO_MODE else 2.0
)


DEPTH_PATH = Path("recordings/live_depth.json")


@dataclass(frozen=True)
class DisplayBook:
    bids: tuple
    asks: tuple

    @property
    def spread(self):
        if not self.bids or not self.asks:
            return None

        return self.asks[0][0] - self.bids[0][0]


class DisplayMarket:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def get(self, outcome):
        return self.outcomes.get(outcome)


def parse_levels(levels):
    return tuple(
        (Decimal(price), Decimal(size))
        for price, size in levels
    )


def read_depth():
    """Read one published snapshot without opening WebSockets."""

    try:
        data = json.loads(
            DEPTH_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None

    return data


def build_pricing_table(venues, *, stale=False):
    """Render venue-specific theoretical Antivist fair prices."""

    table = Table(
        expand=True,
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
    )

    table.add_column("VENUE", ratio=2)
    table.add_column("FAIR BID", justify="right", ratio=2)
    table.add_column("FAIR ASK", justify="right", ratio=2)
    table.add_column("SPREAD", justify="right", ratio=2)
    table.add_column("SPREAD %", justify="right", ratio=2)
    table.add_column("ASK MAX QTY", justify="right")
    table.add_column("ASK LIMIT", justify="center")
    table.add_column("BID MAX QTY", justify="right")
    table.add_column("BID LIMIT", justify="center")

    for venue in ("pascal", "polymarket"):
        venue_data = venues.get(venue, {})
        pricing = venue_data.get("pricing")

        values = ("N/A",) * 8

        if (
            not stale
            and venue_data.get("status") == "READY"
            and pricing is not None
        ):
            try:
                bid = Decimal(pricing["bid"]["fair_entry"])
                ask = Decimal(pricing["ask"]["fair_entry"])

                midpoint = (ask + bid) / Decimal("2")
                spread = ask - bid

                spread_pct = (
                    spread / midpoint * Decimal("100")
                    if midpoint > 0
                    else None
                )

                values = (
                    f"{bid:.3f}",
                    f"{ask:.3f}",
                    f"{spread:.3f}",
                    (
                        f"{spread_pct:.4f}%"
                        if spread_pct is not None
                        else "N/A"
                    ),
                )

                def capacity_values(side):
                    payload = pricing[side]

                    quantity = payload.get("max_quantity")
                    outcome = payload.get("limiting_outcome")

                    return (
                        f"{Decimal(quantity):.3f}"
                        if quantity is not None
                        else "N/A",
                        outcome or "N/A",
                    )

                values += (
                    *capacity_values("ask"),
                    *capacity_values("bid"),
                )

            except (
                KeyError,
                TypeError,
                ValueError,
                ArithmeticError,
            ):
                pass

        table.add_row(
            venue.upper(),
            *values,
        )

    return Panel(
        table,
        title="[bold]FAIR HEDGE PRICING[/bold]",
        subtitle="THEORETICAL | BEFORE EXECUTION COSTS",
        border_style="cyan",
        expand=True,
    )

def build_consolidated_hedge_book(venues, *, stale=False):
    """
    Display normalized hedge ladders from independent venues.

    This is a consolidated view, not consolidated liquidity.
    """

    table = Table(
        expand=True,
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
    )

    table.add_column("VENUE", ratio=2)
    table.add_column("PRICE", justify="right", ratio=2)
    table.add_column("QTY", justify="right", ratio=2)
    table.add_column("NOTIONAL", justify="right", ratio=2)
    table.add_column("LEGS", justify="right")

    bids = []
    asks = []

    if not stale:
        for venue in ("pascal", "polymarket"):
            venue_data = venues.get(venue, {})

            if venue_data.get("status") != "READY":
                continue

            # Display risk-allocated quotes instead of theoretical capacity.
            ladders = venue_data.get("risk_ladders") or {}

            for side, destination in (
                ("buy", bids),
                ("sell", asks),
            ):
                for level in ladders.get(side, []):
                    price = Decimal(str(level["price"]))
                    quantity = Decimal(str(level["quantity"]))

                    destination.append({
                        "venue": venue.upper(),
                        "price": price,
                        "quantity": quantity,
                        "notional": price * quantity,
                        "legs": len(level.get("hedge_legs", [])),
                    })

    # Best ask closest to spread appears at the bottom of asks.
    asks.sort(key=lambda level: level["price"], reverse=True)

    # Best bid closest to spread appears at the top of bids.
    bids.sort(key=lambda level: level["price"], reverse=True)


    def add_level(level, *, side):
        color = "red" if side == "SELL" else "green"

        table.add_row(
            level["venue"],
            f"[{color}]{level['price']:.2f}[/{color}]",
            f"{level['quantity']:,.3f}",
            f"{level['notional']:,.2f}",
            str(level["legs"]),
        )

    for level in asks:
        add_level(level, side="SELL")

    best_ask = asks[-1]["price"] if asks else None
    best_bid = bids[0]["price"] if bids else None

    spread = (
        best_ask - best_bid
        if best_ask is not None and best_bid is not None
        else None
    )
    midpoint = (
        (best_ask + best_bid) / Decimal("2")
        if best_ask is not None and best_bid is not None
        else None
    )

    spread_pct = (
        spread / midpoint * Decimal("100")
        if spread is not None and midpoint is not None and midpoint > 0
        else None
    )

    spread_label = (
        f"SPREAD {spread:.2f} ({spread_pct:.4f}%)"
        if spread is not None and spread_pct is not None
        else "SPREAD N/A"
    )

    table.add_row(
        "",
        f"[bold cyan]{spread_label}[/bold cyan]",
        "",
        "",
        "",
    )

    for level in bids:
        add_level(level, side="BUY")

    if not bids and not asks:
        table.add_row(
            "NO DATA",
            "-",
            "-",
            "-",
            "-",
        )

    return Panel(
        table,
        title="[bold]CONSOLIDATED HEDGE ORDER BOOK[/bold]",
        subtitle="RISK-ALLOCATED 50/50 | INDICATIVE | NO LIVE ORDERS",
        border_style="cyan",
        expand=True,
    )


def build_view(data):
    snapshots = {}
    health = {}

    venues = data.get("venues", {}) if data else {}

    for venue in ("pascal", "polymarket"):
        venue_data = venues.get(venue, {})
        outcomes = {}

        for outcome, book in venue_data.get(
            "outcomes", {}
        ).items():
            outcomes[outcome] = DisplayBook(
                bids=parse_levels(book["bids"]),
                asks=parse_levels(book["asks"]),
            )

        snapshots[venue] = (
            DisplayMarket(outcomes)
            if outcomes
            else None
        )

        status = venue_data.get("status", "UNAVAILABLE")

        # Do not present old data as currently live.
        if data and time.time() - data["published_at"] > SNAPSHOT_STALE_SECONDS:
            status = "STALE"

        health[venue] = SimpleNamespace(status=status)

    market_data = VenueMarketData(
        pascal=snapshots["pascal"],
        polymarket=snapshots["polymarket"],
    )

    fixture_label = (
        data.get("fixture", "Waiting for market data")
        if data
        else "Waiting for market data"
    )

    order_books = build_dashboard(
        market_data=market_data,
        health_by_venue=health,
        fixture_label=fixture_label,
    )

    pricing_table = build_pricing_table(
        venues,
        stale=(
            not data
            or time.time() - data.get("published_at", 0) > SNAPSHOT_STALE_SECONDS
        ),
    )

    hedge_book = build_consolidated_hedge_book(
        venues,
        stale=(
            not data
            or time.time() - data.get("published_at", 0) > SNAPSHOT_STALE_SECONDS
        ),
    )

    return Group(
        *([Panel("PUBLIC DEMO — theoretical quotes only; no orders submitted to Antivist", border_style="yellow")] if data and data.get("mode") == "PUBLIC DEMO" else []),
        pricing_table,
        hedge_book,
        order_books,
    )

def main():
    console = Console()

    with Live(
        build_view(None),
        console=console,
        screen=True,
        auto_refresh=False,
    ) as live:
        try:
            while True:
                live.update(
                    build_view(read_depth()),
                    refresh=True,
                )
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
