"""Async HTTP client for the Kalshi REST API."""

from __future__ import annotations

import asyncio
import time

import httpx

from niche_scanner.kalshi.auth import KalshiAuth
from niche_scanner.kalshi.models import Market, Order, OrderBook


class KalshiClient:
    """Rate-limited async client for the Kalshi trading API.

    Parameters
    ----------
    base_url:
        Kalshi API base URL (e.g. ``https://trading-api.kalshi.com/trade-api/v2``).
    auth:
        A :class:`KalshiAuth` instance used to sign every request.
    max_requests_per_second:
        Maximum concurrent requests (enforced via semaphore).
    """

    def __init__(
        self,
        base_url: str,
        auth: KalshiAuth,
        max_requests_per_second: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._semaphore = asyncio.Semaphore(max_requests_per_second)
        self._client = httpx.AsyncClient()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self, method: str, path: str) -> dict[str, str]:
        """Build signed headers for the given *method* and *path*."""
        timestamp_ms = int(time.time() * 1000)
        return self._auth.sign(timestamp_ms, method, path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        """Send a signed request and return the JSON response body."""
        url = f"{self._base_url}{path}"
        headers = self._headers(method, path)
        async with self._semaphore:
            response = await self._client.request(
                method, url, headers=headers, params=params, json=json,
            )
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        status: str | None = None,
        series_ticker: str | None = None,
        limit: int = 100,
    ) -> list[Market]:
        """Fetch a list of markets, optionally filtered."""
        params: dict[str, str | int] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if series_ticker is not None:
            params["series_ticker"] = series_ticker
        data = await self._request("GET", "/markets", params=params)
        return [Market(**m) for m in data.get("markets", [])]

    async def get_batch_orderbooks(
        self, tickers: list[str],
    ) -> dict[str, OrderBook]:
        """Fetch order books for up to 100 tickers in a single call."""
        if len(tickers) > 100:
            msg = "Kalshi allows at most 100 tickers per batch orderbook call"
            raise ValueError(msg)
        params: dict[str, str] = {"tickers": ",".join(tickers)}
        data = await self._request(
            "GET", "/markets/orderbooks", params=params,
        )
        result: dict[str, OrderBook] = {}
        for ob in data.get("orderbooks", []):
            book = OrderBook(**ob)
            result[book.ticker] = book
        return result

    async def create_order(
        self,
        ticker: str,
        side: str,
        action: str,
        yes_price: int,
        no_price: int,
        count: int,
        time_in_force: str = "gtc",
        post_only: bool = False,
    ) -> Order:
        """Place an order on Kalshi and return the resulting Order."""
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            "yes_price": yes_price,
            "no_price": no_price,
            "count": count,
            "time_in_force": time_in_force,
            "post_only": post_only,
        }
        data = await self._request("POST", "/portfolio/orders", json=body)
        return Order(**data["order"])

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an existing order."""
        await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def get_balance(self) -> int:
        """Return the portfolio balance in cents."""
        data = await self._request("GET", "/portfolio/balance")
        return int(data["balance"])

    async def get_positions(self) -> list[dict]:
        """Return current portfolio positions."""
        data = await self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
