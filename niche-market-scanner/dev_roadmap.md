# Niche Market Scanner — Development Roadmap

**Project:** Kalshi weather/economics edge scanner with paper trading
**Design spec:** `../docs/superpowers/specs/2026-04-04-niche-market-scanner-design.md`
**Source spec:** `../strategies/specs/niche-market-edge-scanner-trading-system--19c9c94d.json` (v3.1)

## Status Overview

| Status | Count |
|--------|-------|
| Complete | 39 |
| In Progress | 0 |
| Not Started | 0 |
| Blocked | 0 |
| **Total** | **39** |

---

## Phase 1: Core Infrastructure (Complete)

All core components implemented and tested. 41 tests passing, Docker build working, Kalshi API connected.

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 1.1 | Project scaffold, Dockerfile, docker-compose | Complete | — | `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `AGENTS.md` |
| 1.2 | Kalshi API models (Market, OrderBook, Event, Order) | Complete | — | `src/niche_scanner/kalshi/models.py` |
| 1.3 | RSA-PSS authentication (base64, full path signing) | Complete | — | `src/niche_scanner/kalshi/auth.py` |
| 1.4 | Kalshi REST client (rate limiting, batch orderbooks) | Complete | 1.3 | `src/niche_scanner/kalshi/client.py` |
| 1.5 | Fee calculator (taker/maker formulas) | Complete | — | `src/niche_scanner/sizing/fees.py` |
| 1.6 | Kelly position sizer (concentrated/barbell/NO-bet) | Complete | 1.5 | `src/niche_scanner/sizing/kelly.py`, `src/niche_scanner/engines/base.py` |
| 1.7 | Configuration system (env + YAML + ICAO stations) | Complete | — | `src/niche_scanner/config.py`, `config/settings.yaml`, `config/icao_stations.yaml` |
| 1.8 | Trade journal (SQLite, per-vertical metrics) | Complete | — | `src/niche_scanner/journal/db.py`, `src/niche_scanner/journal/trade_journal.py` |

---

## Phase 2: Weather Engine Hardening (Complete)

Weather engine refined for live Kalshi market structure (2F buckets, floor_strike/cap_strike fields).

### Item 2.5 Live Scan Findings (2026-04-04)

- **400 markets fetched** across 52 weather series (20 cities, high + low temp)
- **Zero liquidity in all weather temperature markets** — no bids, asks, or volume at 9 AM ET
- Markets exist with correct structure (floor_strike, cap_strike, 2F buckets) but no participants
- API returns `yes_bid_dollars`, `volume_fp` etc. as dollar-denominated fields (not integer cents)
- **Implication:** Weather edge strategy requires either (a) waiting for liquidity to develop as Kalshi grows, or (b) acting as a market maker (posting limit orders at NOAA-derived fair prices)
- **Pivot recommendation:** Focus on economics markets (3.x) which may have more activity, and investigate providing liquidity rather than taking it

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 2.1 | Parse floor_strike/cap_strike from Kalshi Market response | Complete | 1.2 | `src/niche_scanner/kalshi/models.py`, `tests/kalshi/test_models.py` |
| 2.2 | Update weather engine ticker parser for actual Kalshi format (KXHIGHNY-26APR04-T75, B74.5) | Complete | 2.1 | `src/niche_scanner/engines/weather.py`, `tests/engines/test_weather.py` |
| 2.3 | Use floor_strike/cap_strike for bucket boundaries instead of subtitle parsing | Complete | 2.1 | `src/niche_scanner/engines/weather.py` |
| 2.4 | Add city-to-series-ticker mapping (new_york -> KXHIGHNY, chicago -> KXHIGHCHI, etc.) | Complete | 2.2 | `src/niche_scanner/engines/weather.py`, `config/icao_stations.yaml` |
| 2.5 | Run daytime scan with live orderbooks and log edge signals | Complete | 2.1-2.4 | `data/test_scan.db` |

---

## Phase 3: Economics Engine Integration (Complete)

Economics engine integrated with FRED API (CPI YoY, Fed rate, historical data) and release calendar for scan intensification.

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 3.1 | Integrate Cleveland Fed Inflation Nowcast scraper | Complete | — | `src/niche_scanner/engines/economics.py`, `tests/engines/test_economics.py` |
| 3.2 | Integrate CME FedWatch probability scraper | Complete | — | `src/niche_scanner/engines/economics.py` |
| 3.3 | Integrate FRED API for historical economic data | Complete | — | `src/niche_scanner/engines/economics.py` |
| 3.4 | Build economics release calendar from BLS/Fed schedule | Complete | — | `src/niche_scanner/engines/economics.py`, `config/settings.yaml` |
| 3.5 | Map Kalshi economics series tickers (CPI, Fed rate, GDP, jobs) | Complete | — | `src/niche_scanner/engines/economics.py` |

---

## Phase 4: Alerts & Monitoring (Complete)

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 4.1 | Telegram bot integration (alerts for edge signals, daily P&L) | Complete | — | `src/niche_scanner/alerts/telegram.py`, `tests/alerts/test_telegram.py` |
| 4.2 | Health monitor (API connectivity, heartbeats, dead-man's-switch) | Complete | — | `src/niche_scanner/monitor/health.py` |

---

## Phase 5: Future Improvements (Proposed)

Identified during continuous improvement review. Not blocking — all core functionality works.

| # | Item | Status | Priority | Target Files |
|---|------|--------|----------|--------------|
| 5.1 | Wire AlertManager into MarketScanner and main.py scan loop | Complete | High | `src/niche_scanner/scanner/market_scanner.py`, `src/niche_scanner/main.py` |
| 5.2 | Wire HealthMonitor into main.py with startup validation | Complete | High | `src/niche_scanner/main.py` |
| 5.3 | Wire ReleaseCalendar into scan loop for dynamic interval switching | Complete | High | `src/niche_scanner/main.py`, `src/niche_scanner/scanner/market_scanner.py` |
| 5.4 | Add thin-market scanner engine (cross-category dead room detection) | Complete | Medium | `src/niche_scanner/engines/thin_market.py` |
| 5.5 | Add Kalshi WebSocket client for real-time orderbook streaming | Complete | Medium | `src/niche_scanner/kalshi/websocket.py` |
| 5.6 | Add economics markets to scanner fetch (alongside weather series) | Complete | High | `src/niche_scanner/scanner/market_scanner.py` |
| 5.7 | Integration test: end-to-end scan cycle with mocked Kalshi + NOAA APIs | Complete | Medium | `tests/test_integration.py` |

---

## Phase 6: Continuous Improvement

Improvements identified during codebase review after Phase 5 completion.

| # | Item | Status | Priority | Target Files |
|---|------|--------|----------|--------------|
| 6.1 | Add AGENTS.md to all subpackages with 3+ source files | Complete | Medium | `src/niche_scanner/kalshi/AGENTS.md`, `src/niche_scanner/engines/AGENTS.md`, `src/niche_scanner/execution/AGENTS.md`, `src/niche_scanner/sizing/AGENTS.md`, `src/niche_scanner/journal/AGENTS.md` |
| 6.2 | Build real probability estimation in economics engine | Complete | High | `src/niche_scanner/engines/economics.py`, `tests/engines/test_economics.py` |
| 6.3 | Add broader market discovery for thin-market engine | Complete | High | `src/niche_scanner/scanner/market_scanner.py`, `src/niche_scanner/kalshi/client.py` |
| 6.4 | Wire dashboard API stubs to real ScanCycleLogger and BalanceTracker data | Complete | High | `src/niche_scanner/dashboard/server.py`, `src/niche_scanner/dashboard/routes.py`, `tests/dashboard/test_routes.py`, `tests/dashboard/test_server.py` |
| 6.5 | Add in-memory EdgeSignal buffer wired to /signals page and /api/signals/recent (dashboard Phase 4.1-4.4) | Complete | High | `src/niche_scanner/dashboard/signal_buffer.py`, `src/niche_scanner/dashboard/server.py`, `src/niche_scanner/dashboard/routes.py`, `src/niche_scanner/dashboard/pages.py`, `src/niche_scanner/main.py`, `tests/dashboard/test_signal_buffer.py` |
| 6.6 | Wire BalanceTracker.record_snapshot() into scan loop so /api/balance/history returns real data (paper + live modes) | Complete | High | `src/niche_scanner/main.py`, `tests/test_main_balance_tracking.py` |
| 6.7 | Convert economics engine FRED client from blocking httpx.get to async httpx.AsyncClient (prevents event loop stalls during scan cycles) | Complete | High | `src/niche_scanner/engines/economics.py`, `tests/engines/test_economics.py`, `src/niche_scanner/engines/AGENTS.md` |
| 6.8 | Parallelize per-series market fetching in MarketScanner.scan_cycle with asyncio.gather (90+ sequential requests per cycle drops to batched concurrent calls bounded by KalshiClient's existing semaphore) | Complete | High | `src/niche_scanner/scanner/market_scanner.py`, `tests/scanner/test_market_scanner.py` |
| 6.9 | Extract Trader protocol (execution/base.py) with public total_cost_cents; replace getattr on PaperTrader._total_cost_cents in main.py and fix incorrect PaperTrader type hint in MarketScanner | Complete | Medium | `src/niche_scanner/execution/base.py`, `src/niche_scanner/execution/paper_trader.py`, `src/niche_scanner/execution/live_trader.py`, `src/niche_scanner/scanner/market_scanner.py`, `src/niche_scanner/main.py`, `tests/execution/test_base.py` |
| 6.10 | Reuse a shared `httpx.AsyncClient` inside the weather and economics engines so NOAA and FRED fetches reuse TCP connections instead of creating and tearing down a new client on every call (kept backwards-compatible: engines lazily build their own client when none is injected, exposing a `close()` method for orderly shutdown) | Complete | Medium | `src/niche_scanner/engines/weather.py`, `src/niche_scanner/engines/economics.py`, `src/niche_scanner/engines/AGENTS.md`, `src/niche_scanner/main.py`, `tests/engines/test_economics.py`, `tests/engines/test_weather.py` |
| 6.11 | Fix dashboard crash in paper mode: all routes and pages accessed `risk_guard.state`/`.config`/`.is_killed` without null-checking, but `risk_guard` is `None` in paper mode (the default). Added null-safe handling across `routes.py` (4 endpoints) and `pages.py` (4 page routes), with 9 paper-mode tests. | Complete | Critical | `src/niche_scanner/dashboard/routes.py`, `src/niche_scanner/dashboard/pages.py`, `tests/dashboard/test_routes.py` |
| 6.12 | Reuse a shared `httpx.AsyncClient` in AlertManager instead of creating and tearing down a new client for every Telegram message (same anti-pattern fixed in 6.10 for engines). Added lazy client init, `close()` method, wired shutdown into `main.py`, 7 new tests. | Complete | Medium | `src/niche_scanner/alerts/telegram.py`, `src/niche_scanner/main.py`, `tests/alerts/test_telegram.py` |

---

## Build & Test Commands

- **Install:** `cd niche-market-scanner && pip install -e ".[dev]"`
- **Test:** `python -m pytest tests/ -v`
- **Lint:** `ruff check src/ tests/` (if ruff configured)
- **Docker:** `docker compose up --build`
- **Run scanner:** `python -m niche_scanner.main` (requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY env vars)
