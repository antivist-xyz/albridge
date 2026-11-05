"""Normalized fixture identity supplied by Antivist."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata


def normalize_team_name(value: str) -> str:
    """Normalize team names for identity comparison."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", value)


@dataclass(frozen=True)
class Fixture:
    team_id: int
    team_slug: str
    team_name: str
    team_aliases: tuple[str, ...]
    home_team: str
    away_team: str
    kickoff_at: datetime
    team_side: str

    @property
    def win_side(self) -> str:
        return self.team_side

    @property
    def loss_side(self) -> str:
        return "away" if self.team_side == "home" else "home"


def fixture_from_antivist(market: dict) -> Fixture:
    """Build a fixture using public Antivist market metadata."""

    team = market["team"]
    next_fixture = market["next_fixture"]

    label = next_fixture["fixture_label"]
    parts = re.split(
        r"\s+vs\.?\s+",
        label,
        flags=re.IGNORECASE,
    )

    if len(parts) != 2:
        raise ValueError(f"Unsupported fixture label: {label}")

    home, away = (part.strip() for part in parts)

    kickoff = datetime.fromisoformat(
        next_fixture["kickoff_at"].replace("Z", "+00:00")
    )

    if kickoff.tzinfo is None:
        raise ValueError("Fixture kickoff must have a timezone")

    kickoff = kickoff.astimezone(timezone.utc)

    aliases = tuple(dict.fromkeys(
        [team["name"], *team.get("aliases", [])]
    ))

    normalized_aliases = {
        normalize_team_name(alias) for alias in aliases
    }

    home_matches = (
        normalize_team_name(home) in normalized_aliases
    )
    away_matches = (
        normalize_team_name(away) in normalized_aliases
    )

    if home_matches == away_matches:
        raise ValueError(
            "Cannot unambiguously identify the Antivist team "
            "in the fixture"
        )

    return Fixture(
        team_id=team["id"],
        team_slug=team["slug"],
        team_name=team["name"],
        team_aliases=aliases,
        home_team=home,
        away_team=away,
        kickoff_at=kickoff,
        team_side="home" if home_matches else "away",
    )
