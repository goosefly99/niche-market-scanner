# Niche Market Scanner — Development Roadmap

**Project:** Kalshi weather/economics edge scanner with paper trading
**Design spec:** `../docs/superpowers/specs/2026-04-04-niche-market-scanner-design.md`
**Source spec:** `../strategies/specs/niche-market-edge-scanner-trading-system--19c9c94d.json` (v3.1)

## Status Overview

| Status | Count |
|--------|-------|
| Complete | 12 |
| In Progress | 0 |
| Not Started | 8 |
| Blocked | 0 |
| **Total** | **20** |

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

## Phase 2: Weather Engine Hardening (Not Started)

Weather engine exists but needs refinement based on live Kalshi market structure (2F buckets, floor_strike/cap_strike fields). Scan cycle runs but needs daytime testing with live orderbooks.

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 2.1 | Parse floor_strike/cap_strike from Kalshi Market response | Complete | 1.2 | `src/niche_scanner/kalshi/models.py`, `tests/kalshi/test_models.py` |
| 2.2 | Update weather engine ticker parser for actual Kalshi format (KXHIGHNY-26APR04-T75, B74.5) | Complete | 2.1 | `src/niche_scanner/engines/weather.py`, `tests/engines/test_weather.py` |
| 2.3 | Use floor_strike/cap_strike for bucket boundaries instead of subtitle parsing | Complete | 2.1 | `src/niche_scanner/engines/weather.py` |
| 2.4 | Add city-to-series-ticker mapping (new_york -> KXHIGHNY, chicago -> KXHIGHCHI, etc.) | Complete | 2.2 | `src/niche_scanner/engines/weather.py`, `config/icao_stations.yaml` |
| 2.5 | Run daytime scan with live orderbooks and log edge signals | Not Started | 2.1-2.4 | `data/test_scan.db` |

---

## Phase 3: Economics Engine Integration (Not Started)

Economics engine has the framework but indicator fetchers are stubs. Need to integrate real data sources.

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 3.1 | Integrate Cleveland Fed Inflation Nowcast scraper | Not Started | — | `src/niche_scanner/engines/economics.py`, `tests/engines/test_economics.py` |
| 3.2 | Integrate CME FedWatch probability scraper | Not Started | — | `src/niche_scanner/engines/economics.py` |
| 3.3 | Integrate FRED API for historical economic data | Not Started | — | `src/niche_scanner/engines/economics.py` |
| 3.4 | Build economics release calendar from BLS/Fed schedule | Not Started | — | `src/niche_scanner/engines/economics.py`, `config/settings.yaml` |
| 3.5 | Map Kalshi economics series tickers (CPI, Fed rate, GDP, jobs) | Not Started | — | `src/niche_scanner/engines/economics.py` |

---

## Phase 4: Alerts & Monitoring (Not Started)

| # | Item | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 4.1 | Telegram bot integration (alerts for edge signals, daily P&L) | Not Started | — | `src/niche_scanner/alerts/telegram.py`, `tests/alerts/test_telegram.py` |
| 4.2 | Health monitor (API connectivity, heartbeats, dead-man's-switch) | Not Started | — | `src/niche_scanner/monitor/health.py` |

---

## Build & Test Commands

- **Install:** `cd niche-market-scanner && pip install -e ".[dev]"`
- **Test:** `python -m pytest tests/ -v`
- **Lint:** `ruff check src/ tests/` (if ruff configured)
- **Docker:** `docker compose up --build`
- **Run scanner:** `python -m niche_scanner.main` (requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY env vars)
