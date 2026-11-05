
from dataclasses import dataclass
from decimal import Decimal
from dataclasses import replace

from albridge.market_snapshot import MarketSnapshot


@dataclass(frozen=True)
class HedgePrices:
    win: Decimal
    draw: Decimal
    loss: Decimal


@dataclass(frozen=True)
class HedgeQuote:
    side: str
    fair_entry: Decimal
    hedge_quantities: dict[str, Decimal]
    venue: str | None = None


def calculate_hedge_quotes(
    *,
    win_anchor: Decimal,
    draw_anchor: Decimal,
    loss_anchor: Decimal,
    prices: HedgePrices,
    fee_rates: dict[str, Decimal] | None = None,
) -> tuple[HedgeQuote, HedgeQuote]:
    """
    Calculate theoretical Antivist BID/ASK prices using
    complete terminal payoff hedges on prediction markets.

    Assumptions:
    - One Antivist contract.
    - WIN, DRAW and LOSS are mutually exclusive and exhaustive.
    - Antivist terminal prices equal the supplied anchors.
    - Prediction market tokens settle at 0 or 1.
    - No fees, funding or slippage.
    """

    if not loss_anchor < draw_anchor < win_anchor:
        raise ValueError("Expected LOSS < DRAW < WIN")

    if fee_rates is None:
        fee_rates = {
            "WIN": Decimal("0"),
            "DRAW": Decimal("0"),
            "LOSS": Decimal("0"),
        }

    if set(fee_rates) != {"WIN", "DRAW", "LOSS"}:
        raise ValueError("Incomplete hedge fee configuration")

    if any(
        not rate.is_finite() or rate < 0
        for rate in fee_rates.values()
    ):
        raise ValueError("Invalid hedge fee rate")

    for price in (prices.win, prices.draw, prices.loss):
        if not Decimal("0") <= price <= Decimal("1"):
            raise ValueError("Invalid prediction market price")

    def effective_price(outcome: str, price: Decimal) -> Decimal:
        rate = fee_rates[outcome]
        return price + rate * price * (Decimal("1") - price)

    # ASK: Albridge SHORT Antivist.
    # Hedge by purchasing WIN YES and DRAW YES.
    ask_win_qty = win_anchor - loss_anchor
    ask_draw_qty = draw_anchor - loss_anchor

    fair_ask = (
        loss_anchor
        + ask_win_qty * effective_price("WIN", prices.win)
        + ask_draw_qty * effective_price("DRAW", prices.draw)
    )

    # BID: Albridge LONG Antivist.
    # Hedge by purchasing DRAW YES and LOSS YES.
    bid_draw_qty = win_anchor - draw_anchor
    bid_loss_qty = win_anchor - loss_anchor

    fair_bid = (
        win_anchor
        - bid_draw_qty * effective_price("DRAW", prices.draw)
        - bid_loss_qty * effective_price("LOSS", prices.loss)
    )

    return (
        HedgeQuote(
            side="SELL",
            fair_entry=fair_ask,
            hedge_quantities={
                "WIN": ask_win_qty,
                "DRAW": ask_draw_qty,
            },
        ),
        HedgeQuote(
            side="BUY",
            fair_entry=fair_bid,
            hedge_quantities={
                "DRAW": bid_draw_qty,
                "LOSS": bid_loss_qty,
            },
        ),
    )


def calculate_quotes_from_snapshot(
    *,
    snapshot: MarketSnapshot,
    win_anchor: Decimal,
    draw_anchor: Decimal,
    loss_anchor: Decimal,
    fee_rates: dict[str, Decimal] | None = None,
) -> tuple[HedgeQuote, HedgeQuote] | None:
    """
    Calculate theoretical hedge quotes from a normalized venue snapshot.

    Venue provenance is preserved in each result.
    Uses best ask prices only; execution depth is not yet evaluated.
    """

    try:
        win_ask = snapshot.get("WIN").best_ask
        draw_ask = snapshot.get("DRAW").best_ask
        loss_ask = snapshot.get("LOSS").best_ask
    except KeyError:
        return None

    if any(
        price is None
        for price in (win_ask, draw_ask, loss_ask)
    ):
        return None

    ask, bid = calculate_hedge_quotes(
        win_anchor=win_anchor,
        draw_anchor=draw_anchor,
        loss_anchor=loss_anchor,
        prices=HedgePrices(
            win=win_ask,
            draw=draw_ask,
            loss=loss_ask,
        ),
        fee_rates=fee_rates,
    )

    return (
        replace(ask, venue=snapshot.venue),
        replace(bid, venue=snapshot.venue),
    )


def anchors_from_pricing(pricing) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate scenario anchors from public Antivist pricing metadata."""

    values = (
        pricing.oracle_anchor,
        pricing.pf_win,
        pricing.pf_draw,
        pricing.pf_loss,
    )

    if any(value is None for value in values):
        raise ValueError("Incomplete Antivist pricing metadata")

    oracle_anchor = pricing.oracle_anchor

    win = oracle_anchor + pricing.pf_win
    draw = oracle_anchor + pricing.pf_draw
    loss = oracle_anchor + pricing.pf_loss

    if not all(value.is_finite() for value in (win, draw, loss)):
        raise ValueError("Non-finite Antivist anchors")

    if not loss < draw < win:
        raise ValueError("Expected LOSS < DRAW < WIN")

    return win, draw, loss


@dataclass(frozen=True)
class HedgeCapacity:
    venue: str
    side: str
    max_antivist_quantity: Decimal
    limiting_outcome: str


def calculate_hedge_capacity(
    *,
    snapshot: MarketSnapshot,
    quote: HedgeQuote,
) -> HedgeCapacity | None:
    """
    Calculate maximum Antivist quantity hedgeable
    using only the first ask level of each required token.

    Assumes book sizes are denominated in outcome shares.
    Does not reserve liquidity or account for concurrent quotes.
    """

    capacities = {}

    for outcome, hedge_ratio in quote.hedge_quantities.items():
        if hedge_ratio <= 0:
            raise ValueError("Hedge ratio must be positive")

        try:
            book = snapshot.get(outcome)
        except KeyError:
            return None

        if not book.asks:
            return None

        available_size = book.asks[0][1]

        if available_size <= 0:
            return None

        capacities[outcome] = available_size / hedge_ratio

    limiting_outcome = min(capacities, key=capacities.get)

    return HedgeCapacity(
        venue=snapshot.venue,
        side=quote.side,
        max_antivist_quantity=capacities[limiting_outcome],
        limiting_outcome=limiting_outcome,
    )

def build_fee_debug(
    *,
    side: str,
    prices: HedgePrices,
    hedge_quantities: dict[str, Decimal],
    fee_rates: dict[str, Decimal],
    fair_entry: Decimal,
) -> dict:
    """Build diagnostic fee breakdown per Antivist unit."""

    pm_prices = {
        "WIN": prices.win,
        "DRAW": prices.draw,
        "LOSS": prices.loss,
    }

    legs = []
    raw_cost = Decimal("0")
    total_fee = Decimal("0")

    for outcome, quantity in hedge_quantities.items():
        price = pm_prices[outcome]
        rate = fee_rates[outcome]

        fee = quantity * price * (Decimal("1") - price) * rate
        raw_cost += quantity * price
        total_fee += fee

        legs.append({
            "outcome": outcome,
            "pm_price": price,
            "quantity": quantity,
            "fee_rate": rate,
            "fee": fee,
        })

    raw_entry = (
        fair_entry - total_fee
        if side == "SELL"
        else fair_entry + total_fee
    )

    return {
        "side": side,
        "legs": legs,
        "raw_entry": raw_entry,
        "total_fee": total_fee,
        "adjusted_entry": fair_entry,
    }