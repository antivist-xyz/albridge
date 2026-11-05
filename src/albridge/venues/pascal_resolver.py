"""Discover Pascal moneyline instruments for an Antivist fixture."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata
from decimal import Decimal

import httpx

from albridge.fixture import Fixture


PASCAL_MARKETS_URL = "https://data.pascal.trade/api/v1/markets"
KICKOFF_TOLERANCE_SECONDS = 300


@dataclass(frozen=True)
class ResolvedPascalMarket:
    game_id: str
    kickoff_at: datetime
    outcome_symbols: dict[str, str]
    fee_rates: dict[str, Decimal]

def normalize_name(value: str) -> str:
    """Normalize club names."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", value)


def matches_team(expected: str, actual: str) -> bool:
    """Allow common club-name prefixes without substring matching."""
    a = normalize_name(expected)
    b = normalize_name(actual)

    return bool(a and b and (a == b or a in b or b in a))


class PascalMarketResolver:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    def resolve(
        self,
        fixture: Fixture,
    ) -> ResolvedPascalMarket | None:
        """Find an unambiguous WIN/DRAW/LOSS instrument set."""

        client = self.client or httpx.Client(timeout=20)

        try:
            response = client.get(PASCAL_MARKETS_URL)
            response.raise_for_status()
            payload = response.json()
        finally:
            if self.client is None:
                client.close()

        markets = payload.get("data", [])

        if not isinstance(markets, list):
            raise ValueError("Unexpected Pascal markets response")

        grouped = {}

        for market in markets:
            attrs = market.get("display_attributes") or {}
            market_type = attrs.get("market_type") or {}
            game_page = attrs.get("game_page") or {}
            instrument = market_type.get("market") or {}
            teams = market_type.get("teams") or {}

            if market_type.get("kind") != "sports":
                continue

            if market_type.get("sport") != "soccer":
                continue

            if market_type.get("period") != "reg_time":
                continue

            if instrument.get("type") != "moneyline":
                continue

            if not matches_team(
                fixture.home_team,
                teams.get("home_name", ""),
            ):
                continue

            if not matches_team(
                fixture.away_team,
                teams.get("away_name", ""),
            ):
                continue

            start_ms = attrs.get("expected_event_start_time_ms")

            if start_ms is None:
                continue

            try:
                kickoff = datetime.fromtimestamp(
                    int(start_ms) / 1000,
                    tz=timezone.utc,
                )
            except (ValueError, TypeError, OverflowError):
                continue

            delta = abs(
                (kickoff - fixture.kickoff_at).total_seconds()
            )

            if delta > KICKOFF_TOLERANCE_SECONDS:
                continue

            game_id = game_page.get("game_id")
            symbol = market.get("symbol")
            outcome = instrument.get("outcome")

            if not game_id or not symbol:
                continue

            mapping = (
                {
                    "home": "WIN",
                    "draw": "DRAW",
                    "away": "LOSS",
                }
                if fixture.team_side == "home"
                else {
                    "away": "WIN",
                    "draw": "DRAW",
                    "home": "LOSS",
                }
            )

            role = mapping.get(outcome)

            if role is None:
                continue

            key = (game_id, kickoff)
            fee_rate_raw = market.get("taker_fee_rate")

            if fee_rate_raw is None:
                continue

            try:
                fee_rate = Decimal(str(fee_rate_raw))
            except (ValueError, TypeError):
                continue

            if not fee_rate.is_finite() or fee_rate < 0:
                continue

            grouped.setdefault(key, {}).setdefault(
                role, []
            ).append((symbol, fee_rate))

        complete = []

        for (game_id, kickoff), outcomes in grouped.items():
            if set(outcomes) != {"WIN", "DRAW", "LOSS"}:
                continue

            if any(len(symbols) != 1 for symbols in outcomes.values()):
                continue

            complete.append(
                ResolvedPascalMarket(
                    game_id=game_id,
                    kickoff_at=kickoff,
                    outcome_symbols={
                        role: entries[0][0]
                        for role, entries in outcomes.items()
                    },
                    fee_rates={
                        role: entries[0][1]
                        for role, entries in outcomes.items()
                    },
                )
            )

        # Do not select an ambiguous fixture automatically.
        return complete[0] if len(complete) == 1 else None
