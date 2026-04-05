# Dashboard Development Roadmap

Spec: `strategies/specs/niche-scanner-dashboard--dashboard-v1.json` (v1.1, validated)

## Phase 1: FastAPI Server + REST API

| # | Task | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 1.1 | Add fastapi, uvicorn[standard], jinja2 to pyproject.toml | Done (scaffold) | -- | `pyproject.toml` |
| 1.2 | Create dashboard package with `__init__.py` | Done (scaffold) | -- | `dashboard/__init__.py` |
| 1.3 | Create DashboardServer class (FastAPI app, uvicorn task, death watchdog) | Complete | 1.2 | `dashboard/server.py` |
| 1.4 | Create APIRouter with all GET endpoints | Complete | 1.3 | `dashboard/routes.py` |
| 1.5 | Add `TradeJournal.get_trade_by_id()` and `get_daily_stats()` methods | Complete | -- | `journal/trade_journal.py` |
| 1.6 | Expose TradeJournal aiosqlite.Connection as property | Complete | -- | `journal/trade_journal.py` |
| 1.7 | Integrate DashboardServer into main.py (task + death watchdog) | Not Started | 1.3 | `main.py` |
| 1.8 | Add dashboard config section to settings.yaml | Complete | -- | `config/settings.yaml` |
| 1.9 | Add startup WARNING log if host != 127.0.0.1 | Complete | 1.3 | `dashboard/server.py` |
| 1.10 | Update docker-compose.yml port mapping (127.0.0.1:8050:8050) | Not Started | -- | `docker-compose.yml` |
| 1.11 | Enable WAL mode on SQLite connection | Not Started | -- | `journal/db.py` |

## Phase 2: HTMX Frontend Pages

| # | Task | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 2.1 | Create `base.html` layout (Tailwind CDN, HTMX CDN, nav, kill switch button) | Done (scaffold) | -- | `templates/base.html` |
| 2.2 | Create `overview.html` (P&L cards, risk gauges, health lights, positions, balance chart) | Not Started | 2.1 | `templates/overview.html` |
| 2.3 | Create `trades.html` (filterable table, HTMX pagination) | Not Started | 2.1 | `templates/trades.html` |
| 2.4 | Create `signals.html` (HTMX-polled signals table, every 10s) | Not Started | 2.1 | `templates/signals.html` |
| 2.5 | Create `config.html` (read-only settings, reload button) | Not Started | 2.1 | `templates/config.html` |
| 2.6 | Create pages.py route handler (/, /trades, /signals, /config) | Not Started | 2.1 | `dashboard/pages.py` |
| 2.7 | Create HTMX partial templates (_trade_rows, _overview_stats, etc.) | Not Started | 2.2, 2.3 | `templates/partials/` |
| 2.8 | Add Jinja2 template filters (cents_to_dollars, format_pct, relative_time) | Not Started | 2.1 | `dashboard/server.py` or `pages.py` |
| 2.9 | Wire kill switch single-click UX with 5s undo toast | Not Started | 2.1, 5.1 | `templates/base.html` |

## Phase 3: Scan Cycle + Balance Persistence

| # | Task | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 3.1 | Add scan_cycles and balance_history CREATE TABLE to db.py | Not Started | -- | `journal/db.py` |
| 3.2 | Create ScanCycleLogger (record_cycle, get_recent_cycles) | Not Started | 3.1 | `dashboard/scan_cycle_logger.py` |
| 3.3 | Create BalanceTracker (record_snapshot, get_history) | Not Started | 3.1 | `dashboard/balance_tracker.py` |
| 3.4 | Integrate ScanCycleLogger into main.py scan loop (with timer) | Not Started | 3.2, 1.7 | `main.py` |
| 3.5 | Integrate BalanceTracker into main.py scan loop | Not Started | 3.3, 1.7 | `main.py` |
| 3.6 | Add /api/scan-cycles and /api/balance/history endpoints | Not Started | 3.2, 3.3 | `dashboard/routes.py` |
| 3.7 | Wire balance chart on overview page to real data | Not Started | 3.3, 2.2 | `templates/overview.html` |
| 3.8 | Add scan cycle performance stats to overview page | Not Started | 3.2, 2.2 | `templates/overview.html` |

## Phase 4: HTMX Polling for Signals (deferred WebSocket to v2)

| # | Task | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 4.1 | Implement in-memory EdgeSignal buffer (bounded deque, maxlen=100) | Not Started | 1.3 | `dashboard/server.py` |
| 4.2 | Add signal capture hook in MarketScanner or main.py | Not Started | 4.1 | `main.py` or `scanner/market_scanner.py` |
| 4.3 | Create GET /api/signals/recent endpoint | Not Started | 4.1 | `dashboard/routes.py` |
| 4.4 | Wire signals.html HTMX polling to /api/signals/recent (every 10s) | Not Started | 4.3, 2.4 | `templates/signals.html` |
| 4.5 | Add HTMX auto-refresh to overview page (every 30s) | Not Started | 2.2 | `templates/overview.html` |
| 4.6 | Add HTMX auto-refresh to trades page (every 60s) | Not Started | 2.3 | `templates/trades.html` |

## Phase 5: Kill Switch API + Hardening

| # | Task | Status | Depends On | Target Files |
|---|------|--------|------------|--------------|
| 5.1 | Create kill_switch.py (POST kill, undo-kill, reset, config/reload) | Not Started | 1.3 | `dashboard/kill_switch.py` |
| 5.2 | Implement 5-second undo window with timestamped token | Not Started | 5.1 | `dashboard/kill_switch.py` |
| 5.3 | Implement POST /api/config/reload | Not Started | 5.1 | `dashboard/kill_switch.py` |
| 5.4 | Wire kill switch button in base.html (single-click, undo toast) | Not Started | 5.1, 2.1 | `templates/base.html` |
| 5.5 | Wire kill switch reset (CONFIRM modal) | Not Started | 5.1, 2.1 | `templates/base.html` |
| 5.6 | Add kill status banner to base.html | Not Started | 5.1, 2.1 | `templates/base.html` |
| 5.7 | Add request logging middleware | Not Started | 1.3 | `dashboard/server.py` |
| 5.8 | Add exception handling middleware | Not Started | 1.3 | `dashboard/server.py` |
| 5.9 | Update Docker HEALTHCHECK to /api/ping | Not Started | 1.4 | `Dockerfile` |
| 5.10 | Write integration tests (kill switch round-trip, undo, confirmation) | Not Started | 5.1, 5.2 | `tests/` |
| 5.11 | Security hardening (no secret leaks, rate limiting on kill switch) | Not Started | 5.1 | `dashboard/kill_switch.py`, `dashboard/routes.py` |
