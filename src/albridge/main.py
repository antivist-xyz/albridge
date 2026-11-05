"""Entry point for the Albridge market-data runtime."""

import argparse
import logging
import os

from dotenv import load_dotenv

from albridge.market_resolver import MarketResolver
from albridge.runtime import run_market_data

from albridge.market_rules import AntivistMarketRules

from pathlib import Path

from albridge.order_manager import parse_existing_orders
from albridge.order_registry import OrderRegistry
from albridge.logging_config import configure_logging

from albridge.risk.instance_lock import (
    AlbridgeAlreadyRunning,
    single_instance_lock,
)

from albridge.risk.emergency_halt import (
    assert_trading_enabled,
    EmergencyHalt,
)

LOGGER = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--market", default=None,
        help="Antivist team market slug (e.g. manchester-city)",
    )
    parser.add_argument(
        "--public-demo", action="store_true",
        help="Public read-only demo, without Antivist trading credentials",
    )
    parser.add_argument(
        "--market-config", default=None, metavar="JSON_PATH",
        help="Optional public Antivist market metadata JSON for --public-demo",
    )
    args = parser.parse_args()

    load_dotenv()
    configure_logging()

    market_slug = args.market or os.getenv("ANTIVIST_MARKET_SLUG")
    if not market_slug:
        parser.error("Specify --market or ANTIVIST_MARKET_SLUG")
    if args.market_config and not args.public_demo:
        parser.error("--market-config requires --public-demo")

    # Fail closed: no execution is possible in public mode, even when .env
    # contains LIVE settings left over from a private testnet session.
    if args.public_demo and (
        os.getenv("ALBRIDGE_EXECUTE_LIVE") == "1"
        or os.getenv("ALBRIDGE_TESTNET_SMOKE") == "1"
    ):
        parser.error(
            "Public demo refuses ALBRIDGE_EXECUTE_LIVE=1 or "
            "ALBRIDGE_TESTNET_SMOKE=1; set both to 0"
        )

    antivist = None
    registry = None
    existing_orders = ()
    registered_orders = ()
    execute_live = False
    execute_testnet_smoke = False

    if args.public_demo:
        from albridge.public_market import (
            DEFAULT_PUBLIC_BASE_URL,
            load_public_market,
        )
        antivist_market = load_public_market(
            slug=market_slug,
            base_url=os.getenv("ANTIVIST_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
            config_path=args.market_config,
        )
        print("ALBRIDGE EXECUTION MODE | PUBLIC DEMO (NO ORDERS)", flush=True)
    else:
        from antivist.client import AntivistClient

        antivist = AntivistClient(
            base_url=os.environ["ANTIVIST_BASE_URL"],
            api_key=os.environ["ANTIVIST_API_KEY"],
            api_secret=os.environ["ANTIVIST_API_SECRET"],
        )
        antivist_market = antivist.get_market(market_slug)
        market_id = antivist_market["id"]
        existing_orders = parse_existing_orders(
            response=antivist.get_orders(market_id=market_id)
        )
        database_path = Path("data/albridge.sqlite3")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        registry = OrderRegistry(database_path)
        registered_orders = registry.get_by_market(market_id=market_id)

        execute_testnet_smoke = os.getenv("ALBRIDGE_TESTNET_SMOKE") == "1"
        execute_live = os.getenv("ALBRIDGE_EXECUTE_LIVE") == "1"
        if execute_live:
            try:
                assert_trading_enabled()
            except EmergencyHalt as exc:
                raise SystemExit(str(exc)) from None
        print(
            f"ALBRIDGE EXECUTION MODE | {'LIVE' if execute_live else 'DRY RUN'}",
            flush=True,
        )

    try:
        market_id = antivist_market["id"]
        if type(market_id) is not int or market_id <= 0:
            raise ValueError("Invalid Antivist market_id")

        resolution = MarketResolver().resolve(antivist_market)
        market_rules = AntivistMarketRules.from_market(antivist_market)
        fixture = resolution.fixture

        LOGGER.info("ANTIVIST MARKET | %s", fixture.team_name)
        LOGGER.info("KICKOFF | %s", fixture.kickoff_at.isoformat())
        LOGGER.info("AVAILABLE VENUES | %s", resolution.available_venues)
        for venue, error in resolution.errors.items():
            LOGGER.warning("VENUE UNAVAILABLE | %s: %s", venue, error)
        if not resolution.available_venues:
            raise SystemExit("No external venues resolved for this fixture")

        with single_instance_lock():
            run_market_data(
                resolution,
                market_rules=market_rules,
                market_id=market_id,
                registered_orders=registered_orders,
                existing_orders=existing_orders,
                antivist_client=antivist,
                registry=registry,
                execute_testnet_smoke=execute_testnet_smoke,
                execute_live=execute_live,
                public_demo=args.public_demo,
            )
    except AlbridgeAlreadyRunning as exc:
        LOGGER.error("ALBRIDGE STARTUP BLOCKED | %s", exc)
        raise SystemExit(1) from None
    finally:
        if antivist is not None and hasattr(antivist, "close"):
            antivist.close()


if __name__ == "__main__":
    main()
