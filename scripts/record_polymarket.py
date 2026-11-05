"""Record Polymarket data using dynamically resolved fixture tokens."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from antivist.client import AntivistClient

from albridge.market_resolver import MarketResolver
from albridge.venues.polymarket import PolymarketWebSocketClient


def main():
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    antivist = AntivistClient(
        base_url=os.environ["ANTIVIST_BASE_URL"],
        api_key=os.environ["ANTIVIST_API_KEY"],
        api_secret=os.environ["ANTIVIST_API_SECRET"],
    )

    try:
        market = antivist.get_market(
            os.environ["ANTIVIST_MARKET_SLUG"]
        )
    finally:
        if hasattr(antivist, "close"):
            antivist.close()

    resolution = MarketResolver().resolve(market)

    if resolution.polymarket is None:
        raise SystemExit(
            "Polymarket market not resolved: "
            + resolution.errors.get("polymarket", "unknown")
        )

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )

    path = Path(
        f"recordings/polymarket_{timestamp}.jsonl"
    )

    print("FIXTURE:", resolution.fixture.home_team,
          "vs", resolution.fixture.away_team)
    print("POLYMARKET:", resolution.polymarket.event_slug)
    print("RECORDER:", path)

    client = PolymarketWebSocketClient(
        outcome_tokens=resolution.polymarket.outcome_tokens,
        recording_path=path,
    )

    try:
        client.start()
    except KeyboardInterrupt:
        client.stop()


if __name__ == "__main__":
    main()
