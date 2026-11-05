from datetime import datetime, timezone

import pytest

from albridge.fixture import fixture_from_antivist


def make_market(label):
    return {
        "team": {
            "id": 115,
            "name": "Barcelona",
            "slug": "barcelona",
            "aliases": ["Barcelona", "FC Barcelona"],
        },
        "next_fixture": {
            "fixture_label": label,
            "kickoff_at": "2026-10-31T20:00:00+00:00",
        },
    }


def test_home_fixture():
    fixture = fixture_from_antivist(
        make_market("Barcelona vs Alaves")
    )

    assert fixture.team_side == "home"
    assert fixture.win_side == "home"
    assert fixture.loss_side == "away"
    assert fixture.team_slug == "barcelona"
    assert fixture.kickoff_at == datetime(
        2026, 10, 31, 20, 0, tzinfo=timezone.utc
    )


def test_away_fixture():
    fixture = fixture_from_antivist(
        make_market("Alaves vs FC Barcelona")
    )

    assert fixture.team_side == "away"
    assert fixture.win_side == "away"
    assert fixture.loss_side == "home"


def test_missing_team_rejected():
    with pytest.raises(ValueError):
        fixture_from_antivist(
            make_market("Real Madrid vs Alaves")
        )


def test_ambiguous_team_rejected():
    with pytest.raises(ValueError):
        fixture_from_antivist(
            make_market("Barcelona vs FC Barcelona")
        )
