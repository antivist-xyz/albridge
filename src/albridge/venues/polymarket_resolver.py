"""Discover Polymarket contracts using only Antivist fixture identity."""

import json
import os
import re
import unicodedata
from datetime import datetime, timezone

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
from dotenv import load_dotenv


GAMMA_URL = "https://gamma-api.polymarket.com"
KICKOFF_TOLERANCE_SECONDS = 15 * 60

@dataclass(frozen=True)
class PolymarketFeeConfig:
    enabled: bool
    fee_type: str | None
    rate: Decimal
    exponent: int


def parse_polymarket_fees(market: dict) -> PolymarketFeeConfig:
    """Parse and validate market-specific Polymarket taker fees."""

    enabled = market.get("feesEnabled")

    if type(enabled) is not bool:
        raise ValueError("Missing or invalid feesEnabled")

    if not enabled:
        return PolymarketFeeConfig(
            enabled=False,
            fee_type=None,
            rate=Decimal("0"),
            exponent=1,
        )

    fee_type = market.get("feeType")
    schedule = market.get("feeSchedule")

    if fee_type != "sports_fees_v3":
        raise ValueError(f"Unsupported fee type: {fee_type}")

    if not isinstance(schedule, dict):
        raise ValueError("Missing Polymarket feeSchedule")

    try:
        rate = Decimal(str(schedule["rate"]))
        exponent = Decimal(str(schedule["exponent"]))
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("Invalid Polymarket feeSchedule") from exc

    if not rate.is_finite() or rate < 0:
        raise ValueError("Invalid Polymarket fee rate")

    # The current hedge pricing formula supports exponent 1.
    if exponent != Decimal("1"):
        raise ValueError(
            f"Unsupported Polymarket fee exponent: {exponent}"
        )

    if schedule.get("takerOnly") is not True:
        raise ValueError("Unsupported Polymarket fee configuration")

    return PolymarketFeeConfig(
        enabled=True,
        fee_type=fee_type,
        rate=rate,
        exponent=1,
    )

def normalize(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def name_matches(expected, actual):
    """Match club names while allowing common prefixes."""
    expected = normalize(expected)
    actual = normalize(actual)

    if not expected or not actual:
        return False

    return (
        expected == actual
        or f" {expected} " in f" {actual} "
        or f" {actual} " in f" {expected} "
    )


def parse_time(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []
    return []


def kickoff_matches(event, expected):
    """Require an explicit fixture kickoff, not market creation time."""
    times = [
        event.get("startTime"),
        *[
            market.get("gameStartTime")
            for market in event.get("markets") or []
        ],
    ]

    valid = [parse_time(value) for value in times]
    valid = [value for value in valid if value is not None]

    return bool(valid) and all(
        abs((value - expected).total_seconds())
        <= KICKOFF_TOLERANCE_SECONDS
        for value in valid
    )


def resolve_outcomes(event, home, away):
    """Resolve three distinct YES/NO moneyline contracts."""
    found = {}

    for market in event.get("markets") or []:
        if market.get("closed") is True:
            continue

        question = normalize(market.get("question") or "")
        outcomes = parse_array(market.get("outcomes"))
        tokens = parse_array(market.get("clobTokenIds"))

        if outcomes != ["Yes", "No"] or len(tokens) != 2:
            continue

        if not all(isinstance(token, str) and token for token in tokens):
            continue

        # Identify a team win or draw by its question.
        if "draw" in question and "end in a draw" in question:
            role = "DRAW"
        elif question.startswith("will ") and " win on " in question:
            team_name = question[5:question.index(" win on ")]

            if name_matches(home, team_name):
                role = "WIN"
            elif name_matches(away, team_name):
                role = "LOSS"
            else:
                continue
        else:
            continue

        if role in found:
            return None

        found[role] = {
            "condition_id": market.get("conditionId"),
            "yes_token_id": tokens[0],
            "no_token_id": tokens[1],
        }

    if set(found) != {"WIN", "DRAW", "LOSS"}:
        return None

    if any(not item["condition_id"] for item in found.values()):
        return None

    if len({item["condition_id"] for item in found.values()}) != 3:
        return None

    return found


def main():
    from antivist.client import AntivistClient

    load_dotenv()

    client = AntivistClient(
        base_url=os.environ["ANTIVIST_BASE_URL"],
        api_key=os.environ["ANTIVIST_API_KEY"],
        api_secret=os.environ["ANTIVIST_API_SECRET"],
    )

    try:
        market = client.get_market(
            os.environ["ANTIVIST_MARKET_SLUG"]
        )
    finally:
        if hasattr(client, "close"):
            client.close()

    fixture = market["next_fixture"]
    label = fixture["fixture_label"]

    teams = re.split(r"\s+vs\.?\s+", label, flags=re.IGNORECASE)
    if len(teams) != 2:
        raise ValueError(f"Unsupported fixture label: {label}")

    home, away = teams
    kickoff = parse_time(fixture["kickoff_at"])

    if kickoff is None:
        raise ValueError("Invalid Antivist kickoff")

    print("FIXTURE:", label)
    print("KICKOFF:", kickoff.isoformat())

    with httpx.Client(timeout=20) as http:
        response = http.get(
            f"{GAMMA_URL}/public-search",
            params={
                "q": f"{home} {away}",
                "limit_per_type": 50,
            },
        )
        response.raise_for_status()
        events = response.json().get("events", [])

    print("SEARCH CANDIDATES:", len(events))

    matches = []

    for event in events:
        title = event.get("title") or ""
        event_teams = re.split(
            r"\s+vs\.?\s+",
            title,
            flags=re.IGNORECASE,
        )

        if len(event_teams) != 2:
            continue

        if not (
            name_matches(home, event_teams[0])
            and name_matches(away, event_teams[1])
        ):
            continue

        if not kickoff_matches(event, kickoff):
            continue

        outcomes = resolve_outcomes(event, home, away)
        if outcomes is None:
            continue

        matches.append({
            "title": title,
            "slug": event.get("slug"),
            "outcomes": outcomes,
        })

    if len(matches) != 1:
        print("RESOLUTION FAILED | matches:", len(matches))
        return

    match = matches[0]
    print("\nPOLYMARKET RESOLVED:", match["title"])
    print("EVENT:", match["slug"])

    for role, instrument in match["outcomes"].items():
        print(f"\n{role}")
        print("  CONDITION:", instrument["condition_id"])
        print("  YES:", instrument["yes_token_id"])
        print("  NO:", instrument["no_token_id"])


if __name__ == "__main__":
    main()


from dataclasses import dataclass
from albridge.fixture import Fixture


@dataclass(frozen=True)
class ResolvedPolymarket:
    event_slug: str
    outcome_tokens: dict[str, dict[str, str]]
    fee_configs: dict[str, PolymarketFeeConfig]

class PolymarketMarketResolver:
    """Discover Polymarket instruments independently of Antivist oracle sources."""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    def resolve(self, fixture: Fixture) -> ResolvedPolymarket | None:
        """Find exactly one matching three-outcome football event."""

        client = self.client or httpx.Client(timeout=20)

        try:
            response = client.get(
                f"{GAMMA_URL}/public-search",
                params={
                    "q": f"{fixture.home_team} {fixture.away_team}",
                    "limit_per_type": 50,
                },
            )
            response.raise_for_status()
            events = response.json().get("events", [])
        finally:
            if self.client is None:
                client.close()

        matches = []

        for event in events:
            title = event.get("title") or ""

            event_teams = re.split(
                r"\s+vs\.?\s+",
                title,
                flags=re.IGNORECASE,
            )

            if len(event_teams) != 2:
                continue

            if not (
                name_matches(fixture.home_team, event_teams[0])
                and name_matches(fixture.away_team, event_teams[1])
            ):
                continue

            if not kickoff_matches(event, fixture.kickoff_at):
                continue

            outcomes = resolve_outcomes(
                event,
                fixture.home_team,
                fixture.away_team,
            )

            if outcomes is None:
                continue

            # Existing parser maps WIN to home and LOSS to away.
            # Reverse those roles when Antivist's team plays away.
            if fixture.team_side == "away":
                outcomes = {
                    "WIN": outcomes["LOSS"],
                    "DRAW": outcomes["DRAW"],
                    "LOSS": outcomes["WIN"],
                }

            # Resolve fee configuration for each selected outcome.
            # Match by condition_id, never by market position or name.

            fee_configs = {}

            for role, instrument in outcomes.items():
                condition_id = instrument["condition_id"]

                matching_markets = [
                    market
                    for market in event.get("markets", [])
                    if market.get("conditionId") == condition_id
                ]

                if len(matching_markets) != 1:
                    raise ValueError(
                        f"Ambiguous Polymarket fee metadata: {role}"
                    )

                fee_configs[role] = parse_polymarket_fees(
                    matching_markets[0]
                )

            matches.append(
                ResolvedPolymarket(
                    event_slug=event["slug"],
                    outcome_tokens=outcomes,
                    fee_configs=fee_configs,
                )
            )

        # Fail closed rather than choose between ambiguous events.
        return matches[0] if len(matches) == 1 else None
