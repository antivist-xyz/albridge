from decimal import Decimal

import pytest

from albridge.venues.polymarket_resolver import (
    parse_polymarket_fees,
)


def test_sports_fees_v3():
    config = parse_polymarket_fees({
        "feesEnabled": True,
        "feeType": "sports_fees_v3",
        "feeSchedule": {
            "exponent": 1,
            "rate": 0.05,
            "takerOnly": True,
            "rebateRate": 0.15,
        },
    })

    assert config.enabled is True
    assert config.rate == Decimal("0.05")
    assert config.exponent == 1


def test_fees_disabled():
    config = parse_polymarket_fees({
        "feesEnabled": False,
    })

    assert config.rate == Decimal("0")


def test_unknown_fee_schedule_is_rejected():
    with pytest.raises(ValueError, match="exponent"):
        parse_polymarket_fees({
            "feesEnabled": True,
            "feeType": "sports_fees_v3",
            "feeSchedule": {
                "exponent": 2,
                "rate": 0.05,
                "takerOnly": True,
            },
        })


def test_missing_fee_metadata_is_rejected():
    with pytest.raises(ValueError, match="feesEnabled"):
        parse_polymarket_fees({})