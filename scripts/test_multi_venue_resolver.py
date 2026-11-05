"""Verify independent Pascal and Polymarket discovery."""

import os

from dotenv import load_dotenv
from antivist.client import AntivistClient

from albridge.fixture import fixture_from_antivist
from albridge.venues.pascal_resolver import PascalMarketResolver
from albridge.venues.polymarket_resolver import PolymarketMarketResolver


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

    print(f"\nANTIVIST | {fixture.home_team} vs {fixture.away_team}")
    print(f"KICKOFF  | {fixture.kickoff_at.isoformat()}")
    print(f"TEAM SIDE | {fixture.team_side}")

    pascal = PascalMarketResolver().resolve(fixture)
    polymarket = PolymarketMarketResolver().resolve(fixture)

    print("\nPASCAL")
    if pascal is None:
        print("NOT RESOLVED")
    else:
        print("GAME:", pascal.game_id)
        for outcome, symbol in pascal.outcome_symbols.items():
            print(f"  {outcome}: {symbol}")

    print("\nPOLYMARKET")
    if polymarket is None:
        print("NOT RESOLVED")
    else:
        print("EVENT:", polymarket.event_slug)
        for outcome, tokens in polymarket.outcome_tokens.items():
            print(f"  {outcome}:")
            print(f"    YES: {tokens['yes_token_id']}")
            print(f"    NO:  {tokens['no_token_id']}")

    print("\nAVAILABLE VENUES:", [
        name for name, result in (
            ("pascal", pascal),
            ("polymarket", polymarket),
        )
        if result is not None
    ])


if __name__ == "__main__":
    main()
