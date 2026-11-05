
from types import SimpleNamespace

import pytest

from albridge.risk.execution_guard import (
    UnhedgedExecutionDetected,
    assert_no_managed_executions,
)


class FakeClient:
    def __init__(self, executions):
        self.executions = executions

    def get_executions(self):
        return {"executions": self.executions}


def make_order(
    client_order_id="albridge-test-1",
    order_id=100,
    status="OPEN",
):
    return SimpleNamespace(
        client_order_id=client_order_id,
        order_id=order_id,
        status=status,
    )


def test_no_executions_allows_reconciliation():
    client = FakeClient([])

    assert_no_managed_executions(
        client=client,
        registered_orders=(make_order(),),
    )


def test_foreign_execution_is_ignored():
    client = FakeClient([
        {
            "execution_id": 1,
            "order_id": 999,
            "client_order_id": "manual-order",
        }
    ])

    assert_no_managed_executions(
        client=client,
        registered_orders=(make_order(),),
    )


def test_managed_execution_blocks_reconciliation():
    client = FakeClient([
        {
            "execution_id": 2,
            "order_id": 100,
            "client_order_id": "albridge-test-1",
            "side": "BUY",
            "quantity": "0.5",
        }
    ])

    with pytest.raises(UnhedgedExecutionDetected):
        assert_no_managed_executions(
            client=client,
            registered_orders=(make_order(),),
        )


def test_execution_matches_by_order_id():
    client = FakeClient([
        {
            "execution_id": 3,
            "order_id": 100,
            "client_order_id": None,
        }
    ])

    with pytest.raises(UnhedgedExecutionDetected):
        assert_no_managed_executions(
            client=client,
            registered_orders=(make_order(),),
        )


def test_terminal_order_execution_triggers_guard():
    client = FakeClient([
        {
            "execution_id": 4,
            "order_id": 100,
            "client_order_id": "albridge-test-1",
        }
    ])

    with pytest.raises(UnhedgedExecutionDetected):
        assert_no_managed_executions(
            client=client,
            registered_orders=(
                make_order(status="CANCELED"),
            ),
        )


def test_invalid_response_blocks_reconciliation():
    class InvalidClient:
        def get_executions(self):
            return {"unexpected": []}

    with pytest.raises(RuntimeError, match="Missing executions"):
        assert_no_managed_executions(
            client=InvalidClient(),
            registered_orders=(make_order(),),
        )
