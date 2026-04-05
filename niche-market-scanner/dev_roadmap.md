# Niche Market Scanner — Development Roadmap

**Project:** Kalshi weather/economics edge scanner with paper trading
**Design spec:** `../docs/superpowers/specs/2026-04-04-niche-market-scanner-design.md`
**Source spec:** `../strategies/specs/niche-market-edge-scanner-trading-system--19c9c94d.json` (v3.1)

## Status Overview

| Status | Count |
|--------|-------|
| Complete | 24 |
| In Progress | 0 |
| Not Started | 3 |
| Blocked | 0 |
| **Total** | **27** |

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
| 5.4 | Add thin-market scanner engine (cross-category dead room detection) | Not Started | Medium | `src/niche_scanner/engines/thin_market.py` |
| 5.5 | Add Kalshi WebSocket client for real-time orderbook streaming | Not Started | Medium | `src/niche_scanner/kalshi/websocket.py` |
| 5.6 | Add economics markets to scanner fetch (alongside weather series) | Complete | High | `src/niche_scanner/scanner/market_scanner.py` |
| 5.7 | Integration test: end-to-end scan cycle with mocked Kalshi + NOAA APIs | Not Started | Medium | `tests/test_integration.py` |

---

## Build & Test Commands

- **Install:** `cd niche-market-scanner && pip install -e ".[dev]"`
- **Test:** `python -m pytest tests/ -v`
- **Lint:** `ruff check src/ tests/` (if ruff configured)
- **Docker:** `docker compose up --build`
- **Run scanner:** `python -m niche_scanner.main` (requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY env vars)
