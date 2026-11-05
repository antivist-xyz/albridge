
"""Build theoretical Antivist quote ladders from external ask depth."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.market_snapshot import MarketSnapshot


ZERO = Decimal("0")


@dataclass(frozen=True)
class HedgeLeg:
    outcome: str
    pm_price: Decimal
    pm_quantity: Decimal
    instrument_id: str | None = None
    execution_side: str = "BUY"

@dataclass(frozen=True)
class HedgeLadderLevel:
    venue: str
    side: str
    price: Decimal
    quantity: Decimal
    hedge_legs: tuple[HedgeLeg, ...]


def _build_side(
    *,
    snapshot: MarketSnapshot,
    side: str,
    base_anchor: Decimal,
    ratios: dict[str, Decimal],
    max_levels: int,
    fee_rates: Decimal,
) -> tuple[HedgeLadderLevel, ...]:
    """Walk required PM ask books, consuming depth virtually."""

    if set(fee_rates) != set(ratios):
        raise ValueError("Fee rates must match required hedge outcomes")

    if any(
        not rate.is_finite() or rate < ZERO
        for rate in fee_rates.values()
    ):
        raise ValueError("Invalid taker fee rate")

    if max_levels < 1:
        raise ValueError("max_levels must be positive")

    books = {}

    for outcome, ratio in ratios.items():
        if ratio <= ZERO:
            raise ValueError("Hedge ratios must be positive")

        try:
            asks = snapshot.get(outcome).asks
        except KeyError:
            return ()

        if not asks:
            return ()

        # The cheapest executable asks must be consumed first.
        levels = sorted(asks, key=lambda item: item[0])

        if any(
            not price.is_finite()
            or not size.is_finite()
            or price < ZERO
            or price > Decimal("1")
            or size <= ZERO
            for price, size in levels
        ):
            raise ValueError("Invalid PM ask level")

        books[outcome] = levels

    indices = {outcome: 0 for outcome in ratios}
    remaining = {
        outcome: books[outcome][0][1]
        for outcome in ratios
    }

    result = []

    while len(result) < max_levels:
        # Stop when any required hedge book runs out.
        if any(
            indices[outcome] >= len(books[outcome])
            for outcome in ratios
        ):
            break

        capacities = {
            outcome: remaining[outcome] / ratio
            for outcome, ratio in ratios.items()
        }

        limiting_outcome = min(capacities, key=capacities.get)
        quantity = capacities[limiting_outcome]

        if quantity <= ZERO:
            break

        hedge_legs = tuple(
            HedgeLeg(
                outcome=outcome,
                pm_price=books[outcome][indices[outcome]][0],
                pm_quantity=quantity * ratio,
                instrument_id=snapshot.get(outcome).symbol,
            )
            for outcome, ratio in ratios.items()
        )

        cost_per_antivist = sum(
            (
                (leg.pm_quantity / quantity)
                * (
                    leg.pm_price
                    + fee_rates[leg.outcome]
                    * leg.pm_price
                    * (Decimal("1") - leg.pm_price)
                )
                for leg in hedge_legs
            ),
            ZERO,
        )

        if side == "SELL":
            price = base_anchor + cost_per_antivist
        else:
            price = base_anchor - cost_per_antivist

        result.append(
            HedgeLadderLevel(
                venue=snapshot.venue,
                side=side,
                price=price,
                quantity=quantity,
                hedge_legs=hedge_legs,
            )
        )

        # Advance only the books whose current ask is exhausted.
        for outcome, ratio in ratios.items():
            # The limiting book is exhausted by construction.
            if outcome == limiting_outcome:
                remaining[outcome] = ZERO
            else:
                remaining[outcome] -= quantity * ratio

                # Remove insignificant Decimal rounding residuals.
                if abs(remaining[outcome]) < Decimal("1e-18"):
                    remaining[outcome] = ZERO

            if remaining[outcome] == ZERO:
                indices[outcome] += 1

                if indices[outcome] < len(books[outcome]):
                    remaining[outcome] = (
                        books[outcome][indices[outcome]][1]
                    )
    return tuple(result)



def build_hedge_ladders(
    *,
    snapshot: MarketSnapshot,
    win_anchor: Decimal,
    draw_anchor: Decimal,
    loss_anchor: Decimal,
    max_levels: int = 20,
    sell_snapshot: MarketSnapshot | None = None,
    buy_snapshot: MarketSnapshot | None = None,
    fee_rates: dict[str, Decimal] | None = None,
) -> tuple[
    tuple[HedgeLadderLevel, ...],
    tuple[HedgeLadderLevel, ...],
]:
    """
    Return theoretical SELL and BUY Antivist ladders.

    Optional side-specific snapshots allow risk allocation
    before ladder construction.

    Without overrides, both sides use the original snapshot.
    """

    if not loss_anchor < draw_anchor < win_anchor:
        raise ValueError("Expected LOSS < DRAW < WIN")

    if fee_rates is None:
        # Legacy behavior for existing unit tests.
        fee_rates = {
            "WIN": ZERO,
            "DRAW": ZERO,
            "LOSS": ZERO,
        }

    if set(fee_rates) != {"WIN", "DRAW", "LOSS"}:
        raise ValueError("Incomplete hedge fee configuration")

    sell_source = (
        sell_snapshot
        if sell_snapshot is not None
        else snapshot
    )

    buy_source = (
        buy_snapshot
        if buy_snapshot is not None
        else snapshot
    )

    if (
        sell_source.venue != snapshot.venue
        or buy_source.venue != snapshot.venue
    ):
        raise ValueError("Cannot mix venues in hedge ladders")

    sell_levels = _build_side(
        snapshot=sell_source,
        side="SELL",
        base_anchor=loss_anchor,
        ratios={
            "WIN": win_anchor - loss_anchor,
            "DRAW": draw_anchor - loss_anchor,
        },
        max_levels=max_levels,
        fee_rates={
            "WIN": fee_rates["WIN"],
            "DRAW": fee_rates["DRAW"],
        },
    )

    buy_levels = _build_side(
        snapshot=buy_source,
        side="BUY",
        base_anchor=win_anchor,
        ratios={
            "DRAW": win_anchor - draw_anchor,
            "LOSS": win_anchor - loss_anchor,
        },
        max_levels=max_levels,
        fee_rates={
            "DRAW": fee_rates["DRAW"],
            "LOSS": fee_rates["LOSS"],
        },
    )

    return sell_levels, buy_levels