"""Run standalone Polymarket market discovery."""

# Re-export existing functions for backward-compatible tests.
from albridge.venues.polymarket_resolver import (
    kickoff_matches,
    name_matches,
    normalize,
    parse_array,
    parse_time,
    resolve_outcomes,
    main,
)


if __name__ == "__main__":
    main()
