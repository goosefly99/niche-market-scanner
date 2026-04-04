# Niche Market Scanner — Development Guide

## Source spec
`strategies/specs/niche-market-edge-scanner-trading-system--19c9c94d.json` (v3.1)

## Architecture
Monolith async Python service. Single event loop, APScheduler for per-vertical
scan intervals. SQLite for state. Paper trading mode by default.

## Key rules
- All Kalshi prices are in cents (0-10000). Balance in cents.
- RSA-PSS signing on every request. Never cache auth tokens.
- ICAO station mapping MUST be verified before any weather pricing.
- Fee formula: taker = round_up(0.07 * C * P * (1-P)), no settlement fees.
- Half-Kelly max. NO bets: 2% per position, 25% aggregate.
- Paper trade mode is the default. Live trading requires explicit config change.
- Never import the deprecated `kalshi-python` package.

## Running
- Dev: `pip install -e ".[dev]" && pytest`
- Docker: `docker compose up --build`

## Testing
pytest with pytest-asyncio. Use respx to mock httpx calls. No real API calls in tests.
