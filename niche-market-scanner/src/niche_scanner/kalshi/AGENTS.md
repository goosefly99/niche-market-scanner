# Kalshi API Package — Agent Guide

## Architecture Overview

This package provides all communication with the Kalshi exchange:
REST client for market data and order placement, WebSocket client for
real-time orderbook streaming, RSA-PSS authentication, and Pydantic
data models.

All HTTP I/O uses **httpx** (async). WebSocket I/O uses the
**websockets** library (v13+). Authentication is RSA-PSS with SHA-256
on every request — tokens are never cached.

## Module Responsibilities

| Module | Role |
|---|---|
| `auth.py` | RSA-PSS signing. Loads PEM private key, produces the 3 required headers (`KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`). Signing message: `{timestamp_ms}{METHOD}{/full/path}`. |
| `client.py` | Async REST client. Rate-limited via semaphore. Methods: `get_markets`, `get_orderbook`, `get_batch_orderbooks`, `create_order`, `cancel_order`, `get_balance`, `get_positions`. |
| `models.py` | Pydantic v2 models: `Market`, `OrderBook`, `OrderBookRow`, `Event`, `Order`. All prices in cents (0-100). Computed fields: `spread`, `implied_prob`, `time_to_close_hours`, `strike_type`, `mid_price`. |
| `websocket.py` | Async WebSocket client. Connects, authenticates via login command, subscribes to `orderbook_delta` channel. Streams `OrderBookDelta` frozen dataclasses into an `asyncio.Queue`. |

## Key Conventions

- **All prices are in cents (0-100).** Never use dollar amounts in this package.
- **RSA-PSS signing on every request.** The signing path must be the full
  URL path from root (e.g. `/trade-api/v2/markets`), not a relative path.
- **Never import the deprecated `kalshi-python` package.**
- REST base URLs:
  - Production: `https://api.elections.kalshi.com/trade-api/v2`
  - Demo: `https://demo-api.kalshi.co/trade-api/v2`
- WebSocket URLs are derived by replacing `/trade-api/v2` with `/trade-api/ws/v2`.
- Batch orderbook fetches use chunks of 20 tickers with individual-fetch fallback.

## Testing

Tests in `tests/kalshi/`. Use `respx` for mocking httpx HTTP calls.
WebSocket tests use `unittest.mock.AsyncMock` with a custom `_MockWsConnection`
class that simulates async iteration. No real API calls in any test.
