"""Safety tests for Polymarket fixture discovery."""

from datetime import datetime, timezone

from scripts.discover_polymarket import (
    kickoff_matches,
    name_matches,
    resolve_outcomes,
)


KICKOFF = datetime(
    2026, 10, 31, 20, 0, tzinfo=timezone.utc
)


def make_market(question, condition):
    return {
        "question": question,
        "conditionId": condition,
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["token-yes", "token-no"]',
        "closed": False,
    }


def test_team_aliases():
    assert name_matches("Barcelona", "FC Barcelona")
    assert name_matches("Alaves", "Deportivo Alavés")
    assert not name_matches("Barcelona", "Real Madrid")


def test_correct_kickoff():
    event = {"startTime": "2026-10-31T20:00:00Z"}
    assert kickoff_matches(event, KICKOFF)


def test_wrong_kickoff():
    event = {"startTime": "2026-11-07T20:00:00Z"}
    assert not kickoff_matches(event, KICKOFF)


def test_creation_date_is_not_kickoff():
    event = {"startDate": "2026-10-18T06:00:37Z"}
    assert not kickoff_matches(event, KICKOFF)


def test_complete_moneyline():
    event = {
        "markets": [
            make_market(
                "Will FC Barcelona win on 2026-10-31?",
                "condition-win",
            ),
            make_market(
                "Will FC Barcelona vs. Deportivo Alavés end in a draw?",
                "condition-draw",
            ),
            make_market(
                "Will Deportivo Alavés win on 2026-10-31?",
                "condition-loss",
            ),
        ],
    }

    result = resolve_outcomes(event, "Barcelona", "Alaves")

    assert result is not None
    assert set(result) == {"WIN", "DRAW", "LOSS"}
    assert result["WIN"]["yes_token_id"] == "token-yes"


def test_incomplete_moneyline():
    event = {
        "markets": [
            make_market(
                "Will FC Barcelona win on 2026-10-31?",
                "condition-win",
            )
        ],
    }

    assert resolve_outcomes(
        event, "Barcelona", "Alaves"
    ) is None
