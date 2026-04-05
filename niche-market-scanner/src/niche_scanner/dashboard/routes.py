"""REST API endpoints for the dashboard.

All ``/api/*`` routes return JSON. Endpoints are grouped by domain:

- **Trades** — paginated history, single trade, aggregate stats, daily P&L
- **Risk** — live RiskState and LiveTradingConfig from RiskGuard
- **Health** — HealthReport from HealthMonitor, scan cycle metrics
- **Scanner** — mode, engines, intervals
- **Balance** — current balance, historical snapshots
- **Scan cycles** — cycle history with duration and signal counts
- **Signals** — recent EdgeSignals from in-memory buffer
- **Ping** — lightweight health check for Docker HEALTHCHECK
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

api_router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

@api_router.get("/ping")
async def ping() -> dict:
    """Lightweight health check for Docker HEALTHCHECK. No computation."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Trade endpoints
# ---------------------------------------------------------------------------

@api_router.get("/trades")
async def get_trades(
    request: Request,
    engine: Optional[str] = Query(None, description="Filter by engine (weather, economics)"),
    action: Optional[str] = Query(None, description="Filter by action (buy, sell, rejected, failed)"),
    outcome: Optional[str] = Query(None, description="Filter by outcome (win, loss, pending)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Paginated trade list with optional engine/action/outcome filters."""
    # TODO: query TradeJournal via request.app.state.journal
    return {
        "trades": [],
        "total": 0,
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/trades/{trade_id}")
async def get_trade_by_id(request: Request, trade_id: int) -> dict:
    """Single trade detail by ID."""
    # TODO: query TradeJournal.get_trade_by_id(trade_id)
    return {"error": "not implemented", "trade_id": trade_id}


@api_router.get("/stats")
async def get_stats(
    request: Request,
    engine: Optional[str] = Query(None, description="Filter by engine"),
) -> dict:
    """Aggregate trade statistics."""
    # TODO: query TradeJournal.get_stats(engine=engine)
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "avg_edge_pp": 0.0,
        "net_pnl_cents": 0,
    }


@api_router.get("/stats/daily")
async def get_daily_stats(request: Request) -> dict:
    """Daily P&L aggregation for charting."""
    # TODO: query TradeJournal.get_daily_stats()
    return {"days": []}


# ---------------------------------------------------------------------------
# Risk endpoints
# ---------------------------------------------------------------------------

@api_router.get("/risk/state")
async def get_risk_state(request: Request) -> dict:
    """Serialize full RiskState from RiskGuard."""
    # TODO: read request.app.state.risk_guard.state
    return {
        "peak_balance_cents": 0,
        "current_balance_cents": 0,
        "cumulative_spend_cents": 0,
        "daily_loss_cents": 0,
        "daily_trade_count": 0,
        "open_position_count": 0,
        "day_start_balance_cents": 0,
        "killed": False,
        "kill_reason": "",
        "positions": {},
    }


@api_router.get("/risk/config")
async def get_risk_config(request: Request) -> dict:
    """Serialize LiveTradingConfig from RiskGuard."""
    # TODO: read request.app.state.risk_guard.config
    return {
        "enabled": False,
        "starting_cash_cents": 0,
        "max_bet_size_cents": 0,
        "max_buy_usd_cum_cents": 0,
        "max_portfolio_drawdown": 0.0,
        "max_daily_loss_cents": 0,
        "position_stop_loss": 0.0,
        "max_open_positions": 0,
        "max_daily_trades": 0,
        "require_confirmation": False,
    }


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@api_router.get("/health")
async def get_health(request: Request) -> dict:
    """Serialize HealthReport from HealthMonitor plus scan cycle metrics."""
    # TODO: call request.app.state.health_monitor.check_health()
    return {
        "components": {},
        "scan_loop_alive": False,
        "errors": [],
        "scan_cycle_duration_ms": None,
        "avg_scan_cycle_duration_ms": None,
    }


# ---------------------------------------------------------------------------
# Scanner endpoints
# ---------------------------------------------------------------------------

@api_router.get("/scanner/status")
async def get_scanner_status(request: Request) -> dict:
    """Scanner mode, enabled engines, scan interval, last scan timestamp."""
    # TODO: read request.app.state.settings and scanner state
    return {
        "mode": "paper",
        "engines": [],
        "scan_interval_sec": 0.0,
        "last_scan_at": None,
        "verticals": {},
    }


# ---------------------------------------------------------------------------
# Balance endpoints
# ---------------------------------------------------------------------------

@api_router.get("/balance")
async def get_balance(request: Request) -> dict:
    """Current balance from RiskGuard.state."""
    # TODO: read request.app.state.risk_guard.state
    return {
        "balance_cents": 0,
        "peak_cents": 0,
        "drawdown_pct": 0.0,
        "cumulative_spend_cents": 0,
    }


@api_router.get("/balance/history")
async def get_balance_history(
    request: Request,
    since_hours: int = Query(24, ge=1),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    """Balance time series from balance_history table."""
    # TODO: query BalanceTracker.get_history()
    return {"snapshots": []}


# ---------------------------------------------------------------------------
# Scan cycle endpoints
# ---------------------------------------------------------------------------

@api_router.get("/scan-cycles")
async def get_scan_cycles(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Scan cycle history from scan_cycles table."""
    # TODO: query ScanCycleLogger.get_recent_cycles()
    return {
        "cycles": [],
        "total": 0,
    }


# ---------------------------------------------------------------------------
# Signal endpoints
# ---------------------------------------------------------------------------

@api_router.get("/signals/recent")
async def get_recent_signals(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Recent EdgeSignals from in-memory buffer (HTMX-polled, no WebSocket)."""
    # TODO: read from signal buffer on app.state
    return {
        "signals": [],
        "count": 0,
    }
