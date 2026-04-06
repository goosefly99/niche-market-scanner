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
| `server.py` | Lifecycle manager. Creates the FastAPI app, mounts routers, starts/stops uvicorn as an asyncio task. Includes a death watchdog that alerts via Telegram if the dashboard task dies. Owns the shared `SignalBuffer`, `ScanCycleLogger`, and `BalanceTracker` on `app.state`. |
| `routes.py` | REST API endpoints (GET-only JSON). Serves trades, stats, risk, health, scanner status, balance, scan cycles, and recent signals. |
| `pages.py` | HTML page route handlers. Renders Jinja2 templates for `/`, `/trades`, `/signals`, `/config`. Supports full-page and HTMX partial rendering. The `/` route loads the last 200 `BalanceTracker` snapshots and embeds them as JSON in the page so Chart.js can render the balance history client-side. The `/trades` route delegates filtering and pagination to `TradeJournal.query_trades()`. |
| `scan_cycle_logger.py` | Writes scan cycle summaries to the `scan_cycles` SQLite table. Receives shared aiosqlite connection from TradeJournal. `main.py` reads `scanner.last_cycle_stats` (a `ScanCycleStats` dataclass snapshot) after each cycle and passes the fields through. |
| `balance_tracker.py` | Writes balance snapshots to the `balance_history` SQLite table. Receives shared aiosqlite connection from TradeJournal. `main.py` calls `record_snapshot` after each scan cycle: live mode reads `risk_guard.state` (current/peak/cumulative_spend), paper mode derives the balance from the starting bankroll minus `trader.total_cost_cents` (exposed via the shared `Trader` protocol in `execution/base.py`) and tracks the peak locally. |
| `signal_buffer.py` | Bounded FIFO (`collections.deque`) of recent `EdgeSignal` objects. Shared between `main.py` (producer, calls `extend()` after each scan cycle) and the dashboard (consumer, `/api/signals/recent` + `/signals` page). Volatile — cleared on restart. Default capacity: 100. |
| `templates/` | Jinja2 HTML templates. `base.html` is the layout; page templates extend it; `partials/` holds HTMX swap fragments. |

**Planned (Phase 5):** `kill_switch.py` — POST endpoints for kill (single-click + 5s undo), undo-kill, reset (requires CONFIRM), and config reload. All will require `X-Confirm: true` header.

## Key Conventions

- **All money values are stored and transmitted in cents.** Convert to dollars only
  at the template rendering layer using the `cents_to_dollars` Jinja2 filter.
- **Single shared `aiosqlite.Connection`** — TradeJournal owns it. ScanCycleLogger
  and BalanceTracker receive it as a constructor parameter. Never open a second
  connection to `data/trades.db`.
- **`risk_guard` is `None` in paper mode.** Every route and page handler that
  accesses `request.app.state.risk_guard` MUST null-check before calling
  `.state`, `.config`, or `.is_killed`. Return zeroed defaults when `None`.
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
    scan_cycle_logger.py    # scan_cycles table writer
    balance_tracker.py      # balance_history table writer
    signal_buffer.py        # In-memory bounded deque of recent EdgeSignals
    templates/
        base.html           # Layout: Tailwind, HTMX, nav, kill switch button
        overview.html       # P&L, risk gauges, health, positions, balance chart
        trades.html         # Filterable paginated trade table
        signals.html        # HTMX-polled recent signals
        config.html         # Read-only settings display + reload button
        partials/
            _trade_rows.html
            _signals_table.html
```

## Spec Reference

Design spec: `strategies/specs/niche-scanner-dashboard--dashboard-v1.json` (v1.1, validated)
Debate log: `strategies/debates/dashboard-debate--dashboard-v1.json`
