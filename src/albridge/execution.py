
"""Prepare and validate Antivist batch submissions."""

from dataclasses import dataclass
from decimal import Decimal

from albridge.order_manager import (DesiredOrder, generate_client_order_id,
                                    build_reconciliation_plan, parse_existing_orders)
from albridge.order_registry import ManagedOrder, OrderRegistry
from albridge.hedge_ladder import HedgeLeg
from albridge.order_manager import ReconciliationAction, match_registered_open_orders

from albridge.risk.execution_guard import (
    assert_no_managed_executions,
    UnhedgedExecutionDetected,
)

from albridge.risk.limits import get_risk_limits

from albridge.risk.emergency_halt import assert_trading_enabled

import logging

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class SubmitRequest:
    client_order_id: str
    market_id: int
    desired_order: DesiredOrder


@dataclass(frozen=True)
class SubmitResult:
    client_order_id: str
    accepted: bool
    order_id: int | None
    status: str | None
    error_code: str | None


def build_submit_payload(
    request: SubmitRequest,
) -> dict:
    """Build a public Antivist SDK submit payload."""

    order = request.desired_order

    return {
        "market_id": request.market_id,
        "side": order.side,
        "price": str(order.price),
        "quantity": str(order.quantity),
        "leverage": "1",
        "post_only": True,
        "reduce_only": False,
        "client_order_id": request.client_order_id,
    }


def parse_batch_submit_results(
    *,
    response: dict,
    requests: tuple[SubmitRequest, ...],
) -> tuple[SubmitResult, ...]:
    """
    Validate individual results from Antivist batch-submit.

    Reject malformed or incomplete responses rather than
    treating unconfirmed orders as accepted or rejected.
    """

    if response.get("ok") is not True:
        raise ValueError("Batch submit not confirmed")

    results = response.get("results")

    if not isinstance(results, list):
        raise ValueError("Missing batch results")

    if len(results) != len(requests):
        raise ValueError("Incomplete batch response")

    parsed = []
    seen_indices = set()

    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Invalid batch item")

        index = item.get("index")

        if type(index) is not int or not 0 <= index < len(requests):
            raise ValueError("Invalid batch index")

        if index in seen_indices:
            raise ValueError("Duplicate batch index")

        seen_indices.add(index)

        expected_id = requests[index].client_order_id

        if item.get("client_order_id") != expected_id:
            raise ValueError("Client order ID mismatch")

        if item.get("ok") is True:
            order = item.get("order")

            if not isinstance(order, dict):
                raise ValueError("Missing accepted order")

            order_id = order.get("order_id")
            status = order.get("status")

            if type(order_id) is not int or order_id <= 0:
                raise ValueError("Invalid accepted order ID")

            if not isinstance(status, str) or not status:
                raise ValueError("Invalid accepted order status")

            parsed.append(
                (
                    index,
                    SubmitResult(
                        client_order_id=expected_id,
                        accepted=True,
                        order_id=order_id,
                        status=status,
                        error_code=None,
                    ),
                )
            )

        elif item.get("ok") is False:
            error = item.get("error")

            if not isinstance(error, dict):
                raise ValueError("Missing rejection error")

            code = error.get("code")

            if not isinstance(code, str) or not code:
                raise ValueError("Invalid rejection code")

            parsed.append(
                (
                    index,
                    SubmitResult(
                        client_order_id=expected_id,
                        accepted=False,
                        order_id=None,
                        status=None,
                        error_code=code,
                    ),
                )
            )

        else:
            raise ValueError("Missing batch item outcome")

    return tuple(
        result
        for _, result in sorted(parsed)
    )

def prepare_submissions(
    *,
    desired_orders: tuple[DesiredOrder, ...],
    market_id: int,
    registry: OrderRegistry,
) -> tuple[SubmitRequest, ...]:
    """
    Persist submission intents before any exchange request.

    Do not submit orders to Antivist.
    """

    if type(market_id) is not int or market_id <= 0:
        raise ValueError("Invalid market_id")

    requests = []

    for desired in desired_orders:
        client_order_id = generate_client_order_id()

        registry.register(
            ManagedOrder(
                client_order_id=client_order_id,
                order_id=None,
                market_id=market_id,
                venue=desired.venue,
                side=desired.side,
                price=desired.price,
                quantity=desired.quantity,
                hedge_legs=desired.hedge_legs,
                status="PENDING_SUBMIT",
            )
        )

        requests.append(
            SubmitRequest(
                client_order_id=client_order_id,
                market_id=market_id,
                desired_order=desired,
            )
        )

    return tuple(requests)


def apply_batch_submit_results(
    *,
    results: tuple[SubmitResult, ...],
    registry: OrderRegistry,
) -> None:
    """
    Atomically persist all confirmed batch outcomes.

    Unknown results must not be passed to this function.
    """

    outcomes = []

    for result in results:
        if result.accepted:
            if result.order_id is None or result.status is None:
                raise ValueError("Incomplete accepted submission")

            outcomes.append(
                (
                    result.client_order_id,
                    True,
                    result.order_id,
                    result.status,
                )
            )
        else:
            if not result.error_code:
                raise ValueError("Incomplete rejected submission")

            outcomes.append(
                (
                    result.client_order_id,
                    False,
                    None,
                    "REJECTED",
                )
            )

    registry.apply_submission_outcomes(tuple(outcomes))


def recover_pending_submissions(
    *,
    client,
    registry: OrderRegistry,
    market_id: int,
) -> tuple[str, ...]:
    """
    Recover pending Antivist submissions using public SDK endpoints.

    Returns unresolved client_order_ids.
    Never assumes that a missing order was rejected.
    """

    pending = {
        order.client_order_id: order
        for order in registry.get_by_market(market_id=market_id)
        if order.status == "PENDING_SUBMIT"
    }

    if not pending:
        return ()

    # Both requests must succeed before changing local state.
    open_response = client.get_orders(market_id=market_id)
    history_response = client.get_order_history()

    if (
        not isinstance(open_response, dict)
        or open_response.get("ok") is not True
        or not isinstance(open_response.get("orders"), list)
    ):
        raise ValueError("Invalid Antivist open orders response")

    if (
        not isinstance(history_response, dict)
        or history_response.get("ok") is not True
        or not isinstance(history_response.get("order_history"), list)
    ):
        raise ValueError("Invalid Antivist order history response")

    found = {}

    for source in (
        open_response["orders"],
        history_response["order_history"],
    ):
        for item in source:
            if not isinstance(item, dict):
                raise ValueError("Malformed Antivist order")

            client_order_id = item.get("client_order_id")

            if client_order_id not in pending:
                continue

            if client_order_id in found:
                raise ValueError(
                    f"Duplicate recovered order: {client_order_id}"
                )

            registered = pending[client_order_id]

            if (
                item.get("market_id") != registered.market_id
                or item.get("side") != registered.side
                or Decimal(str(item.get("quantity"))) != registered.quantity
            ):
                raise ValueError(
                    f"Recovered order mismatch: {client_order_id}"
                )

            # Registered Albridge orders are LIMIT orders.
            if (
                item.get("price") is None
                or Decimal(str(item["price"])) != registered.price
            ):
                raise ValueError(
                    f"Recovered order price mismatch: {client_order_id}"
                )

            status = item.get("status")
            order_id = item.get("order_id")

            if status == "REJECTED":
                if order_id is not None:
                    raise ValueError("Rejected order has an order_id")

                found[client_order_id] = (
                    client_order_id,
                    False,
                    None,
                    "REJECTED",
                )
            elif status in {
                "OPEN",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELED",
            }:
                if type(order_id) is not int or order_id <= 0:
                    raise ValueError("Invalid recovered order_id")

                found[client_order_id] = (
                    client_order_id,
                    True,
                    order_id,
                    status,
                )
            else:
                raise ValueError(
                    f"Unknown recovered status: {status}"
                )

    # Reuse the existing atomic and idempotent registry method.
    if found:
        registry.apply_submission_outcomes(
            tuple(found.values())
        )

    return tuple(sorted(pending.keys() - found.keys()))

def execute_submissions(
    *,
    client,
    registry: OrderRegistry,
    market_id: int,
    desired_orders: tuple[DesiredOrder, ...],
) -> tuple[SubmitResult, ...]:
    """
    Submit a controlled batch through the public Antivist SDK.

    Never automatically retry an uncertain submission.
    """

    if not desired_orders:
        return ()

    # Do not create new orders while any submission is unresolved.
    registered = registry.get_by_market(market_id=market_id)

    if any(
        order.status == "PENDING_SUBMIT"
        for order in registered
    ):
        raise RuntimeError(
            "Unresolved PENDING_SUBMIT orders. "
            "Recovery required before submitting."
        )

    # Persist every intent before sending the HTTP request.
    requests = prepare_submissions(
        desired_orders=desired_orders,
        market_id=market_id,
        registry=registry,
    )

    payloads = [
        build_submit_payload(request)
        for request in requests
    ]

    try:
        response = client.submit_orders(payloads)

        results = parse_batch_submit_results(
            response=response,
            requests=requests,
        )

        apply_batch_submit_results(
            results=results,
            registry=registry,
        )

    except Exception:
        # The exchange may have accepted some or all orders.
        # Keep unresolved submissions in SQLite.
        # Never blindly resend with new client_order_ids.
        raise

    return results

def execute_cancellations(
    *,
    client,
    registry: OrderRegistry,
    order_ids: tuple[int, ...],
    market_id: int,
) -> tuple[int, ...]:
    """
    Cancel managed Antivist orders through the public SDK.

    Return successfully canceled order IDs.
    Never mark failed or uncertain cancellations as complete.
    """

    if not order_ids:
        return ()

    if (
        len(order_ids) > 200
        or len(set(order_ids)) != len(order_ids)
        or any(type(order_id) is not int or order_id <= 0 for order_id in order_ids)
    ):
        raise ValueError("Invalid cancellation batch")

    # Never cancel orders that are not registered as Albridge orders.
    managed = {
        order.order_id: order
        for order in registry.get_by_market(
            market_id=market_id,
        )
        if order.order_id is not None
    }

    for order_id in order_ids:
        order = managed.get(order_id)

        if order is None:
            raise ValueError(
                f"Order {order_id} is not managed by Albridge"
            )

        if order.status not in {"OPEN", "PARTIALLY_FILLED"}:
            raise ValueError(
                f"Order {order_id} is not active"
            )

    response = client.cancel_orders(list(order_ids))

    if (
        not isinstance(response, dict)
        or response.get("ok") is not True
        or not isinstance(response.get("results"), list)
    ):
        raise ValueError("Invalid Antivist cancellation response")

    results = response["results"]

    if len(results) != len(order_ids):
        raise ValueError("Incomplete cancellation response")

    expected = set(order_ids)
    seen = set()
    canceled = []

    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Malformed cancellation result")

        order_id = result.get("order_id")

        if (
            type(order_id) is not int
            or order_id not in expected
            or order_id in seen
        ):
            raise ValueError("Unexpected cancellation order_id")

        seen.add(order_id)

        if result.get("ok") is True:
            canceled.append(order_id)
        elif result.get("ok") is False:
            error = result.get("error")

            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
            ):
                raise ValueError("Invalid cancellation rejection")
        else:
            raise ValueError("Missing cancellation result status")

    registry.confirm_cancellations(
        order_ids=tuple(canceled),
    )
    return tuple(canceled)


def reconcile_live_once(
    *,
    client,
    registry: OrderRegistry,
    market_id: int,
    desired_orders: tuple[DesiredOrder, ...],
) -> dict:
    """
    Execute one live reconciliation cycle against Antivist.

    Cancellation and submission are separate phases.
    Unknown exchange state blocks execution.
    """
    assert_trading_enabled()

    # Recover uncertain submissions before creating new orders.
    unresolved = recover_pending_submissions(
        client=client,
        registry=registry,
        market_id=market_id,
    )

    if unresolved:
        raise RuntimeError(
            f"Unresolved submissions: {len(unresolved)}"
        )

    # Refresh actual exchange state, never reuse startup snapshots.
    response = client.get_orders(market_id=market_id)

    existing_orders = parse_existing_orders(response=response)

    registered_orders = registry.get_by_market(
        market_id=market_id,
    )

    # Stop on detected fills before changing quotes.
    try:
        assert_no_managed_executions(
            client=client,
            registered_orders=registered_orders,
        )

    except UnhedgedExecutionDetected:
        cancel_managed_orders_after_fill(
            client=client,
            registry=registry,
            market_id=market_id,
        )
        raise

    managed_ids = {
        order.client_order_id
        for order in registered_orders
    }

    # No external hedging is enabled yet.
    # Stop if a managed order has been partially executed.
    for order in existing_orders:
        if (
            order.client_order_id in managed_ids
            and order.filled_quantity > 0
        ):
                    cancel_managed_orders_after_fill(
                        client=client,
                        registry=registry,
                        market_id=market_id,
                    )

                    raise UnhedgedExecutionDetected(
                        "Managed Antivist order is partially filled. "
                        "Protective cancellation requested."
                    )

    # A missing active order, or an unrecognized state,
    # must be handled by the existing reconciliation safeguards.
    plan = build_reconciliation_plan(
        desired_orders=desired_orders,
        registered_orders=registered_orders,
        existing_orders=existing_orders,
        market_id=market_id,
    )


    keep_updates = []

    for decision in plan:
        if decision.action.value != "KEEP":
            continue

        existing = decision.existing_order
        desired = decision.desired_order

        if existing is None or desired is None:
            raise RuntimeError("Invalid KEEP decision")

        # Scale the desired hedge legs to the actual
        # quantity resting on Antivist.
        ratio = existing.quantity / desired.quantity

        adjusted_legs = tuple(
            HedgeLeg(
                outcome=leg.outcome,
                pm_price=leg.pm_price,
                pm_quantity=leg.pm_quantity * ratio,
                instrument_id=leg.instrument_id,
                execution_side=leg.execution_side,
            )
            for leg in desired.hedge_legs
        )

        registered = next(
            (
                order
                for order in registered_orders
                if order.client_order_id == existing.client_order_id
            ),
            None,
        )

        if registered is None:
            raise RuntimeError("KEEP order missing from Registry")

        if registered.hedge_legs != adjusted_legs:
            keep_updates.append(
                (
                    existing.client_order_id,
                    existing.order_id,
                    adjusted_legs,
                )
            )

    registry.update_kept_hedges(tuple(keep_updates))

    diagnostics = diagnose_cancellations(
        plan=plan,
        registered_orders=registered_orders,
        desired_orders=desired_orders,
    )

    if diagnostics:
        LOGGER.info(
            "RECONCILIATION CANCEL REASONS | %s",
            diagnostics,
        )

    cancellations = [
        decision.existing_order.order_id
        for decision in plan
        if decision.action.value == "CANCEL"
        and decision.existing_order is not None
    ]

    submissions = [
        decision.desired_order
        for decision in plan
        if decision.action.value == "SUBMIT"
        and decision.desired_order is not None
    ]

    keeps = sum(
        decision.action.value == "KEEP"
        for decision in plan
    )


    # Phase 1: cancel obsolete orders and confirm exchange state.
    canceled_count = 0

    if cancellations:
        canceled = execute_cancellations(
            client=client,
            registry=registry,
            market_id=market_id,
            order_ids=tuple(cancellations),
        )

        if len(canceled) != len(cancellations):
            raise RuntimeError(
                "Partial batch cancellation. "
                "Inspect exchange state before continuing."
            )

        canceled_count = len(canceled)

        # Fetch authoritative exchange state after cancellations.
        response = client.get_orders(market_id=market_id)
        existing_orders = parse_existing_orders(response=response)

        registered_orders = registry.get_by_market(
            market_id=market_id,
        )

        # Detect executions that occurred during cancellation.
        try:
            assert_no_managed_executions(
                client=client,
                registered_orders=registered_orders,
            )
        except UnhedgedExecutionDetected:
            cancel_managed_orders_after_fill(
                client=client,
                registry=registry,
                market_id=market_id,
            )
            raise

        managed_ids = {
            order.client_order_id
            for order in registered_orders
        }

        for order in existing_orders:
            if (
                order.client_order_id in managed_ids
                and order.filled_quantity > 0
            ):
                cancel_managed_orders_after_fill(
                    client=client,
                    registry=registry,
                    market_id=market_id,
                )
                raise UnhedgedExecutionDetected(
                    "Managed order filled during cancellation"
                )

        # Reconcile against the updated exchange state.
        plan = build_reconciliation_plan(
            desired_orders=desired_orders,
            registered_orders=registered_orders,
            existing_orders=existing_orders,
            market_id=market_id,
        )

        # Never submit while another cancellation is required.
        if any(
            decision.action == ReconciliationAction.CANCEL
            for decision in plan
        ):
            raise RuntimeError(
                "New cancellations required after refresh. "
                "Skipping submissions."
            )

        submissions = [
            decision.desired_order
            for decision in plan
            if decision.action == ReconciliationAction.SUBMIT
            and decision.desired_order is not None
        ]

        keeps = sum(
            decision.action == ReconciliationAction.KEEP
            for decision in plan
        )

    # Phase 2: submit only orders selected by reconciliation.
    if submissions:
        total_notional = sum(
            order.price * order.quantity
            for order in submissions
        )

        LOGGER.info(
            "ALBRIDGE NOTIONAL | orders=%s | total=%s USDC | max=%s USDC",
            len(submissions),
            f"{total_notional:,.2f}",
            f"{max(order.price * order.quantity for order in submissions):,.2f}",
        )


        # Conservative budget:
        # all currently open orders on this Antivist market
        # plus all submissions planned for this cycle.
        #
        # Include orders not managed by Albridge to avoid
        # ignoring exposure sharing the same account/market.
        open_notional = sum(
            (
                order.price * order.remaining_quantity
                for order in existing_orders
            ),
            Decimal("0"),
        )

        submit_notional = sum(
            (
                order.price * order.quantity
                for order in submissions
            ),
            Decimal("0"),
        )

        max_market_notional = get_risk_limits().max_total_notional

        projected_notional = (
            open_notional + submit_notional
        )

        if projected_notional > max_market_notional:
            raise RuntimeError(
                "Market order budget exceeded: "
                f"open={open_notional} "
                f"submit={submit_notional} "
                f"projected={projected_notional} "
                f"limit={max_market_notional}"
            )

        assert_trading_enabled()

        results = execute_submissions(
            client=client,
            registry=registry,
            market_id=market_id,
            desired_orders=tuple(submissions),
        )

        # Detect fills and withdraw remaining managed orders.
        try:
            assert_no_managed_executions(
                client=client,
                registered_orders=registry.get_by_market(
                    market_id=market_id,
                ),
            )
        except UnhedgedExecutionDetected:
            cancel_managed_orders_after_fill(
                client=client,
                registry=registry,
                market_id=market_id,
            )
            raise

        rejected = [
            result
            for result in results
            if not result.accepted
        ]

        if rejected:
            raise RuntimeError(
                "Antivist rejected "
                f"{len(rejected)} submissions: "
                + ", ".join(
                    result.error_code or "UNKNOWN"
                    for result in rejected
                )
            )

        if any(
            result.status != "OPEN"
            for result in results
        ):
            raise RuntimeError(
                "Unexpected submitted order status. "
                "Inspect fills before continuing."
            )

        return {
            "keep": keeps,
            "canceled": canceled_count,
            "submitted": len(results),
        }

    return {
        "keep": keeps,
        "canceled": canceled_count,
        "submitted": 0,
    }


from collections import defaultdict


def diagnose_cancellations(
    *,
    plan,
    registered_orders,
    desired_orders,
):
    """
    Diagnose cancel causes without changing execution decisions.
    """

    desired_by_slot = defaultdict(list)

    for order in desired_orders:
        key = (order.venue, order.side)
        desired_by_slot[key].append(order)

    registered_by_id = {
        order.order_id: order
        for order in registered_orders
        if order.order_id is not None
    }

    reasons = defaultdict(int)

    for decision in plan:
        if decision.action.value != "CANCEL":
            continue

        existing = decision.existing_order

        if existing is None:
            continue

        registered = registered_by_id.get(existing.order_id)

        if registered is None:
            reasons["UNREGISTERED"] += 1
            continue

        candidates = desired_by_slot.get(
            (registered.venue, registered.side),
            [],
        )

        if not candidates:
            reasons["NO_DESIRED_QUOTE"] += 1
            continue

        same_price = [
            order for order in candidates
            if order.price == registered.price
        ]

        if not same_price:
            reasons["PRICE_CHANGED_OR_REMOVED"] += 1
            continue

        same_quantity = [
            order for order in same_price
            if order.quantity == registered.quantity
        ]

        if not same_quantity:
            reasons["QUANTITY_CHANGED"] += 1
            continue

        if any(
            order.hedge_legs == registered.hedge_legs
            for order in same_quantity
        ):
            reasons["DUPLICATE_OR_MATCHING_CONFLICT"] += 1
        else:
            reasons["HEDGE_LEGS_ONLY"] += 1

    return dict(reasons)


def cancel_managed_orders_after_fill(
    *,
    client,
    registry: OrderRegistry,
    market_id: int,
) -> None:
    """Cancel resting Albridge orders after an unhedged fill."""

    response = client.get_orders(market_id=market_id)
    existing_orders = parse_existing_orders(response=response)

    registered_orders = registry.get_by_market(
        market_id=market_id,
    )

    matches = match_registered_open_orders(
        registered_orders=registered_orders,
        existing_orders=existing_orders,
    )

    order_ids = tuple(
        existing.order_id
        for registered, existing in matches
        if registered.status in {"OPEN", "PARTIALLY_FILLED"}
    )

    if not order_ids:
        return

    canceled = execute_cancellations(
        client=client,
        registry=registry,
        market_id=market_id,
        order_ids=order_ids,
    )

    if len(canceled) != len(order_ids):
        raise RuntimeError(
            "Protective cancellation incomplete"
        )
