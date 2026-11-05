
from decimal import Decimal

from albridge.hedge_ladder import HedgeLeg
from albridge.order_registry import ManagedOrder, OrderRegistry
from albridge.risk.fill_registry import AntivistFill
from albridge.risk.fill_processor import process_antivist_fill
from albridge.risk.obligation_registry import ObligationRegistry


def test_fill_processing_is_atomic_and_idempotent(tmp_path):
    database = str(tmp_path / "albridge.sqlite3")

    orders = OrderRegistry(database)

    orders.register(
        ManagedOrder(
            client_order_id="albridge-test-001",
            order_id=123,
            market_id=3,
            venue="polymarket",
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("6"),
            hedge_legs=(
                HedgeLeg(
                    outcome="DRAW",
                    pm_price=Decimal("0.40"),
                    pm_quantity=Decimal("180"),
                    instrument_id="draw-token",
                ),
                HedgeLeg(
                    outcome="LOSS",
                    pm_price=Decimal("0.25"),
                    pm_quantity=Decimal("270"),
                    instrument_id="loss-token",
                ),
            ),
            status="OPEN",
        )
    )

    fill = AntivistFill(
        execution_id=456,
        trade_id=789,
        order_id=123,
        client_order_id="albridge-test-001",
        market_id=3,
        side="BUY",
        quantity=Decimal("2"),
        price=Decimal("1000"),
        created_at="2026-11-01T10:00:00+00:00",
    )

    assert process_antivist_fill(
        fill=fill,
        database_path=database,
    ) is True

    # Reprocessing after a restart must not create duplicates.
    assert process_antivist_fill(
        fill=fill,
        database_path=database,
    ) is False

    pending = ObligationRegistry(database).get_pending()

    assert len(pending) == 2
    assert pending[0].quantity == Decimal("60")
    assert pending[1].quantity == Decimal("90")
