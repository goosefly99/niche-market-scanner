# Dashboard Subpackage — Agent Guide

## Architecture Overview

The dashboard is an **in-process FastAPI server** that shares the scanner's asyncio
event loop. It provides a read-only monitoring surface (plus kill switch) for the
niche-market-scanner Kalshi trading bot. There is no separate process or IPC layer;
the dashboard accesses scanner components (RiskGuard, HealthMonitor, TradeJournal)
via direct Python object references stored on `FastAPI.app.state`.

**Frontend stack:** HTMX + Tailwind CSS, both loaded via CDN. No JavaScript build
step. Server-rendered Jinja2 templates with HTMX partial swaps for dynamic updates.
Dark theme by default.

**Backend stack:** FastAPI (ASGI) served by uvicorn running as an asyncio task
alongside the scanner's scan loop. All persistence goes through a single shared
`aiosqlite.Connection` owned by TradeJournal.

## Component Responsibilities

| Module | Role |
|---|---|
| `server.py` | Lifecycle manager. Creates the FastAPI app, mounts routers, starts/stops uvicorn as an asyncio task. Includes a death watchdog that alerts via Telegram if the dashboard task dies. |
| `routes.py` | REST API endpoints (GET-only data, POST kill/reset/reload). Returns JSON for all `/api/*` paths. |
| `pages.py` | HTML page route handlers. Renders Jinja2 templates for `/`, `/trades`, `/signals`, `/config`. Supports full-page and HTMX partial rendering. |
| `kill_switch.py` | Kill switch API router. POST endpoints for kill (single-click + 5s undo), undo-kill, reset (requires CONFIRM), and config reload. All require `X-Confirm: true` header. |
| `scan_cycle_logger.py` | Writes scan cycle summaries to the `scan_cycles` SQLite table. Receives shared aiosqlite connection from TradeJournal. `main.py` reads `scanner.last_cycle_stats` (a `ScanCycleStats` dataclass snapshot) after each cycle and passes the fields through. |
| `balance_tracker.py` | Writes balance snapshots to the `balance_history` SQLite table. Receives shared aiosqlite connection from TradeJournal. |
| `templates/` | Jinja2 HTML templates. `base.html` is the layout; page templates extend it; `partials/` holds HTMX swap fragments. |

## Key Conventions

- **All money values are stored and transmitted in cents.** Convert to dollars only
  at the template rendering layer using the `cents_to_dollars` Jinja2 filter.
- **Single shared `aiosqlite.Connection`** — TradeJournal owns it. ScanCycleLogger
  and BalanceTracker receive it as a constructor parameter. Never open a second
  connection to `data/trades.db`.
- **Read-only by default.** Only four POST endpoints exist: `/api/risk/kill`,
  `/api/risk/undo-kill`, `/api/risk/reset`, `/api/config/reload`. All require
  `X-Confirm: true` header.
- **HTMX partial rendering** — check the `HX-Request` header to decide whether to
  return a full page or just the swappable fragment.
- **No JavaScript build step.** All interactivity comes from HTMX attributes in
  HTML templates. Chart.js via CDN is the exception for balance charts.
- **Graceful degradation** — if the dashboard fails to start (port conflict, etc.),
  the scanner continues operating normally.

## File Layout

```
src/niche_scanner/dashboard/
    __init__.py
    AGENTS.md               # This file
    dev_roadmap.md          # Implementation tracking table
    server.py               # FastAPI app lifecycle, uvicorn task, death watchdog
    routes.py               # REST API endpoints (JSON)
    pages.py                # HTML page route handlers (Jinja2)
    kill_switch.py          # Kill/undo/reset/reload POST endpoints
    scan_cycle_logger.py    # scan_cycles table writer
    balance_tracker.py      # balance_history table writer
    templates/
        base.html           # Layout: Tailwind, HTMX, nav, kill switch button
        overview.html       # P&L, risk gauges, health, positions, balance chart
        trades.html         # Filterable paginated trade table
        signals.html        # HTMX-polled recent signals
        config.html         # Read-only settings display + reload button
        partials/
            _trade_rows.html
            _overview_stats.html
            _risk_gauges.html
            _health_status.html
            _open_positions.html
            _signals_table.html
```

## Spec Reference

Design spec: `strategies/specs/niche-scanner-dashboard--dashboard-v1.json` (v1.1, validated)
Debate log: `strategies/debates/dashboard-debate--dashboard-v1.json`
