"""Pascal market-data WebSocket client and raw recorder."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

from albridge.venues.order_book import PascalOrderBook
from albridge.market_snapshot import build_pascal_snapshot


LOGGER = logging.getLogger(__name__)
PASCAL_WS_URL = "wss://data.pascal.trade/ws"


class PascalWebSocketClient:
    def __init__(
        self,
        *,
        symbols: list[str],
        recording_path: Path,
        outcome_symbols: dict[str, str] | None = None,
    ):
        self.symbols = symbols
        self.recording_path = recording_path
        self.recording_path.parent.mkdir(parents=True, exist_ok=True)

        self._ws = None
        self._stop = False

        # Maintain one independent order book per Pascal instrument.
        self.books = {
            symbol: PascalOrderBook(symbol)
            for symbol in symbols
        }

        # Map Pascal instruments to Antivist-relative outcomes.
        self.outcome_symbols = outcome_symbols

        # Latest complete WIN/DRAW/LOSS snapshot.
        self.market_snapshot = None

        self.connected = False
        self.last_message_monotonic = None

    def _on_open(self, ws):
        for book in self.books.values():
            book.reset()

        self.market_snapshot = None
        self.connected = True
        self.last_message_monotonic = time.monotonic()

        LOGGER.info("PASCAL CONNECTED")

        channels = [{"channel": "market_update"}]

        for symbol in self.symbols:
            channels.append({"channel": "book", "symbol": symbol})
            channels.append({"channel": "trades", "symbol": symbol})

        ws.send(json.dumps({
            "type": "subscribe",
            "channels": channels,
        }))

        LOGGER.info("SUBSCRIBED | symbols=%s", self.symbols)

    def _on_message(self, ws, raw_message):
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            LOGGER.warning("INVALID JSON MESSAGE")
            return

        # Track the last successfully decoded WebSocket message.
        self.last_message_monotonic = time.monotonic()

        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "received_monotonic": time.monotonic(),
            "message": message,
        }

        with self.recording_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")

        if message.get("channel") == "book":
            symbol = message.get("symbol")
            book = self.books.get(symbol)

            if book is not None:
                try:
                    applied = book.apply(message)
                except (ValueError, TypeError, KeyError) as exc:
                    book.reset()
                    self.market_snapshot = None

                    LOGGER.error(
                        "BOOK INVALID | symbol=%s | error=%s",
                        symbol,
                        exc,
                    )
                    ws.close()
                    return

                if applied:
                    LOGGER.info(
                        "BOOK | %s | bid=%s | ask=%s | spread=%s "
                        "| bid_levels=%d | ask_levels=%d",
                        symbol,
                        book.best_bid,
                        book.best_ask,
                        book.spread,
                        len(book.bids),
                        len(book.asks),
                    )
                if applied and self.outcome_symbols is not None:
                    self.market_snapshot = build_pascal_snapshot(
                        books=self.books,
                        symbols=self.outcome_symbols,
                    )

                    if self.market_snapshot is not None:
                        snapshot = self.market_snapshot

                        LOGGER.info(
                            "PASCAL SNAPSHOT | "
                            "WIN=%s/%s | DRAW=%s/%s | LOSS=%s/%s",
                            snapshot.get("WIN").best_bid,
                            snapshot.get("WIN").best_ask,
                            snapshot.get("DRAW").best_bid,
                            snapshot.get("DRAW").best_ask,
                            snapshot.get("LOSS").best_bid,
                            snapshot.get("LOSS").best_ask,
                        )
                        LOGGER.info(
                                    "PASCAL HEALTH | connected=%s | ready=%s | last_message=%s",
                                    self.connected,
                                    self.market_data_ready,
                                    self.last_message_monotonic,
                                )

        channel = message.get("channel", "unknown")
        event_type = message.get("type", "unknown")
        symbol = message.get("symbol", "-")
        seq = message.get("seq", "-")

        LOGGER.info(
            "PASCAL | channel=%s | type=%s | symbol=%s | seq=%s",
            channel, event_type, symbol, seq,
        )

        if event_type == "error":
            LOGGER.error("PASCAL ERROR | %s", message.get("data"))

    def _on_error(self, ws, error):
        self.connected = False
        self.last_message_monotonic = None
        self._invalidate_books()
        LOGGER.error("WEBSOCKET ERROR | %s", error)

    def _on_close(self, ws, status_code, message):
        self.connected = False
        self.last_message_monotonic = None
        self._invalidate_books()
        for book in self.books.values():
            book.reset()

        self.market_snapshot = None

        LOGGER.warning(
            "PASCAL DISCONNECTED | code=%s | reason=%s",
            status_code, message,
        )

    def start(self):
        # Reconnect with a fresh subscription and snapshot.
        while not self._stop:
            self._ws = websocket.WebSocketApp(
                PASCAL_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            self._ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
            )

            if not self._stop:
                LOGGER.warning("RECONNECTING IN 3 SECONDS")
                time.sleep(3)

    def stop(self):
        self._stop = True
        self.connected = False
        self.last_message_monotonic = None
        self._invalidate_books()
        if self._ws is not None:
            self._ws.close()

    def _invalidate_books(self):
        """Invalidate Pascal market data after a connection failure."""
        self.market_snapshot = None

        for book in self.books.values():
            book.reset()

    @property
    def market_data_ready(self) -> bool:
        """Check connection and complete market-data availability."""
        return (
            self.connected
            and self.market_snapshot is not None
        )

    def debug_market_data_state(self):
        """Report why the Pascal market snapshot is not ready."""

        LOGGER.info(
            "PASCAL DEBUG | connected=%s | "
            "snapshot=%s | outcome_symbols=%s",
            self.connected,
            self.market_snapshot is not None,
            self.outcome_symbols,
        )

        for symbol, book in self.books.items():
            LOGGER.info(
                "PASCAL DEBUG BOOK | symbol=%s | "
                "initialized=%s | valid=%s | "
                "bid=%s | ask=%s",
                symbol,
                book.initialized,
                book.valid_for_quoting,
                book.best_bid,
                book.best_ask,
            )