
"""Read-only market maker operations terminal."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
import os

VIDEO_MODE = os.getenv("ALBRIDGE_VIDEO_MODE") == "1"

SNAPSHOT_STALE_SECONDS = (
    15.0 if VIDEO_MODE else 5.0
)

EXCHANGE_STALE_SECONDS = (
    15.0 if VIDEO_MODE else 3.0
)

DEFAULT_FILE = Path("recordings/live_operations.json")



def value(data: dict, key: str) -> str:
    item = data.get(key)
    return "N/A" if item is None else str(item)


def status_text(status: str) -> Text:
    status = status.upper()

    color = {
        "READY": "green",
        "QUOTING": "green",
        "DRY RUN": "cyan",
        "LIVE": "yellow",
        "BLOCKED": "red",
        "HALTED": "bold red",
        "STALE": "bold red",
        "UNAVAILABLE": "red",
    }.get(status, "white")

    return Text(status, style=color)


def read_snapshot(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            snapshot = json.load(file)

        if not isinstance(snapshot, dict):
            return None

        return snapshot

    except (OSError, ValueError):
        return None


def make_table(*headers: str) -> Table:
    table = Table(
        expand=True,
        show_edge=False,
        box=None,
        padding=(0, 2),
    )

    for index, header in enumerate(headers):
        table.add_column(
            header,
            justify="left" if index == 0 else "right",
            no_wrap=True,
        )

    return table


def render(snapshot: dict | None, path: Path):
    if snapshot is None:
        return Panel(
            "NO OPERATIONS SNAPSHOT\n"
            f"Waiting for: {path}\n"
            "No operational state has been verified.",
            title="ALBRIDGE / MM CONTROL",
            border_style="yellow",
        )

    updated_at = snapshot.get("updated_at")
    stale = True

    if type(updated_at) in (int, float):
        age = time.time() - updated_at
        stale = (
            age < -2
            or age > SNAPSHOT_STALE_SECONDS
        )

    exchange_verified_at = snapshot.get("exchange_verified_at")

    exchange_age = None
    exchange_stale = True

    if type(exchange_verified_at) in (int, float):
        exchange_age = time.time() - exchange_verified_at
        exchange_stale = (
            exchange_age < -2
            or exchange_age > EXCHANGE_STALE_SECONDS
        )

    if exchange_age is None:
        exchange_status = "UNAVAILABLE"
    elif exchange_stale:
        exchange_status = f"STALE ({exchange_age:.2f}s)"
    else:
        exchange_status = f"FRESH ({exchange_age:.2f}s)"

    mode = value(snapshot, "mode")
    state = value(snapshot, "state")

    if stale:
        state = "STALE"

    header = Table.grid(expand=True)
    header.add_column()
    header.add_column(justify="right")

    header.add_row(
        Text("ALBRIDGE / MM CONTROL", style="bold"),
        status_text(mode),
    )
    header.add_row(
        f"MARKET  {value(snapshot, 'market')}",
        status_text(state),
    )
    header.add_row(
        f"FIXTURE {value(snapshot, 'fixture')}",
        (
            "SNAPSHOT STALE"
            if stale else
            datetime.fromtimestamp(
                updated_at
            ).strftime("%H:%M:%S")
        ),
    )

    risk = snapshot.get("risk") or {}

    if exchange_stale:
        risk["resting_notional"] = None
        risk["available_budget"] = None

    risk_table = make_table(
        "RISK / EXPOSURE",
        "VALUE",
    )
    risk_table.add_row(
        "Antivist telemetry",
        (
            f"FRESH ({exchange_age:.2f}s)"
            if not exchange_stale and exchange_age is not None
            else "STALE / UNAVAILABLE"
        ),
    )
    risk_table.add_row(
        "Resting notional",
        value(risk, "resting_notional"),
    )
    risk_table.add_row(
        "Notional limit",
        value(risk, "notional_limit"),
    )
    risk_table.add_row(
        "Available budget",
        value(risk, "available_budget"),
    )
    risk_table.add_row(
        "Net position",
        value(risk, "net_position"),
    )
    risk_table.add_row(
        "Unhedged exposure",
        value(risk, "unhedged_exposure"),
    )
    risk_table.add_row(
        "Worst-case loss",
        value(risk, "worst_case_loss"),
    )

    orders = snapshot.get("orders") or {}

    if exchange_stale:
        orders["exchange"] = {}
        orders["pending"] = {}

    order_table = make_table(
        "ORDERS",
        "BUY",
        "SELL",
        "TOTAL",
    )

    for label, key in (
        ("Desired", "desired"),
        ("Exchange", "exchange"),
        ("Pending", "pending"),
    ):
        item = orders.get(key) or {}

        order_table.add_row(
            label,
            value(item, "buy"),
            value(item, "sell"),
            value(item, "total"),
        )

    venue_table = make_table(
        "VENUE",
        "STATUS",
        "FEED AGE",
    )

    for name, venue in (
        snapshot.get("venues") or {}
    ).items():
        venue_table.add_row(
            name.upper(),
            status_text(value(venue, "status")),
            value(venue, "age"),
        )

    pnl_table = make_table(
        "P&L",
        "REALIZED",
        "UPNL",
        "TOTAL",
    )

    pnl = snapshot.get("pnl") or {}

    for name in (
        "antivist",
        "polymarket",
        "pascal",
        "consolidated",
    ):
        item = pnl.get(name) or {}

        pnl_table.add_row(
            name.upper(),
            value(item, "realized"),
            value(item, "upnl"),
            value(item, "total"),
        )

    reconciliation = snapshot.get("reconciliation") or {}

    sync_table = make_table(
        "RECONCILIATION",
        "VALUE",
    )
    for label, key in (
        ("Last sync", "last_sync"),
        ("KEEP", "keep"),
        ("CANCEL", "cancel"),
        ("SUBMIT", "submit"),
        ("Latency", "latency_ms"),
    ):
        sync_table.add_row(
            label,
            value(reconciliation, key),
        )

    alerts = snapshot.get("alerts") or []

    alert_text = "\n".join(
        str(item) for item in alerts[-5:]
    ) or "No alerts reported"

    if exchange_stale:
        alert_text = (
            "ANTIVIST EXCHANGE TELEMETRY STALE\n"
            + alert_text
        )

    if stale:
        alert_text = (
            "STALE SNAPSHOT — DO NOT TRUST DISPLAYED STATE\n"
            + alert_text
        )

    content = Group(
        header,
        Text(""),
        risk_table,
        Text(""),
        order_table,
        Text(""),
        venue_table,
        Text(""),
        pnl_table,
        Text(""),
        sync_table,
        Text(""),
        Panel(
            alert_text,
            title="ALERTS",
            border_style="red" if stale else "dim",
        ),
    )

    return Panel(
        content,
        border_style="red" if stale else "white",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
    )
    args = parser.parse_args()

    console = Console()

    try:
        with Live(
            console=console,
            screen=True,
            refresh_per_second=4,
        ) as live:
            while True:
                snapshot = read_snapshot(args.file)
                live.update(
                    render(snapshot, args.file)
                )
                time.sleep(0.25)

    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
