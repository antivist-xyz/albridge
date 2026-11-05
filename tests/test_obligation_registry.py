
from decimal import Decimal

from albridge.risk.hedge_obligation import HedgeObligation
from albridge.risk.obligation_registry import ObligationRegistry


def test_obligations_survive_restart_without_duplicates(tmp_path):
    database = tmp_path / "albridge.sqlite3"

    obligations = (
        HedgeObligation(
            execution_id=456,
            client_order_id="albridge-test-001",
            venue="polymarket",
            instrument_id="draw-token",
            outcome="DRAW",
            execution_side="BUY",
            pm_price=Decimal("0.40"),
            quantity=Decimal("60"),
        ),
        HedgeObligation(
            execution_id=456,
            client_order_id="albridge-test-001",
            venue="polymarket",
            instrument_id="loss-token",
            outcome="LOSS",
            execution_side="BUY",
            pm_price=Decimal("0.25"),
            quantity=Decimal("90"),
        ),
    )

    registry = ObligationRegistry(database)
    registry.register(obligations=obligations)

    # Simulate a restart and reprocessing the same fill.
    restarted = ObligationRegistry(database)
    restarted.register(obligations=obligations)

    pending = restarted.get_pending()

    assert len(pending) == 2

    assert pending[0].execution_id == 456
    assert pending[0].leg_index == 0
    assert pending[0].quantity == Decimal("60")

    assert pending[1].execution_id == 456
    assert pending[1].leg_index == 1
    assert pending[1].quantity == Decimal("90")

    assert all(item.status == "PENDING" for item in pending)


def test_partial_hedge_accounting(tmp_path):
    database = tmp_path / "albridge.sqlite3"

    registry = ObligationRegistry(database)

    obligation = HedgeObligation(
        execution_id=500,
        client_order_id="albridge-test-500",
        venue="polymarket",
        instrument_id="draw-token",
        outcome="DRAW",
        execution_side="BUY",
        pm_price=Decimal("0.40"),
        quantity=Decimal("60"),
    )

    registry.register(obligations=(obligation,))

    registry.record_hedge_fill(
        execution_id=500,
        leg_index=0,
        filled_quantity=Decimal("20"),
    )

    pending = registry.get_pending()

    assert len(pending) == 1
    assert pending[0].status == "PARTIALLY_HEDGED"
    assert pending[0].hedged_quantity == Decimal("20")
    assert pending[0].quantity - pending[0].hedged_quantity == Decimal("40")

    # Read the stored accounting directly for this test.
    import sqlite3

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT quantity, hedged_quantity, status
            FROM hedge_obligations
            WHERE execution_id = 500 AND leg_index = 0
            """
        ).fetchone()

    assert Decimal(row[0]) == Decimal("60")
    assert Decimal(row[1]) == Decimal("20")
    assert row[2] == "PARTIALLY_HEDGED"

    registry.record_hedge_fill(
        execution_id=500,
        leg_index=0,
        filled_quantity=Decimal("40"),
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT hedged_quantity, status
            FROM hedge_obligations
            WHERE execution_id = 500 AND leg_index = 0
            """
        ).fetchone()

    assert Decimal(row[0]) == Decimal("60")
    assert row[1] == "HEDGED"

def test_partially_hedged_obligation_remains_pending(tmp_path):
    database = tmp_path / "albridge.sqlite3"
    registry = ObligationRegistry(database)

    obligation = HedgeObligation(
        execution_id=600,
        client_order_id="albridge-test-600",
        venue="polymarket",
        instrument_id="draw-token",
        outcome="DRAW",
        execution_side="BUY",
        pm_price=Decimal("0.40"),
        quantity=Decimal("60"),
    )

    registry.register(obligations=(obligation,))

    registry.record_hedge_fill(
        execution_id=600,
        leg_index=0,
        filled_quantity=Decimal("20"),
    )

    pending = registry.get_pending()

    assert len(pending) == 1
    assert pending[0].status == "PARTIALLY_HEDGED"
    assert pending[0].quantity == Decimal("60")
    assert pending[0].hedged_quantity == Decimal("20")

    remaining = (
        pending[0].quantity
        - pending[0].hedged_quantity
    )

    assert remaining == Decimal("40")

def test_external_hedge_fill_is_idempotent(tmp_path):
    database = tmp_path / "albridge.sqlite3"
    registry = ObligationRegistry(database)

    registry.register(obligations=(
        HedgeObligation(
            execution_id=700,
            client_order_id="albridge-700",
            venue="polymarket",
            instrument_id="draw-token",
            outcome="DRAW",
            execution_side="BUY",
            pm_price=Decimal("0.40"),
            quantity=Decimal("60"),
        ),
    ))

    args = dict(
        venue="polymarket",
        external_execution_id="pm-fill-001",
        execution_id=700,
        leg_index=0,
        filled_quantity=Decimal("20"),
    )

    assert registry.record_external_fill(**args) is True
    assert registry.record_external_fill(**args) is False

    # Reopen SQLite to simulate restart.
    restarted = ObligationRegistry(database)

    assert restarted.record_external_fill(**args) is False

    pending = restarted.get_pending()

    assert len(pending) == 1
    assert pending[0].hedged_quantity == Decimal("20")
    assert pending[0].quantity - pending[0].hedged_quantity == Decimal("40")