"""Inspect public Antivist market metadata for venue resolution."""

import json
import os
import sys
from dotenv import load_dotenv
from antivist.client import AntivistClient



def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/inspect_antivist_market.py MARKET_SLUG"
        )

    load_dotenv()
    client = AntivistClient(
        base_url=os.environ["ANTIVIST_BASE_URL"],
        api_key=os.environ["ANTIVIST_API_KEY"],
        api_secret=os.environ["ANTIVIST_API_SECRET"],
    )

    try:
        market = client.get_market(sys.argv[1])

        # Only output public market metadata, never credentials.
        result = {
            "market_id": market.get("id"),
            "slug": market.get("slug"),
            "team": market.get("team"),
            "next_fixture": market.get("next_fixture"),
            "oracle_sources": market.get("oracle_sources"),
        }

        print(json.dumps(result, indent=2, default=str))

    finally:
        client.close() if hasattr(client, "close") else None


if __name__ == "__main__":
    main()
