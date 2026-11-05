
"""Detect unhedged executions on active Albridge orders."""

ACTIVE_STATES = frozenset({
    "OPEN",
    "PARTIALLY_FILLED",
    "PENDING_SUBMIT",
    "PENDING_CANCEL",
})


class UnhedgedExecutionDetected(RuntimeError):
    pass


def assert_no_managed_executions(
    *,
    client,
    registered_orders,
) -> None:
    """
    Fail closed if a recent Antivist execution belongs
    to an active Albridge-managed order.

    This does not perform hedging or execution accounting.
    """

    active = [
        order
        for order in registered_orders
        if order.status != "REJECTED"
    ]

    if not active:
        return

    client_ids = {
        order.client_order_id
        for order in active
    }

    order_ids = {
        order.order_id
        for order in active
        if order.order_id is not None
    }

    response = client.get_executions()

    if not isinstance(response, dict):
        raise RuntimeError("Invalid executions response")

    executions = response.get("executions")

    if not isinstance(executions, list):
        raise RuntimeError("Missing executions list")

    for execution in executions:
        if not isinstance(execution, dict):
            raise RuntimeError("Invalid execution record")

        client_id = execution.get("client_order_id")
        order_id = execution.get("order_id")

        if (
            client_id in client_ids
            or order_id in order_ids
        ):
            raise UnhedgedExecutionDetected(
                "UNHEDGED ANTIVIST EXECUTION | "
                f"execution_id={execution.get('execution_id')} "
                f"order_id={order_id} "
                f"client_order_id={client_id} "
                f"side={execution.get('side')} "
                f"quantity={execution.get('quantity')}"
            )
