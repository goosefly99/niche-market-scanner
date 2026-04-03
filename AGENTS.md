# AGENTS.md — Niche Market Edge Scanner

## Overview
Multi-strategy automated trading system targeting mispriced Polymarket prediction markets across weather, football/soccer, and niche prop categories.

## Tech Stack
- Python 3.11+ (primary language for all engines, scanning, and execution logic)
- NOAA Weather.gov API (station-specific US temperature forecasts, free, no auth)
- Visual Crossing API (historical weather data for calibration and backtesting)
- Meteoblue / Windy API (international weather forecasts for non-US markets)
- Football-Data.org or API-Football (historical football/soccer match outcomes for La Liga, UCL)
- balldontlie or nba_api (NBA game outcomes and team stats)
- Polymarket API (market data, order book, participant counts)
- py-clob-client (default production CLOB execution on Polymarket)
- Simmer SDK (optional execution layer with self-custody and safety rails)
- python-telegram-bot (Telegram alerting and remote control)
- SQLite (trade journal persistence and performance analytics)
- pandas + numpy (probability calculations, bucket analysis, statistical modeling)
- APScheduler (configurable scan interval scheduling)
- aiohttp / httpx (async HTTP for parallel API calls across data sources)
- structlog (structured logging)
- VPS Ubuntu 22.04 + systemd (24/7 operation)
- USDC.e on Polygon (settlement currency for all Polymarket trades)

## Architecture

| Component | Description |
|-----------|-------------|
| **MarketScanner** | Core scanning engine polling Polymarket API on configurable intervals with category filtering, participant counts, and market registry |
| **WeatherEdgeEngine** | ICAO-station-aware weather mispricing detector with NOAA forecasts, bucket probability calculation, and boundary analysis |
| **SportsEdgeEngine** | Football/soccer favorite bias detector for La Liga/UCL with historical win rates and NBA outcome prediction |
| **NichePropScanner** | Ultra-low-liquidity market scanner for golf, esports, and goalscorer props with wallet profiling and copytrade signals |
| **KellyPositionSizer** | Kelly Criterion position sizing with fractional Kelly (half-Kelly default), portfolio limits, and kill switch |
| **ExecutionEngine** | Abstract broker interface for order placement and exit management (py-clob-client default, Simmer SDK optional) |
| **AlertManager** | Telegram-based alerting and remote control interface for signals, executions, and portfolio status |
| **TradeJournal** | Persistent trade logging and strategy performance analytics with SQLite backend |
| **ConfigManager** | Centralized YAML configuration with validated typed config, hot-reload via Telegram, single source of truth |
| **SystemMonitor** | Health monitoring with per-component heartbeats, API connectivity checks, and dead-man's-switch alerting |

## Conventions
- All source code in `src/` with absolute imports
- Tests in `tests/` mirroring src/ structure
- Config loaded from YAML via ConfigManager
- Abstract broker interface (py-clob-client default, Simmer optional)
- APScheduler for scan cycle management
- Logging via structlog
- Type hints required on all public functions
- State persisted to SQLite

## Directory Structure
```
src/
  scanner/        # MarketScanner, opportunity detection
  edges/          # WeatherEdgeEngine, SportsEdgeEngine, NichePropScanner
  sizing/         # KellyPositionSizer
  execution/      # ExecutionEngine (abstract broker interface)
  alerts/         # AlertManager, Telegram integration
  journal/        # TradeJournal, performance tracking
  config/         # ConfigManager
  monitor/        # SystemMonitor, health checks
tests/
config.yaml
```
