# Edge Detection Engines — Agent Guide

## Architecture Overview

Each engine implements the `EdgeEngine` abstract base class and its
`async scan(markets, orderbooks) -> list[EdgeSignal]` method. The
`MarketScanner` calls every registered engine with the same market
list and orderbook dict; each engine evaluates markets through its
own lens (weather forecasts, economic indicators, liquidity analysis).

## Module Responsibilities

| Module | Role |
|---|---|
| `base.py` | ABC `EdgeEngine` with abstract `scan()`. Dataclass `EdgeSignal` (engine, ticker, side, model_prob, market_prob, edge_pp, fee_adjusted_edge, confidence, thesis, metadata). |
| `weather.py` | `WeatherEdgeEngine` — fetches NOAA hourly forecasts, builds Gaussian temperature model (`NOAAForecast`), compares bucket/threshold probabilities against Kalshi weather market prices. Caches NOAA responses in-memory. |
| `economics.py` | `EconomicsEdgeEngine` — classifies markets by release type (CPI, Fed rate, jobs, GDP, etc.) via `SERIES_TICKER_MAP`, fetches indicators from FRED API via async `fetch_indicators()`, computes weighted model probability. Also contains `ReleaseCalendar` for scan interval switching and async `FREDClient` for historical data retrieval. |
| `thin_market.py` | `ThinMarketEngine` — cross-category dead room detector. Derives fair value from orderbook mid-price (or last-trade fallback), compares against executable bid/ask prices, signals when fee-adjusted edge exceeds threshold. |

## Key Conventions

- **All engines receive the same inputs.** `markets: list[Market]` and
  `orderbooks: dict[str, OrderBook]` are passed to every engine.
- **Fee adjustment is mandatory.** Use `round_trip_fee_pp(price_cents)`
  from `sizing.fees` and subtract from raw edge before comparing to
  `min_edge_pp`.
- **EdgeSignal.model_prob is always the YES probability.** For NO-side
  signals, `model_prob` still represents the model's YES estimate;
  `side="no"` indicates the trade direction.
- Weather engine requires `ICAOStations` config for city → NOAA grid
  mapping. Only verified stations are scanned.
- Economics engine requires `FRED_API_KEY` env var for live indicator
  data; without it, indicator fetching returns empty (no crash).
- **All HTTP I/O inside engines is async.** Engines hold a single
  long-lived `httpx.AsyncClient` instance (created lazily on first use
  and reused for every NOAA / FRED request) instead of opening a fresh
  client per call.  This reuses TCP connections and keeps TLS sessions
  warm across scan cycles.  Engines accept an optional
  `http_client=` kwarg for dependency injection; when injected, the
  caller owns the lifecycle.  Engines that own their client expose an
  async `close()` method that `main.py` invokes during shutdown.  No
  synchronous `httpx.get()` calls in the scan path — blocking calls
  would stall the scanner and dashboard on the shared event loop.

## Testing

Tests in `tests/engines/`. Weather tests use `respx` for NOAA HTTP mocking
and temporary YAML files for ICAO config. Economics tests mock FRED responses.
Thin-market tests use synthetic `Market`/`OrderBook` objects directly.
