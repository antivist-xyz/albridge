"""Unauthenticated market metadata for the public, non-trading demo.

The metadata is not an exchange order book. It only supplies the fixture,
reference anchors, and normalization rules for theoretical quotes.
"""

import json
from pathlib import Path
from urllib.parse import quote

import httpx

from albridge.fixture import fixture_from_antivist
from albridge.hedge_pricing import anchors_from_pricing
from albridge.market_resolver import AntivistPricing, optional_decimal
from albridge.market_rules import AntivistMarketRules


DEFAULT_PUBLIC_BASE_URL = "https://testnet.antivist.xyz"


def validate_public_market(market: object, *, expected_slug: str) -> dict:
    """Reject incomplete/mismatched metadata instead of making up prices."""
    if not isinstance(market, dict):
        raise ValueError("Public market metadata must be a JSON object")
    if not isinstance(market.get("team"), dict):
        raise ValueError("Missing team metadata")
    if market["team"].get("slug") != expected_slug:
        raise ValueError("Market slug does not match the requested team")
    if type(market.get("id")) is not int or market["id"] <= 0:
        raise ValueError("Missing or invalid market id")
    fixture_from_antivist(market)
    AntivistMarketRules.from_market(market)
    source = market.get("next_fixture")
    if not isinstance(source, dict):
        raise ValueError("Missing next_fixture")
    pricing = AntivistPricing(
        oracle_anchor=optional_decimal(source.get("oracle_anchor")),
        pf_win=optional_decimal(source.get("pf_win")),
        pf_draw=optional_decimal(source.get("pf_draw")),
        pf_loss=optional_decimal(source.get("pf_loss")),
    )
    anchors_from_pricing(pricing)
    return market


def load_public_market(*, slug: str, base_url: str, config_path: str | None = None) -> dict:
    """Fetch public metadata, or load an explicitly supplied local JSON export."""
    if not slug or "/" in slug or ".." in slug:
        raise ValueError("Invalid team market slug")

    if config_path:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    else:
        endpoint = f"{base_url.rstrip('/')}/api/markets/{quote(slug, safe='')}/"
        try:
            response = httpx.get(endpoint, timeout=10.0, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(
                "Public Antivist market metadata unavailable. "
                "Pass --market-config PATH with an exported public market JSON; "
                "no trading credentials are required."
            ) from exc

    # Accept either the public market object or a standard envelope.
    if isinstance(payload, dict) and isinstance(payload.get("market"), dict):
        payload = payload["market"]
    return validate_public_market(payload, expected_slug=slug)
