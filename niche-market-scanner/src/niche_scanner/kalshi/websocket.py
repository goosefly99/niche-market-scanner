"""Async WebSocket client for real-time Kalshi orderbook streaming.

Connects to the Kalshi WebSocket API, authenticates with RSA-PSS,
and streams orderbook delta updates for subscribed tickers.

Usage::

    ws = KalshiWebSocket(auth=auth, base_url=kalshi_cfg.base_url)
    await ws.connect()
    await ws.subscribe_orderbook(["KXHIGHNY-26APR04-T75"])

    async for delta in ws.updates():
        print(f"{delta.market_ticker} {delta.side} {delta.price}: {delta.delta}")

    await ws.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import websockets

from niche_scanner.kalshi.auth import KalshiAuth

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderBookDelta:
    """A single orderbook update from the WebSocket feed.

    Attributes:
        market_ticker: Kalshi ticker (e.g. ``KXHIGHNY-26APR04-T75``).
        price: Price level in cents (0-99).
        delta: Quantity change (positive = added, negative = removed).
        side: ``"yes"`` or ``"no"``.
    """

    market_ticker: str
    price: int
    delta: int
    side: str


@dataclass(frozen=True, slots=True)
class WebSocketConfig:
    """Tuning parameters for the WebSocket client."""

    ping_interval_sec: float = 30.0
    ping_timeout_sec: float = 10.0
    recv_timeout_sec: float = 1.0
    max_reconnect_attempts: int = 5
    reconnect_base_delay_sec: float = 1.0
    reconnect_backoff_factor: float = 2.0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class KalshiWebSocket:
    """Async WebSocket client for real-time Kalshi market data.

    The client connects to the Kalshi WebSocket API, authenticates
    using RSA-PSS signed credentials, and streams orderbook delta
    updates into an ``asyncio.Queue`` consumed via :meth:`updates`.
    """

    def __init__(
        self,
        auth: KalshiAuth,
        base_url: str,
        config: WebSocketConfig | None = None,
    ) -> None:
        self._auth = auth
        self._ws_url = self._derive_ws_url(base_url)
        self._config = config or WebSocketConfig()
        self._connection: websockets.ClientConnection | None = None
        self._subscribed_tickers: set[str] = set()
        self._msg_id = 0
        self._running = False
        self._update_queue: asyncio.Queue[OrderBookDelta] = asyncio.Queue()

    # ------------------------------------------------------------------
    # URL derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_ws_url(base_url: str) -> str:
        """Convert a Kalshi REST base URL to the WebSocket endpoint.

        ``https://api.elections.kalshi.com/trade-api/v2``
        → ``wss://api.elections.kalshi.com/trade-api/ws/v2``
        """
        return (
            base_url
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            .replace("/trade-api/v2", "/trade-api/ws/v2")
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        """Return a monotonically increasing message ID."""
        self._msg_id += 1
        return self._msg_id

    async def _send(self, msg: dict) -> None:
        """Serialize and send a JSON message."""
        assert self._connection is not None
        await self._connection.send(json.dumps(msg))

    async def _recv(self) -> dict:
        """Receive and deserialize a JSON message."""
        assert self._connection is not None
        raw = await self._connection.recv()
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket connection and authenticate."""
        self._connection = await websockets.connect(
            self._ws_url,
            ping_interval=self._config.ping_interval_sec,
            ping_timeout=self._config.ping_timeout_sec,
        )
        await self._authenticate()
        self._running = True
        logger.info("Connected to Kalshi WebSocket at %s", self._ws_url)

    async def _authenticate(self) -> None:
        """Send a login command signed with RSA-PSS credentials."""
        timestamp_ms = int(time.time() * 1000)
        headers = self._auth.sign(timestamp_ms, "GET", "/trade-api/ws/v2")

        await self._send({
            "id": self._next_id(),
            "cmd": "login",
            "params": {
                "api_key": headers["KALSHI-ACCESS-KEY"],
                "timestamp": int(headers["KALSHI-ACCESS-TIMESTAMP"]),
                "signature": headers["KALSHI-ACCESS-SIGNATURE"],
            },
        })

        response = await self._recv()
        if response.get("error"):
            raise ConnectionError(
                f"WebSocket auth failed: {response['error']}",
            )
        logger.debug("WebSocket authentication successful")

    async def close(self) -> None:
        """Close the WebSocket connection and clear state."""
        self._running = False
        if self._connection:
            await self._connection.close()
            self._connection = None
        self._subscribed_tickers.clear()
        logger.info("WebSocket connection closed")

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def subscribe_orderbook(self, tickers: list[str]) -> None:
        """Subscribe to orderbook delta updates for the given tickers."""
        if not self._connection:
            raise RuntimeError("Not connected. Call connect() first.")

        new_tickers = [t for t in tickers if t not in self._subscribed_tickers]
        if not new_tickers:
            return

        await self._send({
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": new_tickers,
            },
        })
        self._subscribed_tickers.update(new_tickers)
        logger.info(
            "Subscribed to orderbook for %d ticker(s)", len(new_tickers),
        )

    async def unsubscribe_orderbook(self, tickers: list[str]) -> None:
        """Unsubscribe from orderbook updates for the given tickers."""
        if not self._connection:
            return

        remove = [t for t in tickers if t in self._subscribed_tickers]
        if not remove:
            return

        await self._send({
            "id": self._next_id(),
            "cmd": "unsubscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": remove,
            },
        })
        self._subscribed_tickers -= set(remove)
        logger.info("Unsubscribed from %d ticker(s)", len(remove))

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_delta(msg: dict) -> OrderBookDelta | None:
        """Parse a raw delta payload into an :class:`OrderBookDelta`."""
        try:
            return OrderBookDelta(
                market_ticker=msg["market_ticker"],
                price=int(msg["price"]),
                delta=int(msg["delta"]),
                side=msg["side"],
            )
        except (KeyError, ValueError, TypeError):
            logger.debug("Unparseable delta message: %s", msg)
            return None

    async def _receive_loop(self) -> None:
        """Read messages from the WebSocket and enqueue deltas."""
        assert self._connection is not None
        try:
            async for raw in self._connection:
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "orderbook_delta":
                    delta = self._parse_delta(data.get("msg", {}))
                    if delta:
                        await self._update_queue.put(delta)
                elif msg_type == "error":
                    logger.warning("WebSocket error: %s", data.get("msg"))
                else:
                    logger.debug("WS message type=%s", msg_type)
        except websockets.ConnectionClosed as exc:
            logger.warning("WebSocket connection closed: %s", exc)
        finally:
            self._running = False

    async def updates(self) -> asyncio.Queue[OrderBookDelta]:
        """Start the receive loop and return the update queue.

        Callers consume deltas via ``await queue.get()`` or
        ``queue.get_nowait()``.  The receive loop runs as a background
        task until the connection is closed.

        Returns the queue so consumers can integrate it into their
        own event loop (e.g. ``asyncio.wait`` with other coroutines).
        """
        if not self._connection:
            raise RuntimeError("Not connected.")

        asyncio.create_task(self._receive_loop())
        return self._update_queue

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """True if the WebSocket is open and authenticated."""
        return self._connection is not None and self._running

    @property
    def subscribed_tickers(self) -> frozenset[str]:
        """Currently subscribed tickers (immutable view)."""
        return frozenset(self._subscribed_tickers)

    @property
    def ws_url(self) -> str:
        """The derived WebSocket URL."""
        return self._ws_url
