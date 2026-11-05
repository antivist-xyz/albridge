"""Polymarket public CLOB WebSocket client and raw recorder."""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

from albridge.venues.polymarket_order_book import PolymarketOrderBook
from albridge.venues.polymarket_snapshot import (
    build_polymarket_snapshot,
)


LOGGER = logging.getLogger(__name__)

POLYMARKET_WS_URL = (
    "wss://ws-subscriptions-clob.polymarket.com/ws/market"
)


class PolymarketWebSocketClient:
    def __init__(
        self,
        *,
        outcome_tokens: dict[str, dict[str, str]],
        recording_path: Path,
    ):
        self.outcome_tokens = outcome_tokens
        self.recording_path = recording_path
        self.recording_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.connected = False
        self.last_message_monotonic = None

        # Subscribe only to YES tokens.
        # Keep NO token metadata available in the resolver for future execution.
        if set(outcome_tokens) != {"WIN", "DRAW", "LOSS"}:
            raise ValueError("Expected WIN, DRAW and LOSS")

        self.token_roles = {}

        for outcome in ("WIN", "DRAW", "LOSS"):
            token_id = outcome_tokens[outcome]["yes_token_id"]

            if not token_id:
                raise ValueError(f"Missing YES token for {outcome}")

            if token_id in self.token_roles:
                raise ValueError("Duplicate YES token ID")

            self.token_roles[token_id] = (outcome, "YES")

        self.books = {
            token_id: PolymarketOrderBook(asset_id=token_id)
            for token_id in self.token_roles
        }

        self.market_snapshot = None

        if len(self.token_roles) != 3:
            raise ValueError("Expected three unique token IDs")

        self._ws = None
        self._stop = threading.Event()
        self._heartbeat_stop = threading.Event()

    def _on_open(self, ws):
        self._invalidate_books()
        self.connected = True
        self.last_message_monotonic = time.monotonic()
        self.last_pong_monotonic = time.monotonic()
        LOGGER.info("POLYMARKET CONNECTED")

        ws.send(json.dumps({
            "assets_ids": list(self.token_roles),
            "type": "market",
        }))

        LOGGER.info(
            "POLYMARKET SUBSCRIBED | tokens=%d",
            len(self.token_roles),
        )

        self._heartbeat_stop.clear()
        # Start the heartbeat watchdog from connection establishment.
        self.last_pong_monotonic = time.monotonic()

        def heartbeat():
            """Monitor application-level PING/PONG responses."""

            while not self._heartbeat_stop.wait(10):
                if self._stop.is_set():
                    break

                last_pong = self.last_pong_monotonic

                if (
                    last_pong is None
                    or time.monotonic() - last_pong > 30
                ):
                    LOGGER.error("POLYMARKET HEARTBEAT TIMEOUT")
                    self.connected = False
                    self._invalidate_books()
                    ws.close()
                    break

                try:
                    ws.send("PING")
                except websocket.WebSocketException:
                    LOGGER.exception("POLYMARKET HEARTBEAT FAILED")
                    self.connected = False
                    self._invalidate_books()
                    ws.close()
                    break

    def _on_message(self, ws, raw_message):
        if raw_message == "PONG":
            self.last_pong_monotonic = time.monotonic()

            LOGGER.debug(
                "POLYMARKET PONG RECEIVED"
            )
            return

        try:
            message = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            LOGGER.warning("POLYMARKET INVALID JSON")
            return

        self.last_message_monotonic = time.monotonic()

        record = {
            "received_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "received_monotonic": time.monotonic(),
            "message": message,
        }

        with self.recording_path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(json.dumps(record) + "\n")

        # Polymarket can send multiple events in one WebSocket message.
        events = message if isinstance(message, list) else [message]

        for event in events:
            if not isinstance(event, dict):
                continue

            event_type = event.get("event_type")

            if event_type == "book":
                token_id = str(event.get("asset_id"))
                book = self.books.get(token_id)

                if book is None:
                    continue

                try:
                    applied = book.apply_snapshot(event)
                except (ValueError, TypeError, KeyError) as exc:
                    LOGGER.error(
                        "POLYMARKET BOOK INVALID | token=%s | error=%s",
                        token_id,
                        exc,
                    )
                    self._invalidate_books()
                    ws.close()
                    return

                if applied:
                    self._log_book(token_id)
                    self._refresh_market_snapshot()

            elif event_type == "price_change":
                try:
                    timestamp_ms = int(event["timestamp"])

                    changed = False

                    for change in event.get("price_changes", []):
                        token_id = str(change.get("asset_id"))
                        book = self.books.get(token_id)

                        if book is None:
                            continue

                        applied = book.apply_change(
                            change,
                            timestamp_ms,
                        )

                        if applied:
                            changed = True
                            self._log_book(token_id)

                    if changed:
                        self._refresh_market_snapshot()

                except (ValueError, TypeError, KeyError) as exc:
                    LOGGER.error(
                        "POLYMARKET UPDATE INVALID | error=%s",
                        exc,
                    )
                    self._invalidate_books()
                    ws.close()
                    return

            else:
                LOGGER.debug(
                    "POLYMARKET EVENT | type=%s",
                    event_type,
                )
    def _refresh_market_snapshot(self):
        """Publish a normalized WIN/DRAW/LOSS snapshot."""

        self.market_snapshot = build_polymarket_snapshot(
            books=self.books,
            outcome_tokens=self.outcome_tokens,
        )

        if self.market_snapshot is None:
            return

        snapshot = self.market_snapshot

        LOGGER.debug(
            "POLYMARKET SNAPSHOT | "
            "WIN=%s/%s | DRAW=%s/%s | LOSS=%s/%s",
            snapshot.get("WIN").best_bid,
            snapshot.get("WIN").best_ask,
            snapshot.get("DRAW").best_bid,
            snapshot.get("DRAW").best_ask,
            snapshot.get("LOSS").best_bid,
            snapshot.get("LOSS").best_ask,
        )
        LOGGER.debug(
            "POLYMARKET HEALTH | connected=%s | ready=%s | last_message=%s",
            self.connected,
            self.market_data_ready,
            self.last_message_monotonic,
        )

    def _invalidate_books(self):
        """Invalidate all books and the aggregated snapshot."""

        self.market_snapshot = None

        for book in self.books.values():
            book.reset()


    def _log_book(self, token_id: str):
        """Log the current order-book state."""
        book = self.books[token_id]
        outcome, side = self.token_roles[token_id]

        LOGGER.debug(
            "POLYMARKET BOOK | %s %s | "
            "bid=%s | ask=%s | spread=%s | "
            "bid_levels=%d | ask_levels=%d",
            outcome,
            side,
            book.best_bid,
            book.best_ask,
            book.spread,
            len(book.bids),
            len(book.asks),
        )

    def _on_error(self, ws, error):
        self.connected = False
        self.last_message_monotonic = None
        self._invalidate_books()

        LOGGER.error("POLYMARKET WS ERROR | %s", error)

    def _on_close(self, ws, status_code, message):
        self._invalidate_books()
        self._heartbeat_stop.set()
        self.connected = False
        self.last_message_monotonic = None
        self.last_pong_monotonic = None

        LOGGER.warning(
            "POLYMARKET DISCONNECTED | code=%s | reason=%s",
            status_code,
            message,
        )

    def start(self):
        while not self._stop.is_set():
            self._ws = websocket.WebSocketApp(
                POLYMARKET_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            self._ws.run_forever(
                ping_interval=0,
            )

            self._heartbeat_stop.set()

            if not self._stop.is_set():
                LOGGER.warning(
                    "POLYMARKET RECONNECTING IN 3 SECONDS"
                )
                self._stop.wait(3)

    def stop(self):
        self._stop.set()
        self._heartbeat_stop.set()
        self.connected = False
        self.last_message_monotonic = None
        self._invalidate_books()
        self.last_pong_monotonic = None

        if self._ws is not None:
            self._ws.close()

    @property
    def market_data_ready(self) -> bool:
        """Check connection state and complete market-data availability."""
        return (
            self.connected
            and self.market_snapshot is not None
        )
