"""Tests for the async Kalshi REST client."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.kalshi.models import Market, Order, OrderBook

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


@pytest.fixture()
def mock_auth() -> MagicMock:
    """Return a MagicMock KalshiAuth whose sign() returns dummy headers."""
    auth = MagicMock()
    auth.sign.return_value = {
        "KALSHI-ACCESS-KEY": "fake-key",
        "KALSHI-ACCESS-TIMESTAMP": "1700000000000",
        "KALSHI-ACCESS-SIGNATURE": "aabbccdd",
    }
    return auth


@respx.mock
async def test_get_markets(mock_auth: MagicMock) -> None:
    """Mock GET /markets and verify Market list returned."""
    respx.get(f"{BASE_URL}/markets").mock(
        return_value=httpx.Response(
            200,
            json={
                "markets": [
                    {
                        "ticker": "RAIN-NYC-24",
                        "event_ticker": "RAIN-NYC",
                        "subtitle": "Rain in NYC",
                        "yes_bid": 40,
                        "yes_ask": 45,
                        "no_bid": 55,
                        "no_ask": 60,
                        "last_price": 42,
                        "volume": 1000,
                        "open_interest": 500,
                        "status": "active",
                        "close_time": "2025-12-31T23:59:59Z",
                        "result": "",
                    },
                ],
            },
        ),
    )

    client = KalshiClient(base_url=BASE_URL, auth=mock_auth)
    try:
        markets = await client.get_markets()
        assert len(markets) == 1
        assert isinstance(markets[0], Market)
        assert markets[0].ticker == "RAIN-NYC-24"
        assert markets[0].yes_bid == 40
    finally:
        await client.close()


@respx.mock
async def test_get_batch_orderbooks(mock_auth: MagicMock) -> None:
    """Mock GET /markets/orderbooks and verify OrderBook dict."""
    respx.get(f"{BASE_URL}/markets/orderbooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderbooks": [
                    {
                        "ticker": "RAIN-NYC-24",
                        "yes": [[40, 10], [39, 20]],
                        "no": [[55, 15]],
                    },
                ],
            },
        ),
    )

    client = KalshiClient(base_url=BASE_URL, auth=mock_auth)
    try:
        books = await client.get_batch_orderbooks(["RAIN-NYC-24"])
        assert "RAIN-NYC-24" in books
        book = books["RAIN-NYC-24"]
        assert isinstance(book, OrderBook)
        assert len(book.yes) == 2
        assert book.yes[0].price == 40
    finally:
        await client.close()


@respx.mock
async def test_create_order(mock_auth: MagicMock) -> None:
    """Mock POST /portfolio/orders and verify Order returned."""
    respx.post(f"{BASE_URL}/portfolio/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "order": {
                    "order_id": "ord-123",
                    "ticker": "RAIN-NYC-24",
                    "side": "yes",
                    "action": "buy",
                    "type": "limit",
                    "yes_price": 42,
                    "no_price": 58,
                    "count": 5,
                    "status": "resting",
                    "created_time": "2025-06-01T12:00:00Z",
                },
            },
        ),
    )

    client = KalshiClient(base_url=BASE_URL, auth=mock_auth)
    try:
        order = await client.create_order(
            ticker="RAIN-NYC-24",
            side="yes",
            action="buy",
            yes_price=42,
            no_price=58,
            count=5,
        )
        assert isinstance(order, Order)
        assert order.order_id == "ord-123"
        assert order.side == "yes"
        assert order.count == 5
    finally:
        await client.close()


@respx.mock
async def test_get_balance(mock_auth: MagicMock) -> None:
    """Mock GET /portfolio/balance and verify int returned."""
    respx.get(f"{BASE_URL}/portfolio/balance").mock(
        return_value=httpx.Response(200, json={"balance": 50000}),
    )

    client = KalshiClient(base_url=BASE_URL, auth=mock_auth)
    try:
        balance = await client.get_balance()
        assert balance == 50000
        assert isinstance(balance, int)
    finally:
        await client.close()
