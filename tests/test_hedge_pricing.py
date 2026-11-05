
from decimal import Decimal

import pytest

from albridge.hedge_pricing import (
    HedgePrices,
    calculate_hedge_quotes, calculate_quotes_from_snapshot
)

from albridge.market_snapshot import MarketSnapshot, OutcomeBook
from albridge.hedge_pricing import anchors_from_pricing
from albridge.market_resolver import AntivistPricing

W = Decimal("1015")
D = Decimal("985")
L = Decimal("970")

PRICES = HedgePrices(
    win=Decimal("0.88"),
    draw=Decimal("0.08"),
    loss=Decimal("0.04"),
)


def test_fair_ask_and_bid():
    ask, bid = calculate_hedge_quotes(
        win_anchor=W,
        draw_anchor=D,
        loss_anchor=L,
        prices=PRICES,
    )

    assert ask.side == "SELL"
    assert ask.fair_entry == Decimal("1010.80")
    assert ask.hedge_quantities == {
        "WIN": Decimal("45"),
        "DRAW": Decimal("15"),
    }

    assert bid.side == "BUY"
    assert bid.fair_entry == Decimal("1010.80")
    assert bid.hedge_quantities == {
        "DRAW": Decimal("30"),
        "LOSS": Decimal("45"),
    }


@pytest.mark.parametrize(
    "outcome,terminal_mark",
    [
        ("WIN", W),
        ("DRAW", D),
        ("LOSS", L),
    ],
)
def test_terminal_pnl_is_zero(outcome, terminal_mark):
    ask, bid = calculate_hedge_quotes(
        win_anchor=W,
        draw_anchor=D,
        loss_anchor=L,
        prices=PRICES,
    )

    token_prices = {
        "WIN": PRICES.win,
        "DRAW": PRICES.draw,
        "LOSS": PRICES.loss,
    }

    for quote in (ask, bid):
        hedge_cost = sum(
            quantity * token_prices[token]
            for token, quantity in quote.hedge_quantities.items()
        )

        hedge_payout = quote.hedge_quantities.get(
            outcome, Decimal("0")
        )

        hedge_pnl = hedge_payout - hedge_cost

        if quote.side == "SELL":
            antivist_pnl = quote.fair_entry - terminal_mark
        else:
            antivist_pnl = terminal_mark - quote.fair_entry

        assert antivist_pnl + hedge_pnl == Decimal("0")


def test_rejects_invalid_anchor_order():
    with pytest.raises(ValueError):
        calculate_hedge_quotes(
            win_anchor=D,
            draw_anchor=W,
            loss_anchor=L,
            prices=PRICES,
        )


def test_rejects_invalid_prediction_price():
    with pytest.raises(ValueError):
        calculate_hedge_quotes(
            win_anchor=W,
            draw_anchor=D,
            loss_anchor=L,
            prices=HedgePrices(
                win=Decimal("1.1"),
                draw=Decimal("0.08"),
                loss=Decimal("0.04"),
            ),
        )


@pytest.mark.parametrize("venue", ["polymarket", "pascal"])
def test_quote_preserves_venue(venue):
    def make_book(outcome, ask):
        return OutcomeBook(
            outcome=outcome,
            symbol=f"{outcome}_YES",
            bids=(),
            asks=((Decimal(ask), Decimal("100")),),
            sequence=1,
            state_time_ms=1000,
        )

    snapshot = MarketSnapshot(
        venue=venue,
        outcomes={
            "WIN": make_book("WIN", "0.88"),
            "DRAW": make_book("DRAW", "0.08"),
            "LOSS": make_book("LOSS", "0.04"),
        },
    )

    result = calculate_quotes_from_snapshot(
        snapshot=snapshot,
        win_anchor=W,
        draw_anchor=D,
        loss_anchor=L,
    )

    assert result is not None
    ask, bid = result

    assert ask.venue == venue
    assert bid.venue == venue
    assert ask.fair_entry == Decimal("1010.80")
    assert bid.fair_entry == Decimal("1010.80")


def test_missing_liquidity_returns_none():
    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": OutcomeBook(
                outcome="WIN",
                symbol="WIN_YES",
                bids=(),
                asks=(),
                sequence=1,
                state_time_ms=1000,
            ),
            "DRAW": OutcomeBook(
                outcome="DRAW",
                symbol="DRAW_YES",
                bids=(),
                asks=((Decimal("0.08"), Decimal("100")),),
                sequence=1,
                state_time_ms=1000,
            ),
            "LOSS": OutcomeBook(
                outcome="LOSS",
                symbol="LOSS_YES",
                bids=(),
                asks=((Decimal("0.04"), Decimal("100")),),
                sequence=1,
                state_time_ms=1000,
            ),
        },
    )

    result = calculate_quotes_from_snapshot(
        snapshot=snapshot,
        win_anchor=W,
        draw_anchor=D,
        loss_anchor=L,
    )

    assert result is None

def test_anchors_from_antivist_pricing():
    pricing = AntivistPricing(
        oracle_anchor=Decimal("1000"),
        pf_win=Decimal("15"),
        pf_draw=Decimal("-15"),
        pf_loss=Decimal("-30"),
    )

    assert anchors_from_pricing(pricing) == (
        Decimal("1015"),
        Decimal("985"),
        Decimal("970"),
    )


from albridge.hedge_pricing import calculate_hedge_capacity


def test_hedge_capacity_from_best_ask():
    def book(outcome, size):
        return OutcomeBook(
            outcome=outcome,
            symbol=f"{outcome}_YES",
            bids=(),
            asks=((Decimal("0.10"), Decimal(size)),),
            sequence=1,
            state_time_ms=1000,
        )

    snapshot = MarketSnapshot(
        venue="polymarket",
        outcomes={
            "WIN": book("WIN", "450"),
            "DRAW": book("DRAW", "150"),
            "LOSS": book("LOSS", "90"),
        },
    )

    ask, bid = calculate_hedge_quotes(
        win_anchor=W,
        draw_anchor=D,
        loss_anchor=L,
        prices=PRICES,
    )

    ask_capacity = calculate_hedge_capacity(
        snapshot=snapshot,
        quote=ask,
    )

    bid_capacity = calculate_hedge_capacity(
        snapshot=snapshot,
        quote=bid,
    )

    assert ask_capacity is not None
    assert bid_capacity is not None

    # ASK: min(450/45, 150/15) = 10
    assert ask_capacity.max_antivist_quantity == Decimal("10")

    # BID: min(150/30, 90/45) = 2
    assert bid_capacity.max_antivist_quantity == Decimal("2")
    assert bid_capacity.limiting_outcome == "LOSS"

def test_fair_entry_includes_outcome_fees():
    ask, bid = calculate_hedge_quotes(
        win_anchor=Decimal("1030"),
        draw_anchor=Decimal("1000"),
        loss_anchor=Decimal("985"),
        prices=HedgePrices(
            win=Decimal("0.5"),
            draw=Decimal("0.5"),
            loss=Decimal("0.5"),
        ),
        fee_rates={
            "WIN": Decimal("0.05"),
            "DRAW": Decimal("0.02"),
            "LOSS": Decimal("0"),
        },
    )

    assert ask.fair_entry == Decimal("1015.6375")
    assert bid.fair_entry == Decimal("992.35")