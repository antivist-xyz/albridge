"""Rich terminal renderer for multi-venue market depth."""

from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DEPTH = 5
OUTCOMES = ("WIN", "DRAW", "LOSS")


def format_price(value):
    if value is None:
        return "—"
    return f"{value:.4f}"


def format_size(value):
    if value is None:
        return "—"
    return f"{value:,.2f}"


def build_book_table(outcome_book):
    """Render a conventional price ladder with asks above bids."""

    table = Table(
        box=None,
        expand=True,
        show_edge=False,
        padding=(0, 0),
    )

    table.add_column("BID SIZE", justify="right", ratio=1)
    table.add_column("PRICE", justify="right", ratio=1)
    table.add_column("ASK SIZE", justify="right", ratio=1)

    if outcome_book is None:
        bids = []
        asks = []
    else:
        bids = list(outcome_book.bids[:DEPTH])
        asks = list(outcome_book.asks[:DEPTH])

    # Highest ask first; best ask nearest the spread.
    for price, size in reversed(asks):
        table.add_row(
            "",
            Text(format_price(price), style="red"),
            format_size(size),
        )

    # Preserve a consistent five-level ladder.
    for _ in range(DEPTH - len(asks)):
        table.add_row("", "—", "")

    spread = None

    if outcome_book is not None:
        spread = outcome_book.spread

    table.add_row(
        "",
        Text(
            f"SPREAD {format_price(spread)}",
            style="dim",
        ),
        "",
    )

    # Highest bid first; best bid nearest the spread.
    for price, size in bids:
        table.add_row(
            format_size(size),
            Text(format_price(price), style="green"),
            "",
        )

    for _ in range(DEPTH - len(bids)):
        table.add_row("", "—", "")

    return table


def build_venue_panel(venue, outcome, snapshot, health=None):
    """Render one venue book for one outcome."""

    outcome_book = (
        snapshot.get(outcome)
        if snapshot is not None
        else None
    )

    status = health.status if health is not None else "UNAVAILABLE"

    status_style = {
        "READY": "green",
        "SILENT": "yellow",
        "EMPTY": "yellow",
        "INITIALIZING": "yellow",
        "DISCONNECTED": "red",
    }.get(status, "dim")

    content = Group(
        Text(status, style=status_style),
        build_book_table(outcome_book),
    )

    return Panel(
        content,
        title=venue.upper(),
        border_style="bright_black",
        padding=(0, 0),
        expand=True,
    )


def build_dashboard(
    *,
    market_data,
    health_by_venue,
    fixture_label: str,
):
    """Render all six venue/outcome books horizontally."""

    books = Table.grid(
        expand=True,
        padding=0,
    )

    # One equally sized column for each order book.
    for _ in range(6):
        books.add_column(ratio=1)

    panels = []

    for outcome in OUTCOMES:
        for venue in ("pascal", "polymarket"):
            snapshot = getattr(market_data, venue)

            panel = build_venue_panel(
                venue,
                outcome,
                snapshot,
                health_by_venue.get(venue),
            )

            # Identify both outcome and venue on each panel.
            panel.title = f"{outcome} | {venue.upper()}"

            panels.append(panel)

    books.add_row(*panels)

    return Group(
        Text(
            f"ALBRIDGE / MARKET DATA | {fixture_label}",
            style="bold white",
        ),
        books,
    )