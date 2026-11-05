"""Test independent Pascal market discovery."""

import os

from dotenv import load_dotenv
from antivist.client import AntivistClient

from albridge.fixture import fixture_from_antivist
from albridge.venues.pascal_resolver import PascalMarketResolver


def main():
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

    fixture = fixture_from_antivist(market)

    print("FIXTURE:", fixture.home_team, "vs", fixture.away_team)
    print("KICKOFF:", fixture.kickoff_at.isoformat())
    print("ANTIVIST TEAM SIDE:", fixture.team_side)

    result = PascalMarketResolver().resolve(fixture)

    if result is None:
        print("PASCAL | NOT RESOLVED")
        return

    print("PASCAL | RESOLVED")
    print("GAME ID:", result.game_id)

    for outcome in ("WIN", "DRAW", "LOSS"):
        print(f"{outcome}: {result.outcome_symbols[outcome]}")


if __name__ == "__main__":
    main()
