"""Tests for the Kalshi WebSocket client."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from niche_scanner.kalshi.websocket import (
    KalshiWebSocket,
    OrderBookDelta,
    WebSocketConfig,
)


# ---------------------------------------------------------------------------
# Mock auth
# ---------------------------------------------------------------------------


class _MockAuth:
    """Fake KalshiAuth that returns predictable signing output."""

    api_key_id = "test-key-123"

    def sign(
        self, timestamp_ms: int, method: str, path: str,
    ) -> dict[str, str]:
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": "mock-sig-base64",
        }


def _mock_auth() -> _MockAuth:
    return _MockAuth()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockWsConnection:
    """Fake WebSocket connection for testing.

    ``messages[0]`` is returned by ``recv()`` (used during auth).
    ``messages[1:]`` are yielded by ``async for msg in connection:``.
    """

    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent: list[str] = []
        self.close = AsyncMock()

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return self._messages[0] if self._messages else '{"id": 1}'

    async def __aiter__(self):
        for msg in self._messages[1:]:
            yield msg


def _mock_connection(recv_messages: list[str] | None = None) -> _MockWsConnection:
    """Build a mock WebSocket connection."""
    return _MockWsConnection(recv_messages or ['{"id": 1}'])


# ---------------------------------------------------------------------------
# Test 1: URL derivation
# ---------------------------------------------------------------------------


def test_derive_ws_url_production() -> None:
    """Production REST URL converts to WSS correctly."""
    rest = "https://api.elections.kalshi.com/trade-api/v2"
    assert KalshiWebSocket._derive_ws_url(rest) == (
        "wss://api.elections.kalshi.com/trade-api/ws/v2"
    )


def test_derive_ws_url_demo() -> None:
    """Demo REST URL converts to WSS correctly."""
    rest = "https://demo-api.kalshi.co/trade-api/v2"
    assert KalshiWebSocket._derive_ws_url(rest) == (
        "wss://demo-api.kalshi.co/trade-api/ws/v2"
    )


# ---------------------------------------------------------------------------
# Test 2: connect + authenticate
# ---------------------------------------------------------------------------


async def test_connect_sends_login() -> None:
    """connect() should open a WebSocket and send a login command."""
    auth_response = json.dumps({"id": 1})
    conn = _mock_connection([auth_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()

    # Verify login message was sent
    assert len(conn.sent) == 1
    sent = json.loads(conn.sent[0])
    assert sent["cmd"] == "login"
    assert sent["params"]["api_key"] == "test-key-123"
    assert "signature" in sent["params"]
    assert "timestamp" in sent["params"]
    assert ws.connected


# ---------------------------------------------------------------------------
# Test 3: auth failure raises
# ---------------------------------------------------------------------------


async def test_auth_failure_raises() -> None:
    """connect() should raise ConnectionError on auth failure."""
    error_response = json.dumps({"error": "invalid signature"})
    conn = _mock_connection([error_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        try:
            await ws.connect()
            assert False, "Should have raised"  # noqa: B011
        except ConnectionError as exc:
            assert "invalid signature" in str(exc)


# ---------------------------------------------------------------------------
# Test 4: subscribe sends correct message
# ---------------------------------------------------------------------------


async def test_subscribe_orderbook() -> None:
    """subscribe_orderbook() sends a subscribe command with correct channels."""
    auth_response = json.dumps({"id": 1})
    conn = _mock_connection([auth_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()

        await ws.subscribe_orderbook(["TICK-A", "TICK-B"])

    # Last sent message should be the subscribe command
    sent = json.loads(conn.sent[-1])
    assert sent["cmd"] == "subscribe"
    assert sent["params"]["channels"] == ["orderbook_delta"]
    assert set(sent["params"]["market_tickers"]) == {"TICK-A", "TICK-B"}
    assert ws.subscribed_tickers == frozenset({"TICK-A", "TICK-B"})


# ---------------------------------------------------------------------------
# Test 5: duplicate subscribe is deduplicated
# ---------------------------------------------------------------------------


async def test_subscribe_deduplication() -> None:
    """Subscribing to already-subscribed tickers should not re-send."""
    auth_response = json.dumps({"id": 1})
    conn = _mock_connection([auth_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()

        await ws.subscribe_orderbook(["TICK-A"])
        count_after_first = len(conn.sent)

        await ws.subscribe_orderbook(["TICK-A"])
        assert len(conn.sent) == count_after_first


# ---------------------------------------------------------------------------
# Test 6: unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_orderbook() -> None:
    """unsubscribe_orderbook() sends an unsubscribe command."""
    auth_response = json.dumps({"id": 1})
    conn = _mock_connection([auth_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()
        await ws.subscribe_orderbook(["TICK-A", "TICK-B"])

        await ws.unsubscribe_orderbook(["TICK-A"])

    sent = json.loads(conn.sent[-1])
    assert sent["cmd"] == "unsubscribe"
    assert ws.subscribed_tickers == frozenset({"TICK-B"})


# ---------------------------------------------------------------------------
# Test 7: parse delta
# ---------------------------------------------------------------------------


def test_parse_delta_valid() -> None:
    """Valid delta messages parse correctly."""
    msg = {
        "market_ticker": "KXHIGHNY-26APR04-T75",
        "price": 45,
        "delta": 10,
        "side": "yes",
    }
    delta = KalshiWebSocket._parse_delta(msg)
    assert delta is not None
    assert delta.market_ticker == "KXHIGHNY-26APR04-T75"
    assert delta.price == 45
    assert delta.delta == 10
    assert delta.side == "yes"


def test_parse_delta_missing_field() -> None:
    """Missing fields return None."""
    assert KalshiWebSocket._parse_delta({"price": 45}) is None


def test_parse_delta_negative() -> None:
    """Negative deltas (quantity removed) parse correctly."""
    msg = {
        "market_ticker": "TICK-1",
        "price": 30,
        "delta": -5,
        "side": "no",
    }
    delta = KalshiWebSocket._parse_delta(msg)
    assert delta is not None
    assert delta.delta == -5


# ---------------------------------------------------------------------------
# Test 8: receive loop enqueues deltas
# ---------------------------------------------------------------------------


async def test_receive_loop_enqueues_deltas() -> None:
    """_receive_loop should parse and enqueue orderbook_delta messages."""
    auth_response = json.dumps({"id": 1})
    delta_msg = json.dumps({
        "type": "orderbook_delta",
        "msg": {
            "market_ticker": "TICK-1",
            "price": 50,
            "delta": 3,
            "side": "yes",
        },
    })
    conn = _mock_connection([auth_response, delta_msg])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)
        mock_ws.ConnectionClosed = Exception  # For the except clause

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()

        # Run the receive loop briefly
        loop_task = asyncio.create_task(ws._receive_loop())
        await asyncio.sleep(0.05)

        assert not ws._update_queue.empty()
        delta = ws._update_queue.get_nowait()
        assert delta.market_ticker == "TICK-1"
        assert delta.price == 50
        assert delta.delta == 3

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Test 9: non-delta messages are ignored
# ---------------------------------------------------------------------------


async def test_non_delta_messages_ignored() -> None:
    """Messages that aren't orderbook_delta should not enqueue."""
    auth_response = json.dumps({"id": 1})
    other_msg = json.dumps({"type": "subscribed", "msg": {"channel": "orderbook_delta"}})
    conn = _mock_connection([auth_response, other_msg])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)
        mock_ws.ConnectionClosed = Exception

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()

        loop_task = asyncio.create_task(ws._receive_loop())
        await asyncio.sleep(0.05)

        assert ws._update_queue.empty()

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Test 10: close clears state
# ---------------------------------------------------------------------------


async def test_close_clears_state() -> None:
    """close() should reset connection, subscriptions, and running flag."""
    auth_response = json.dumps({"id": 1})
    conn = _mock_connection([auth_response])

    with patch("niche_scanner.kalshi.websocket.websockets") as mock_ws:
        mock_ws.connect = AsyncMock(return_value=conn)

        ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
        await ws.connect()
        await ws.subscribe_orderbook(["TICK-A"])

        assert ws.connected
        assert len(ws.subscribed_tickers) == 1

        await ws.close()

        assert not ws.connected
        assert len(ws.subscribed_tickers) == 0
        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test 11: subscribe without connect raises
# ---------------------------------------------------------------------------


async def test_subscribe_without_connect_raises() -> None:
    """subscribe_orderbook() before connect() should raise RuntimeError."""
    ws = KalshiWebSocket(auth=_mock_auth(), base_url="https://demo-api.kalshi.co/trade-api/v2")
    try:
        await ws.subscribe_orderbook(["TICK-A"])
        assert False, "Should have raised"  # noqa: B011
    except RuntimeError as exc:
        assert "Not connected" in str(exc)


# ---------------------------------------------------------------------------
# Test 12: config defaults
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    """WebSocketConfig should have reasonable defaults."""
    cfg = WebSocketConfig()
    assert cfg.ping_interval_sec == 30.0
    assert cfg.max_reconnect_attempts == 5
    assert cfg.reconnect_backoff_factor == 2.0


# ---------------------------------------------------------------------------
# Test 13: OrderBookDelta is immutable
# ---------------------------------------------------------------------------


def test_delta_is_frozen() -> None:
    """OrderBookDelta should be immutable (frozen dataclass)."""
    delta = OrderBookDelta(
        market_ticker="T1", price=50, delta=3, side="yes",
    )
    try:
        delta.price = 99  # type: ignore[misc]
        assert False, "Should have raised"  # noqa: B011
    except AttributeError:
        pass  # Expected: frozen dataclass
