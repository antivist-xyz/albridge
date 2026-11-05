"""The public demonstration must not require trading credentials."""

import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from albridge.public_market import load_public_market, validate_public_market


MARKET = {
    "id": 4,
    "team": {
        "id": 11, "slug": "manchester-city", "name": "Manchester City",
        "aliases": ["Man City"],
    },
    "next_fixture": {
        "fixture_label": "Nottingham Forest vs Manchester City",
        "kickoff_at": "2026-11-06T19:00:00Z",
        "oracle_anchor": "1000", "pf_win": "15", "pf_draw": "-15",
        "pf_loss": "-30",
    },
    "tick_size": "0.01", "quantity_step": "0.001", "min_quantity": "0.01",
}


def test_public_market_from_local_export(tmp_path):
    path = tmp_path / "market.json"
    path.write_text(json.dumps({"market": MARKET}), encoding="utf-8")
    result = load_public_market(slug="manchester-city", base_url="http://unused", config_path=str(path))
    assert result == MARKET


def test_public_market_rejects_wrong_team():
    with pytest.raises(ValueError, match="slug"):
        validate_public_market(MARKET, expected_slug="barcelona")


def test_public_market_rejects_missing_anchors():
    market = {**MARKET, "next_fixture": {**MARKET["next_fixture"], "pf_loss": None}}
    with pytest.raises(ValueError):
        validate_public_market(market, expected_slug="manchester-city")


def test_public_market_fetches_without_credentials(monkeypatch):
    import albridge.public_market as pm

    def fake_get(url, *, timeout, follow_redirects):
        assert url.endswith("/api/markets/manchester-city/")
        assert follow_redirects is False
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: MARKET)

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    assert load_public_market(slug="manchester-city", base_url="https://testnet.example") == MARKET


def test_public_demo_never_starts_execution(monkeypatch, capsys):
    import albridge.main as main_module
    import albridge.public_market as pm

    monkeypatch.setenv("ALBRIDGE_EXECUTE_LIVE", "0")
    monkeypatch.setenv("ALBRIDGE_TESTNET_SMOKE", "0")
    monkeypatch.delenv("ANTIVIST_API_KEY", raising=False)
    monkeypatch.delenv("ANTIVIST_API_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["albridge.main", "--public-demo", "--market", "manchester-city"])
    monkeypatch.setattr(pm, "load_public_market", lambda **kw: MARKET)
    fake_resolution = SimpleNamespace(
        fixture=SimpleNamespace(team_name="Manchester City", kickoff_at=SimpleNamespace(isoformat=lambda: "2026-11-06T19:00:00Z")),
        available_venues=("pascal",), errors={},
    )
    monkeypatch.setattr(main_module.MarketResolver, "resolve", lambda self, market: fake_resolution)
    monkeypatch.setattr(main_module, "single_instance_lock", nullcontext)
    received = {}
    monkeypatch.setattr(main_module, "run_market_data", lambda *args, **kwargs: received.update(kwargs))
    main_module.main()
    assert received["public_demo"] is True
    assert received["execute_live"] is False
    assert received["execute_testnet_smoke"] is False
    assert received["antivist_client"] is None
    assert received["registry"] is None
    assert received["existing_orders"] == ()
    assert "PUBLIC DEMO" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["ALBRIDGE_EXECUTE_LIVE", "ALBRIDGE_TESTNET_SMOKE"])
def test_public_demo_rejects_execution_flags(monkeypatch, flag):
    import albridge.main as main_module
    monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(sys, "argv", ["albridge.main", "--public-demo", "--market", "manchester-city"])
    with pytest.raises(SystemExit) as caught:
        main_module.main()
    assert caught.value.code == 2
