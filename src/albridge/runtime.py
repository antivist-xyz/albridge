"""Run independent market-data feeds in one Albridge process."""

import logging
import threading
import time
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from albridge.market_data import MarketDataAggregator
from albridge.venues.pascal import PascalWebSocketClient
from albridge.venues.polymarket import PolymarketWebSocketClient
from albridge.market_data_health import assess_feed_health

from albridge.depth_publisher import publish_depth

from albridge.hedge_pricing import (
    anchors_from_pricing,
    calculate_quotes_from_snapshot,
    calculate_hedge_capacity,
    HedgePrices, build_fee_debug
)
from albridge.hedge_ladder import build_hedge_ladders
from albridge.ladder_normalizer import normalize_hedge_ladder
from albridge.market_rules import AntivistMarketRules

from albridge.risk.price_level_budget import (
    calculate_price_level_budget,
)

from albridge.risk.ladder_allocator import (
    allocate_ladder_to_price_budget,
)

from albridge.risk.snapshot_split import (
    split_snapshot_for_quote_sides,
)

from albridge.order_manager import (
    build_desired_orders,
    build_reconciliation_plan,
    ReconciliationBlocked,
)

from albridge.order_logger import OrderDecisionLogger

from albridge.execution import (
    execute_submissions,
    reconcile_live_once,
    diagnose_cancellations,
)

from collections import Counter, defaultdict

from albridge.quote_budget import allocate_quote_budget
from albridge.operations_snapshot import publish_operations_snapshot

from albridge.operations_snapshot import (
    publish_operations_snapshot,
    collect_exchange_state,
)

from albridge.shutdown import shutdown_managed_orders

LOGGER = logging.getLogger(__name__)


def run_market_data(
    resolution,
    *,
    market_rules: AntivistMarketRules,
    market_id: int,
    registered_orders=(),
    existing_orders=(),
    antivist_client=None,
    registry=None,
    execute_testnet_smoke=False,
    execute_live=False,
    public_demo=False,
):

    """Start available venue feeds and monitor their published snapshots."""

    if public_demo and (execute_live or execute_testnet_smoke):
        raise ValueError("Public demo cannot execute orders")


    if type(market_id) is not int or market_id <= 0:
        raise ValueError("Invalid Antivist market_id")

    if execute_testnet_smoke:
        if antivist_client is None or registry is None:
            raise ValueError(
                "Testnet execution requires Antivist client and registry"
            )

        LOGGER.warning(
            "TESTNET SMOKE ENABLED | market=%s | max_submits=1",
            market_id,
        )

    LOGGER.info("ANTIVIST MARKET ID | %s", market_id)

    LOGGER.info(
        "ANTIVIST MARKET RULES | "
        "tick_size=%s quantity_step=%s min_quantity=%s",
        market_rules.tick_size,
        market_rules.quantity_step,
        market_rules.min_quantity,
    )

    try:
        win_anchor, draw_anchor, loss_anchor = anchors_from_pricing(
            resolution.pricing
        )
    except ValueError as exc:
        LOGGER.warning("HEDGE PRICING DISABLED | %s", exc)
        anchors = None
    else:
        anchors = (win_anchor, draw_anchor, loss_anchor)

        LOGGER.info(
            "ANTIVIST ANCHORS | WIN=%s DRAW=%s LOSS=%s",
            win_anchor,
            draw_anchor,
            loss_anchor,
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    pascal_client = None
    polymarket_client = None

    if resolution.pascal is not None:
        outcome_symbols = resolution.pascal.outcome_symbols

        pascal_client = PascalWebSocketClient(
            symbols=list(outcome_symbols.values()),
            outcome_symbols=outcome_symbols,
            recording_path=Path(
                f"recordings/pascal_{timestamp}.jsonl"
            ),
        )

    if resolution.polymarket is not None:
        polymarket_client = PolymarketWebSocketClient(
            outcome_tokens=resolution.polymarket.outcome_tokens,
            recording_path=Path(
                f"recordings/polymarket_{timestamp}.jsonl"
            ),
        )

    aggregator = MarketDataAggregator(
        pascal_client=pascal_client,
        polymarket_client=polymarket_client,
    )

    clients = {
        "pascal": pascal_client,
        "polymarket": polymarket_client,
    }

    threads = []

    for venue, client in clients.items():
        if client is None:
            LOGGER.info("%s | UNAVAILABLE", venue.upper())
            continue

        thread = threading.Thread(
            target=client.start,
            name=f"{venue}-websocket",
            daemon=True,
        )

        threads.append(thread)
        thread.start()

        LOGGER.info("%s | STARTING", venue.upper())

    if not threads:
        LOGGER.error("NO MARKET DATA VENUES AVAILABLE")
        return

    LOGGER.info("ALBRIDGE MARKET DATA STARTED")

    try:

        fixture = resolution.fixture
        fixture_label = f"{fixture.home_team} vs {fixture.away_team}"
        last_desired_signature = None
        last_plan_signature = None

        order_logger = OrderDecisionLogger(
            "logs/order_manager.log"
        )

        previous_hedge_composition = None

        last_live_sync = 0.0
        LIVE_SYNC_INTERVAL = 1.0

        previous_quote_state = None
        previous_price_state = None

        operations_exchange_verified_at = None
        operations_exchange_state = None
        last_operations_poll = float("-inf")
        OPERATIONS_POLL_INTERVAL = 2.0

        operations_reconciliation = None
        operations_venues = {}

        last_health_log = 0.0
        last_runtime_tick = None

        while True:
            time.sleep(0.25)

            tick_started = time.monotonic()

            if last_runtime_tick is not None:
                tick_gap = tick_started - last_runtime_tick

                if tick_gap > 1.0:
                    LOGGER.warning(
                        "RUNTIME TICK DELAY | gap=%.3fs",
                        tick_gap,
                    )

            last_runtime_tick = tick_started

            market_data = aggregator.snapshot()
            ladders_by_venue = {}

            risk_ladders_by_venue = {}

            quotes_by_venue = {}

            if anchors is not None:
                for venue in market_data.available_venues:
                    snapshot = market_data.get(venue)
                    client = clients[venue]

                    if snapshot is None or client is None:
                        continue

                    # Never derive tradable quotes from a stale or unhealthy feed.
                    health = assess_feed_health(
                        venue=venue,
                        client=client,
                    )

                    if health.status != "READY":
                        continue

                    if venue == "pascal":
                        if resolution.pascal is None:
                            continue

                        fee_rates = resolution.pascal.fee_rates

                    elif venue == "polymarket":
                        if resolution.polymarket is None:
                            continue

                        fee_rates = {
                            outcome: config.rate if config.enabled else Decimal("0")
                            for outcome, config
                            in resolution.polymarket.fee_configs.items()
                        }

                    else:
                        continue

                    if set(fee_rates) != {"WIN", "DRAW", "LOSS"}:
                        raise ValueError(f"Incomplete fee configuration: {venue}")

                    # Build theoretical hedge ladders from the venue snapshot.
                    # raw_sell_ladder, raw_buy_ladder = build_hedge_ladders(
                    #     snapshot=snapshot,
                    #     win_anchor=anchors[0],
                    #     draw_anchor=anchors[1],
                    #     loss_anchor=anchors[2],
                    #     max_levels=5,
                    # )


                    # --------------------------------------------------
                    # 1. Build theoretical ladders from full liquidity.
                    # --------------------------------------------------

                    raw_sell_ladder, raw_buy_ladder = build_hedge_ladders(
                        snapshot=snapshot,
                        win_anchor=anchors[0],
                        draw_anchor=anchors[1],
                        loss_anchor=anchors[2],
                        max_levels=5,
                        fee_rates=fee_rates,
                    )

                    sell_ladder = normalize_hedge_ladder(
                        levels=raw_sell_ladder,
                        rules=market_rules,
                    )

                    buy_ladder = normalize_hedge_ladder(
                        levels=raw_buy_ladder,
                        rules=market_rules,
                    )

                    ladders_by_venue[venue] = {
                        "sell": sell_ladder,
                        "buy": buy_ladder,
                    }

                    # --------------------------------------------------
                    # 2. Split shared DRAW liquidity before building.
                    # --------------------------------------------------

                    sell_snapshot, buy_snapshot = (
                        split_snapshot_for_quote_sides(snapshot)
                    )

                    # --------------------------------------------------
                    # 3. Build ladders from side-specific liquidity.
                    # --------------------------------------------------

                    risk_raw_sell, risk_raw_buy = build_hedge_ladders(
                        snapshot=snapshot,
                        sell_snapshot=sell_snapshot,
                        buy_snapshot=buy_snapshot,
                        win_anchor=anchors[0],
                        draw_anchor=anchors[1],
                        loss_anchor=anchors[2],
                        max_levels=5,
                        fee_rates=fee_rates,
                    )

                    # --------------------------------------------------
                    # 4. Normalize risk-allocated ladders.
                    # --------------------------------------------------

                    risk_sell_ladder = normalize_hedge_ladder(
                        levels=risk_raw_sell,
                        rules=market_rules,
                    )

                    risk_buy_ladder = normalize_hedge_ladder(
                        levels=risk_raw_buy,
                        rules=market_rules,
                    )

                    risk_ladders_by_venue[venue] = {
                        "sell": risk_sell_ladder,
                        "buy": risk_buy_ladder,
                    }


                    quotes = calculate_quotes_from_snapshot(
                        snapshot=snapshot,
                        win_anchor=anchors[0],
                        draw_anchor=anchors[1],
                        loss_anchor=anchors[2],
                        fee_rates=fee_rates,
                    )

                    if quotes is not None:
                        ask_quote, bid_quote = quotes

                        prices = HedgePrices(
                            win=snapshot.get("WIN").best_ask,
                            draw=snapshot.get("DRAW").best_ask,
                            loss=snapshot.get("LOSS").best_ask,
                        )

                        for quote in (ask_quote, bid_quote):
                            debug = build_fee_debug(
                                side=quote.side,
                                prices=prices,
                                hedge_quantities=quote.hedge_quantities,
                                fee_rates=fee_rates,
                                fair_entry=quote.fair_entry,
                            )

                            LOGGER.debug(
                                "PRICING FEES | venue=%s side=%s | "
                                "raw=%s fees=%s adjusted=%s | legs=%s",
                                venue,
                                quote.side,
                                debug["raw_entry"],
                                debug["total_fee"],
                                debug["adjusted_entry"],
                                [
                                    (
                                        leg["outcome"],
                                        str(leg["pm_price"]),
                                        str(leg["quantity"]),
                                        str(leg["fee_rate"]),
                                        str(leg["fee"]),
                                    )
                                    for leg in debug["legs"]
                                ],
                            )

                        quotes_by_venue[venue] = {
                            "quotes": quotes,
                            "capacities": {
                                "ask": calculate_hedge_capacity(
                                    snapshot=snapshot,
                                    quote=ask_quote,
                                ),
                                "bid": calculate_hedge_capacity(
                                    snapshot=snapshot,
                                    quote=bid_quote,
                                ),
                            },
                        }

            # Build desired Antivist orders from risk-allocated ladders.
            # No orders are submitted or canceled.
            desired_orders = build_desired_orders(
                risk_ladders_by_venue=risk_ladders_by_venue,
            )

            desired_orders = allocate_quote_budget(
                orders=desired_orders,
                quantity_step=market_rules.quantity_step,
                min_quantity=market_rules.min_quantity,
            )


            # Refresh read-only exchange telemetry every 2 seconds.
            operations_now = time.monotonic()

            if (
                not public_demo
                and operations_now - last_operations_poll
                >= OPERATIONS_POLL_INTERVAL
            ):
                last_operations_poll = operations_now

                try:
                    if antivist_client is None or registry is None:
                        raise RuntimeError(
                            "Antivist client or Registry unavailable"
                        )

                    poll_started = time.monotonic()

                    operations_exchange_state = collect_exchange_state(
                        client=antivist_client,
                        registry=registry,
                        market_id=market_id,
                    )

                    poll_duration = time.monotonic() - poll_started

                    if poll_duration > 0.5:
                        LOGGER.warning(
                            "ANTIVIST TELEMETRY SLOW | duration=%.3fs",
                            poll_duration,
                        )



                    operations_exchange_verified_at = time.time()

                except Exception as exc:
                    # An API error must never be displayed as zero
                    # resting exposure.
                    operations_exchange_state = None
                    operations_exchange_verified_at = None

                    LOGGER.warning(
                        "OPERATIONS EXCHANGE STATE UNAVAILABLE | %s",
                        exc,
                    )

            current_quote_state = Counter(
                (o.venue, o.side, o.price, o.quantity)
                for o in desired_orders
            )

            current_price_state = Counter(
                (o.venue, o.side, o.price)
                for o in desired_orders
            )

            if previous_quote_state is not None:
                removed_quotes = previous_quote_state - current_quote_state
                added_quotes = current_quote_state - previous_quote_state

                removed_prices = previous_price_state - current_price_state
                added_prices = current_price_state - previous_price_state

                if removed_quotes or added_quotes:
                    LOGGER.info(
                        "QUOTE CHANGE | "
                        "removed=%s added=%s | "
                        "price_removed=%s price_added=%s",
                        sum(removed_quotes.values()),
                        sum(added_quotes.values()),
                        sum(removed_prices.values()),
                        sum(added_prices.values()),
                    )

            previous_quote_state = current_quote_state
            previous_price_state = current_price_state

            # Detect hedge composition changes without Antivist quote changes.
            current_composition = defaultdict(Counter)

            for order in desired_orders:
                key = (
                    order.venue,
                    order.side,
                    order.price,
                    order.quantity,
                )
                current_composition[key][order.hedge_legs] += 1

            if previous_hedge_composition is not None:
                changed = 0

                for key in current_composition.keys() & previous_hedge_composition.keys():
                    old = previous_hedge_composition[key]
                    new = current_composition[key]

                    removed = sum((old - new).values())
                    added = sum((new - old).values())

                    changed += min(removed, added)

                if changed:
                    LOGGER.info(
                        "HEDGE COMPOSITION CHANGED | market=%s "
                        "same_quote_different_legs=%s",
                        market_id,
                        changed,
                    )

            previous_hedge_composition = current_composition

            # Log only when the desired order set changes.
            desired_signature = tuple(
                (
                    order.venue,
                    order.side,
                    order.price,
                    order.quantity,
                    order.hedge_legs,
                )
                for order in desired_orders
            )

            if desired_signature != last_desired_signature:
                LOGGER.info(
                    "DESIRED ORDERS | market=%s total=%s "
                    "pascal=%s polymarket=%s",
                    market_id,
                    len(desired_orders),
                    sum(o.venue == "pascal" for o in desired_orders),
                    sum(o.venue == "polymarket" for o in desired_orders),
                )
                last_desired_signature = desired_signature

            # Live execution and dry run are mutually exclusive.
            if execute_live:
                now = time.monotonic()

                if now - last_live_sync >= LIVE_SYNC_INTERVAL:
                    last_live_sync = now

                    LOGGER.warning(
                        "LIVE RECONCILIATION START | market=%s | desired=%s",
                        market_id,
                        len(desired_orders),
                    )


                    sync_started = time.monotonic()

                    result = reconcile_live_once(
                        client=antivist_client,
                        registry=registry,
                        market_id=market_id,
                        desired_orders=tuple(desired_orders),
                    )

                    sync_latency_ms = (
                        time.monotonic() - sync_started
                    ) * 1000

                    operations_reconciliation = {
                        "last_sync": datetime.now().strftime(
                            "%H:%M:%S"
                        ),
                        "keep": result["keep"],
                        "cancel": result["canceled"],
                        "submit": result["submitted"],
                        "latency_ms": f"{sync_latency_ms:.0f} ms",
                    }


                    LOGGER.warning(
                        "ANTIVIST LIVE | market=%s KEEP=%s CANCEL=%s SUBMIT=%s",
                        market_id,
                        result["keep"],
                        result["canceled"],
                        result["submitted"],
                    )

            elif not public_demo:
                # Deterministic reconciliation.
                # DRY RUN: never submit or cancel exchange orders.
                try:
                    plan = build_reconciliation_plan(
                        desired_orders=desired_orders,
                        registered_orders=registered_orders,
                        existing_orders=existing_orders,
                        market_id=market_id,
                    )

                    diagnostics = diagnose_cancellations(
                        plan=plan,
                        registered_orders=registered_orders,
                        desired_orders=desired_orders,
                    )

                    if diagnostics:
                        LOGGER.info(
                            "DRY RUN CANCEL REASONS | %s",
                            diagnostics,
                        )

                    if execute_testnet_smoke:
                        # A pending cancellation requires reconciliation first.
                        if any(
                            decision.action.value == "CANCEL"
                            for decision in plan
                        ):
                            raise RuntimeError(
                                "Testnet smoke blocked: CANCEL decisions present"
                            )

                        submit_candidates = [
                            decision.desired_order
                            for decision in plan
                            if decision.action.value == "SUBMIT"
                            and decision.desired_order is not None
                        ]

                        if submit_candidates:
                            # Submit exactly one quote from the live hedge ladders.
                            selected = submit_candidates[0]

                            LOGGER.warning(
                                "TESTNET SUBMIT | venue=%s | side=%s | "
                                "price=%s | quantity=%s",
                                selected.venue,
                                selected.side,
                                selected.price,
                                selected.quantity,
                            )

                            results = execute_submissions(
                                client=antivist_client,
                                registry=registry,
                                market_id=market_id,
                                desired_orders=(selected,),
                            )

                            result = results[0]

                            LOGGER.warning(
                                "TESTNET RESULT | accepted=%s | "
                                "order_id=%s | status=%s | error=%s",
                                result.accepted,
                                result.order_id,
                                result.status,
                                result.error_code,
                            )

                            # An accepted resting order should be visible via the SDK.
                            if result.accepted and result.status in {
                                "OPEN",
                                "PARTIALLY_FILLED",
                            }:
                                response = antivist_client.get_orders(
                                    market_id=market_id,
                                )

                                if (
                                    not isinstance(response, dict)
                                    or response.get("ok") is not True
                                    or not isinstance(response.get("orders"), list)
                                ):
                                    raise RuntimeError(
                                        "Could not verify Antivist open orders"
                                    )

                                found = [
                                    order
                                    for order in response["orders"]
                                    if order.get("order_id") == result.order_id
                                    and order.get("client_order_id")
                                    == result.client_order_id
                                ]

                                if len(found) != 1:
                                    raise RuntimeError(
                                        "Submitted order not found in open orders; "
                                        "inspect exchange state before retrying"
                                    )

                                LOGGER.warning(
                                    "TESTNET VERIFIED | order_id=%s | "
                                    "client_order_id=%s",
                                    result.order_id,
                                    result.client_order_id,
                                )

                            # No second submit from this process.
                            return

                    else:
                        order_logger.log_plan(plan)

                    plan_signature = tuple(
                        (
                            decision.action.value,
                            (
                                decision.existing_order.order_id
                                if decision.existing_order is not None
                                else None
                            ),
                            (
                                decision.desired_order.venue,
                                decision.desired_order.side,
                                decision.desired_order.price,
                                decision.desired_order.quantity,
                            )
                            if decision.desired_order is not None
                            else None,
                        )
                        for decision in plan
                    )

                    if plan_signature != last_plan_signature:
                        counts = {
                            action: sum(
                                decision.action.value == action
                                for decision in plan
                            )
                            for action in ("KEEP", "CANCEL", "SUBMIT")
                        }

                        LOGGER.info(
                            "ORDER MANAGER DRY RUN | market=%s "
                            "KEEP=%s CANCEL=%s SUBMIT=%s",
                            market_id,
                            counts["KEEP"],
                            counts["CANCEL"],
                            counts["SUBMIT"],
                        )

                        last_plan_signature = plan_signature

                except ReconciliationBlocked as exc:
                    blocked_signature = ("BLOCKED", str(exc))

                    if blocked_signature != last_plan_signature:
                        LOGGER.warning(
                            "ORDER MANAGER BLOCKED | %s",
                            exc,
                        )
                        last_plan_signature = blocked_signature
            cycle_duration = time.monotonic() - tick_started

            if cycle_duration > 1.0:
                LOGGER.warning(
                    "RUNTIME SLOW CYCLE | duration=%.3fs",
                    cycle_duration,
                )


            # --------------------------------------------------
            # DEPTH PUBLISHING
            # --------------------------------------------------

            publish_depth(
                market_data=market_data,
                clients=clients,
                fixture_label=fixture_label,
                quotes_by_venue=quotes_by_venue,
                ladders_by_venue=ladders_by_venue,
                anchors=anchors,
                risk_ladders_by_venue=risk_ladders_by_venue,
                public_demo=public_demo,
            )

            # --------------------------------------------------
            # VENUE HEALTH
            # --------------------------------------------------
            # Update health every runtime cycle.
            # Technical logging remains limited to every 5s.
            # --------------------------------------------------

            now = time.monotonic()

            should_log_health = (
                now - last_health_log >= 5.0
            )

            for venue, client in clients.items():
                if client is None:
                    continue

                health = assess_feed_health(
                    venue=venue,
                    client=client,
                    now_monotonic=now,
                )

                age = health.last_message_age_seconds

                age_label = (
                    f"{age:.2f}s"
                    if age is not None
                    else "N/A"
                )

                # Operational state updated every cycle.
                operations_venues[venue] = {
                    "status": health.status,
                    "connected": health.connected,
                    "snapshot_ready": health.snapshot_ready,
                    "age": age_label,
                }

                # Technical logs every 5 seconds.
                if should_log_health:
                    LOGGER.info(
                        "VENUE HEALTH | %s | "
                        "status=%s | "
                        "connected=%s | "
                        "ready=%s | "
                        "last_message_age=%s",
                        venue.upper(),
                        health.status,
                        health.connected,
                        health.ready,
                        age_label,
                    )

                    if (
                        venue == "pascal"
                        and health.status == "INITIALIZING"
                    ):
                        client.debug_market_data_state()

            if should_log_health:
                last_health_log = now

                LOGGER.info(
                    "ALBRIDGE | available_venues=%s",
                    market_data.available_venues,
                )

            # --------------------------------------------------
            # OPERATIONS SNAPSHOT
            # --------------------------------------------------
            # Publish only after venue health is updated.
            # The MM Control terminal is strictly read-only.
            # --------------------------------------------------

            publish_operations_snapshot(
                market_id=market_id,
                fixture_label=fixture_label,
                desired_orders=desired_orders,
                execute_live=execute_live,
                exchange_state=operations_exchange_state,
                reconciliation=operations_reconciliation,
                venues=operations_venues,
                exchange_verified_at=operations_exchange_verified_at,
                public_demo=public_demo,
            )

    # --------------------------------------------------
    # RUNTIME SHUTDOWN
    # --------------------------------------------------

    except KeyboardInterrupt:
        LOGGER.warning("ALBRIDGE STOP REQUESTED")

    finally:
        try:
            if execute_live:
                if antivist_client is None or registry is None:
                    LOGGER.critical(
                        "PROTECTIVE SHUTDOWN UNAVAILABLE | "
                        "Antivist client or Registry missing"
                    )
                else:
                    try:
                        shutdown_managed_orders(
                            client=antivist_client,
                            registry=registry,
                            market_id=market_id,
                        )

                    except Exception:
                        LOGGER.exception(
                            "CRITICAL | PROTECTIVE SHUTDOWN FAILED | "
                            "Orders may remain open on Antivist"
                        )
                        raise

        finally:
            for client in clients.values():
                if client is not None:
                    client.stop()

            for thread in threads:
                thread.join(timeout=5)

            LOGGER.warning("ALBRIDGE STOPPED")
