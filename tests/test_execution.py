from decimal import Decimal

from albridge.execution import (
    SubmitRequest,
    build_submit_payload,
    parse_batch_submit_results,
    prepare_submissions,
    apply_batch_submit_results,
    recover_pending_submissions
)
from albridge.order_manager import DesiredOrder
from albridge.order_registry import OrderRegistry
from albridge.execution import (
    SubmitResult,
    apply_batch_submit_results,
    execute_submissions,
    execute_cancellations
)

import pytest

def test_batch_submit_partial_acceptance():
    requests = tuple(
        SubmitRequest(
            client_order_id=f"albridge-test-{index}",
            market_id=3,
            desired_order=DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000") + index,
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        )
        for index in range(2)
    )

    response = {
        "ok": True,
        "results": [
            {
                "index": 0,
                "client_order_id": "albridge-test-0",
                "ok": True,
                "order": {
                    "order_id": 123,
                    "status": "OPEN",
                },
            },
            {
                "index": 1,
                "client_order_id": "albridge-test-1",
                "ok": False,
                "error": {
                    "code": "INSUFFICIENT_MARGIN",
                    "message": "Order rejected",
                },
            },
        ],
    }

    results = parse_batch_submit_results(
        response=response,
        requests=requests,
    )

    assert len(results) == 2

    assert results[0].accepted is True
    assert results[0].order_id == 123
    assert results[0].status == "OPEN"

    assert results[1].accepted is False
    assert results[1].error_code == "INSUFFICIENT_MARGIN"


def test_submit_payload_uses_public_sdk_fields():
    request = SubmitRequest(
        client_order_id="albridge-test-001",
        market_id=3,
        desired_order=DesiredOrder(
            venue="polymarket",
            side="SELL",
            price=Decimal("1020.50"),
            quantity=Decimal("1.250"),
            hedge_legs=(),
        ),
    )

    payload = build_submit_payload(request)

    assert payload["market_id"] == 3
    assert payload["side"] == "SELL"
    assert payload["price"] == "1020.50"
    assert payload["quantity"] == "1.250"
    assert payload["post_only"] is True
    assert payload["client_order_id"] == "albridge-test-001"


def test_prepare_submissions_persists_before_execution(tmp_path):
    database = tmp_path / "albridge.sqlite3"
    registry = OrderRegistry(database)

    desired = DesiredOrder(
        venue="pascal",
        side="BUY",
        price=Decimal("1000"),
        quantity=Decimal("2"),
        hedge_legs=(),
    )

    requests = prepare_submissions(
        desired_orders=(desired,),
        market_id=3,
        registry=registry,
    )

    assert len(requests) == 1

    request = requests[0]

    stored = registry.get(request.client_order_id)

    assert stored is not None
    assert stored.status == "PENDING_SUBMIT"
    assert stored.order_id is None
    assert stored.market_id == 3
    assert stored.venue == "pascal"
    assert stored.price == Decimal("1000")
    assert stored.quantity == Decimal("2")
    assert stored.hedge_legs == desired.hedge_legs

    # The record survives a process restart.
    restarted = OrderRegistry(database)

    assert restarted.get(request.client_order_id) == stored


def test_apply_batch_results(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    desired_orders = (
        DesiredOrder(
            venue="pascal",
            side="BUY",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            hedge_legs=(),
        ),
        DesiredOrder(
            venue="polymarket",
            side="SELL",
            price=Decimal("1001"),
            quantity=Decimal("3"),
            hedge_legs=(),
        ),
    )

    requests = prepare_submissions(
        desired_orders=desired_orders,
        market_id=3,
        registry=registry,
    )

    results = (
        SubmitResult(
            client_order_id=requests[0].client_order_id,
            accepted=True,
            order_id=123,
            status="PARTIALLY_FILLED",
            error_code=None,
        ),
        SubmitResult(
            client_order_id=requests[1].client_order_id,
            accepted=False,
            order_id=None,
            status=None,
            error_code="INSUFFICIENT_MARGIN",
        ),
    )

    apply_batch_submit_results(
        results=results,
        registry=registry,
    )

    accepted = registry.get(requests[0].client_order_id)
    rejected = registry.get(requests[1].client_order_id)

    assert accepted.order_id == 123
    assert accepted.status == "PARTIALLY_FILLED"

    assert rejected.order_id is None
    assert rejected.status == "REJECTED"


def test_unknown_submission_remains_pending(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        ),
        market_id=3,
        registry=registry,
    )

    # Simulate a timeout: no confirmed batch response.
    assert (
        registry.get(requests[0].client_order_id).status
        == "PENDING_SUBMIT"
    )


def test_batch_confirmation_is_idempotent(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        ),
        market_id=3,
        registry=registry,
    )

    results = (
        SubmitResult(
            client_order_id=requests[0].client_order_id,
            accepted=True,
            order_id=123,
            status="OPEN",
            error_code=None,
        ),
    )

    apply_batch_submit_results(results=results, registry=registry)

    # Simulate replaying the same HTTP response after recovery.
    apply_batch_submit_results(results=results, registry=registry)

    stored = registry.get(requests[0].client_order_id)

    assert stored.order_id == 123
    assert stored.status == "OPEN"


def test_batch_confirmation_rolls_back_on_conflict(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    desired_orders = tuple(
        DesiredOrder(
            venue="pascal",
            side="BUY",
            price=Decimal("1000") + index,
            quantity=Decimal("2"),
            hedge_legs=(),
        )
        for index in range(2)
    )

    requests = prepare_submissions(
        desired_orders=desired_orders,
        market_id=3,
        registry=registry,
    )

    # Confirm the second order with ID 200.
    registry.confirm_submission(
        client_order_id=requests[1].client_order_id,
        order_id=200,
        status="OPEN",
    )

    conflicting_results = (
        SubmitResult(
            client_order_id=requests[0].client_order_id,
            accepted=True,
            order_id=100,
            status="OPEN",
            error_code=None,
        ),
        SubmitResult(
            client_order_id=requests[1].client_order_id,
            accepted=True,
            order_id=999,
            status="OPEN",
            error_code=None,
        ),
    )

    with pytest.raises(ValueError, match="Conflicting submission"):
        apply_batch_submit_results(
            results=conflicting_results,
            registry=registry,
        )

    # First update must have been rolled back.
    first = registry.get(requests[0].client_order_id)
    second = registry.get(requests[1].client_order_id)

    assert first.status == "PENDING_SUBMIT"
    assert first.order_id is None

    assert second.status == "OPEN"
    assert second.order_id == 200


def test_recover_pending_submission(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        ),
        market_id=3,
        registry=registry,
    )

    client_order_id = requests[0].client_order_id

    class FakeClient:
        def get_orders(self, market_id=None):
            return {
                "ok": True,
                "orders": [{
                    "order_id": 123,
                    "client_order_id": client_order_id,
                    "market_id": 3,
                    "side": "BUY",
                    "price": "1000",
                    "quantity": "2",
                    "status": "OPEN",
                }],
            }

        def get_order_history(self):
            return {
                "ok": True,
                "order_history": [],
            }

    unresolved = recover_pending_submissions(
        client=FakeClient(),
        registry=registry,
        market_id=3,
    )

    assert unresolved == ()

    stored = registry.get(client_order_id)

    assert stored.order_id == 123
    assert stored.status == "OPEN"


def test_missing_order_remains_pending(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="polymarket",
                side="SELL",
                price=Decimal("1020"),
                quantity=Decimal("1"),
                hedge_legs=(),
            ),
        ),
        market_id=3,
        registry=registry,
    )

    class FakeClient:
        def get_orders(self, market_id=None):
            return {"ok": True, "orders": []}

        def get_order_history(self):
            return {"ok": True, "order_history": []}

    unresolved = recover_pending_submissions(
        client=FakeClient(),
        registry=registry,
        market_id=3,
    )

    assert unresolved == (requests[0].client_order_id,)

    stored = registry.get(requests[0].client_order_id)

    assert stored.status == "PENDING_SUBMIT"
    assert stored.order_id is None


def test_execute_submissions_through_sdk(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    class FakeClient:
        calls = 0

        def submit_orders(self, orders):
            self.calls += 1

            assert len(orders) == 1
            assert orders[0]["post_only"] is True

            return {
                "ok": True,
                "results": [
                    {
                        "index": 0,
                        "client_order_id": orders[0]["client_order_id"],
                        "ok": True,
                        "order": {
                            "order_id": 123,
                            "status": "OPEN",
                        },
                    }
                ],
            }

    client = FakeClient()

    results = execute_submissions(
        client=client,
        registry=registry,
        market_id=3,
        desired_orders=(
            DesiredOrder(
                venue="polymarket",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("1"),
                hedge_legs=(),
            ),
        ),
    )

    assert client.calls == 1
    assert len(results) == 1
    assert results[0].accepted is True

    stored = registry.get(results[0].client_order_id)

    assert stored.status == "OPEN"
    assert stored.order_id == 123


def test_timeout_keeps_submission_pending(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    class TimeoutClient:
        def submit_orders(self, orders):
            raise TimeoutError("Simulated network timeout")

    desired = DesiredOrder(
        venue="pascal",
        side="SELL",
        price=Decimal("1010"),
        quantity=Decimal("1"),
        hedge_legs=(),
    )

    with pytest.raises(TimeoutError):
        execute_submissions(
            client=TimeoutClient(),
            registry=registry,
            market_id=3,
            desired_orders=(desired,),
        )

    registered = registry.get_by_market(market_id=3)

    assert len(registered) == 1
    assert registered[0].status == "PENDING_SUBMIT"

    # No second submission may be generated.
    with pytest.raises(RuntimeError, match="PENDING_SUBMIT"):
        execute_submissions(
            client=TimeoutClient(),
            registry=registry,
            market_id=3,
            desired_orders=(desired,),
        )


def test_batch_cancel_partial_success(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")
    register_open_order(registry, order_id=123)
    register_open_order(registry, order_id=124)

    class FakeClient:
        def cancel_orders(self, order_ids):
            assert order_ids == [123, 124]

            return {
                "ok": True,
                "results": [
                    {
                        "order_id": 123,
                        "ok": True,
                    },
                    {
                        "order_id": 124,
                        "ok": False,
                        "error": {
                            "code": "ORDER_NOT_CANCELABLE",
                            "message": "Order cannot be canceled.",
                        },
                    },
                ],
            }

    canceled = execute_cancellations(
        client=FakeClient(),
        registry=registry,
        order_ids=(123, 124),
        market_id=3,
    )

    assert canceled == (123,)
    orders = {
        order.order_id: order
        for order in registry.get_by_market(market_id=3)
    }

    assert orders[123].status == "CANCELED"
    assert orders[124].status == "OPEN"


def test_batch_cancel_rejects_incomplete_response(tmp_path):
    import pytest

    registry = OrderRegistry(tmp_path / "test.sqlite3")

    register_open_order(registry, order_id=123)
    register_open_order(registry, order_id=124)

    class FakeClient:
        def cancel_orders(self, order_ids):
            return {
                "ok": True,
                "results": [
                    {"order_id": 123, "ok": True},
                ],
            }

    with pytest.raises(ValueError, match="Incomplete"):
        execute_cancellations(
            client=FakeClient(),
            registry=registry,
            market_id=3,
            order_ids=(123, 124),
        )

    orders = registry.get_by_market(market_id=3)

    assert all(
        order.status == "OPEN"
        for order in orders
    )

def test_confirm_cancellations_is_idempotent(tmp_path):
    registry = OrderRegistry(tmp_path / "test.sqlite3")

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        ),
        market_id=3,
        registry=registry,
    )

    client_order_id = requests[0].client_order_id

    registry.confirm_submission(
        client_order_id=client_order_id,
        order_id=123,
        status="OPEN",
    )

    registry.confirm_cancellations(order_ids=(123,))
    registry.confirm_cancellations(order_ids=(123,))

    stored = registry.get(client_order_id)

    assert stored.status == "CANCELED"
    assert stored.order_id == 123

def register_open_order(
    registry,
    *,
    order_id,
    market_id=3,
):
    """Register a confirmed Albridge order for cancellation tests."""

    requests = prepare_submissions(
        desired_orders=(
            DesiredOrder(
                venue="pascal",
                side="BUY",
                price=Decimal("1000"),
                quantity=Decimal("2"),
                hedge_legs=(),
            ),
        ),
        market_id=market_id,
        registry=registry,
    )

    registry.confirm_submission(
        client_order_id=requests[0].client_order_id,
        order_id=order_id,
        status="OPEN",
    )