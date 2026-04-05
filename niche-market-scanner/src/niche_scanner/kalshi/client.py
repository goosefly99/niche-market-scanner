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
        """Build signed headers for the given *method* and *path*.

        The signing message must use the full path from root
        (e.g. /trade-api/v2/markets), not just the relative path.
        """
        timestamp_ms = int(time.time() * 1000)
        # Extract the URL path component from base_url for signing
        from urllib.parse import urlparse
        base_path = urlparse(self._base_url).path.rstrip("/")
        full_path = f"{base_path}{path}"
        return self._auth.sign(timestamp_ms, method.upper(), full_path)

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
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> list[Market]:
        """Fetch a list of markets, optionally filtered by series or event."""
        params: dict[str, str | int] = {"limit": limit}
        if series_ticker is not None:
            params["series_ticker"] = series_ticker
        if event_ticker is not None:
            params["event_ticker"] = event_ticker
        if cursor is not None:
            params["cursor"] = cursor
        data = await self._request("GET", "/markets", params=params)
        return [Market(**m) for m in data.get("markets", [])]

    async def get_active_markets(
        self,
        limit: int = 200,
        cursor: str | None = None,
    ) -> tuple[list[Market], str]:
        """Fetch open markets across all categories for broad discovery.

        Unlike :meth:`get_markets`, this method filters by
        ``status=open`` and does not require a series or event ticker,
        returning markets from any category (politics, crypto, sports,
        etc.).  Pagination is supported via the returned cursor.

        Parameters
        ----------
        limit:
            Maximum number of markets to return in a single page
            (Kalshi caps this at 200).
        cursor:
            Pagination cursor from a previous call.  Pass ``None``
            for the first page.

        Returns
        -------
        tuple[list[Market], str]
            A ``(markets, next_cursor)`` pair.  When ``next_cursor``
            is empty the final page has been reached.
        """
        params: dict[str, str | int] = {
            "limit": min(limit, 200),
            "status": "open",
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = await self._request("GET", "/markets", params=params)
        markets = [Market(**m) for m in data.get("markets", [])]
        next_cursor: str = data.get("cursor", "")
        return markets, next_cursor

    async def get_events(
        self,
        status: str | None = "open",
        series_ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[dict], str]:
        """Fetch events, optionally filtered. Returns (events, cursor)."""
        params: dict[str, str | int] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if series_ticker is not None:
            params["series_ticker"] = series_ticker
        if cursor is not None:
            params["cursor"] = cursor
        data = await self._request("GET", "/events", params=params)
        return data.get("events", []), data.get("cursor", "")

    async def get_orderbook(self, ticker: str) -> OrderBook | None:
        """Fetch orderbook for a single ticker."""
        try:
            data = await self._request("GET", f"/markets/{ticker}/orderbook")
            ob_data = data.get("orderbook", data)
            ob_data["ticker"] = ticker
            return OrderBook(**ob_data)
        except Exception:
            return None

    async def get_batch_orderbooks(
        self, tickers: list[str],
    ) -> dict[str, OrderBook]:
        """Fetch order books for tickers. Falls back to individual fetches."""
        result: dict[str, OrderBook] = {}
        batch_size = 20
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i : i + batch_size]
            try:
                params: dict[str, str] = {"tickers": ",".join(chunk)}
                data = await self._request(
                    "GET", "/markets/orderbooks", params=params,
                )
                for ob_data in data.get("orderbooks", []):
                    if isinstance(ob_data, dict):
                        book = OrderBook(**ob_data)
                        result[book.ticker] = book
            except Exception:
                # Fall back to individual fetches for this chunk
                for ticker in chunk:
                    ob = await self.get_orderbook(ticker)
                    if ob:
                        result[ob.ticker] = ob
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
