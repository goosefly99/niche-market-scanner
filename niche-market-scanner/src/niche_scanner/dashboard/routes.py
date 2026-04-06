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

import dataclasses
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

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
    """Paginated trade list with optional engine/action/outcome filters.

    Builds a dynamic WHERE clause from the provided filters. The ``outcome``
    filter value ``"pending"`` matches rows where ``outcome IS NULL``.
    """
    journal = request.app.state.journal
    conn: aiosqlite.Connection = journal.connection
    conn.row_factory = aiosqlite.Row

    clauses: list[str] = []
    params: list[str | int] = []

    if engine is not None:
        clauses.append("engine = ?")
        params.append(engine)
    if action is not None:
        clauses.append("action = ?")
        params.append(action)
    if outcome is not None:
        if outcome == "pending":
            clauses.append("outcome IS NULL")
        else:
            clauses.append("outcome = ?")
            params.append(outcome)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    # Total count for pagination
    count_cursor = await conn.execute(
        f"SELECT COUNT(*) FROM trades{where}", params,
    )
    count_row = await count_cursor.fetchone()
    total: int = count_row[0] if count_row else 0

    # Paginated rows
    cursor = await conn.execute(
        f"SELECT * FROM trades{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()
    trades = [dict(row) for row in rows]

    return {
        "trades": trades,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/trades/{trade_id}")
async def get_trade_by_id(request: Request, trade_id: int) -> JSONResponse:
    """Single trade detail by ID.

    Returns 404 if the trade does not exist.
    """
    journal = request.app.state.journal
    conn: aiosqlite.Connection = journal.connection
    conn.row_factory = aiosqlite.Row

    cursor = await conn.execute(
        "SELECT * FROM trades WHERE id = ?", (trade_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Trade not found", "trade_id": trade_id},
        )
    return JSONResponse(content={"trade": dict(row)})


@api_router.get("/stats")
async def get_stats(
    request: Request,
    engine: Optional[str] = Query(None, description="Filter by engine"),
) -> dict:
    """Aggregate trade statistics via TradeJournal.get_stats()."""
    journal = request.app.state.journal
    stats: dict = await journal.get_stats(engine=engine)
    return stats


@api_router.get("/stats/daily")
async def get_daily_stats(request: Request) -> dict:
    """Daily P&L aggregation for charting.

    Groups resolved trades by date(created_at) and sums net P&L per day.
    """
    journal = request.app.state.journal
    conn: aiosqlite.Connection = journal.connection
    conn.row_factory = aiosqlite.Row

    cursor = await conn.execute(
        """
        SELECT
            date(created_at)                             AS day,
            COUNT(*)                                     AS trade_count,
            COALESCE(SUM(outcome = 'win'), 0)            AS wins,
            COALESCE(SUM(outcome = 'loss'), 0)           AS losses,
            COALESCE(SUM(payout_cents - cost_cents), 0)  AS net_pnl_cents
        FROM trades
        WHERE outcome IS NOT NULL
        GROUP BY date(created_at)
        ORDER BY day ASC
        """,
    )
    rows = await cursor.fetchall()
    days = [dict(row) for row in rows]

    return {"days": days}


# ---------------------------------------------------------------------------
# Risk endpoints
# ---------------------------------------------------------------------------


def _serialize_risk_state(risk_guard: object) -> dict:
    """Serialize RiskState dataclass from RiskGuard to a JSON-safe dict."""
    state = risk_guard.state  # type: ignore[attr-defined]
    data = dataclasses.asdict(state)
    return data


def _serialize_risk_config(risk_guard: object) -> dict:
    """Serialize LiveTradingConfig dataclass from RiskGuard to a JSON-safe dict."""
    config = risk_guard.config  # type: ignore[attr-defined]
    data = dataclasses.asdict(config)
    return data


@api_router.get("/risk/state")
async def get_risk_state(request: Request) -> dict:
    """Serialize full RiskState from RiskGuard.

    Returns a zeroed-out state dict when running in paper mode
    (``risk_guard`` is ``None``).
    """
    risk_guard = request.app.state.risk_guard
    if risk_guard is None:
        return {
            "peak_balance_cents": 0,
            "current_balance_cents": 0,
            "cumulative_spend_cents": 0,
            "daily_loss_cents": 0,
            "daily_trade_count": 0,
            "open_position_count": 0,
            "day_start_balance_cents": 0,
            "day_start_timestamp": 0.0,
            "killed": False,
            "kill_reason": "",
            "positions": {},
        }
    return _serialize_risk_state(risk_guard)


@api_router.get("/risk/config")
async def get_risk_config(request: Request) -> dict:
    """Serialize LiveTradingConfig from RiskGuard.

    Returns an empty dict when running in paper mode
    (``risk_guard`` is ``None``).
    """
    risk_guard = request.app.state.risk_guard
    if risk_guard is None:
        return {}
    return _serialize_risk_config(risk_guard)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


def _serialize_health_report(report: object) -> dict:
    """Serialize a HealthReport to a JSON-safe dict.

    ComponentHealth objects contain monotonic timestamps that aren't
    meaningful to clients, so we convert them to ``seconds_since_heartbeat``.
    """
    components: dict = {}
    for name, comp in report.components.items():  # type: ignore[attr-defined]
        components[name] = {
            "name": comp.name,
            "healthy": comp.healthy,
            "last_error": comp.last_error,
            "seconds_since_heartbeat": round(comp.seconds_since_heartbeat, 1),
        }

    return {
        "components": components,
        "scan_loop_alive": report.scan_loop_alive,  # type: ignore[attr-defined]
        "errors": list(report.errors),  # type: ignore[attr-defined]
    }


@api_router.get("/health")
async def get_health(request: Request) -> dict:
    """Serialize HealthReport from HealthMonitor plus scan cycle metrics."""
    health_monitor = request.app.state.health_monitor
    report = health_monitor.check_health()
    return _serialize_health_report(report)


# ---------------------------------------------------------------------------
# Scanner endpoints
# ---------------------------------------------------------------------------


@api_router.get("/scanner/status")
async def get_scanner_status(request: Request) -> dict:
    """Scanner mode, enabled engines, scan interval, last scan timestamp.

    ``killed`` is always ``False`` in paper mode (no risk guard).
    """
    settings = request.app.state.settings
    risk_guard = request.app.state.risk_guard

    scanner_cfg = settings.scanner
    sizing_cfg = settings.sizing
    verticals_cfg = settings.verticals

    # Determine mode from sizing config
    mode = "paper" if sizing_cfg.get("paper_trade", True) else "live"

    # Gather enabled engines
    engines = [
        name for name, cfg in verticals_cfg.items()
        if isinstance(cfg, dict) and cfg.get("enabled", False)
    ]

    return {
        "mode": mode,
        "engines": engines,
        "scan_intervals": {
            "weather_interval_min": scanner_cfg.get("weather_interval_min"),
            "economics_interval_min": scanner_cfg.get("economics_interval_min"),
        },
        "verticals": verticals_cfg,
        "killed": risk_guard.is_killed if risk_guard is not None else False,
    }


# ---------------------------------------------------------------------------
# Balance endpoints
# ---------------------------------------------------------------------------


@api_router.get("/balance")
async def get_balance(request: Request) -> dict:
    """Current balance from RiskGuard.state.

    In paper mode (``risk_guard`` is ``None``) returns zeroed values.
    The paper-mode effective balance is tracked by ``main.py`` via
    ``BalanceTracker`` snapshots instead.
    """
    risk_guard = request.app.state.risk_guard
    if risk_guard is None:
        return {
            "balance_cents": 0,
            "peak_cents": 0,
            "drawdown_pct": 0.0,
            "cumulative_spend_cents": 0,
        }

    state = risk_guard.state

    peak = state.peak_balance_cents
    current = state.current_balance_cents
    drawdown_pct = (1.0 - current / peak) if peak > 0 else 0.0

    return {
        "balance_cents": current,
        "peak_cents": peak,
        "drawdown_pct": round(drawdown_pct, 6),
        "cumulative_spend_cents": state.cumulative_spend_cents,
    }


@api_router.get("/balance/history")
async def get_balance_history(
    request: Request,
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    """Balance time series from balance_history table.

    Returns snapshots ordered oldest-first (chronological) for charting.
    """
    tracker = request.app.state.balance_tracker
    snapshots = await tracker.get_history(limit=limit)
    return {"snapshots": snapshots}


# ---------------------------------------------------------------------------
# Scan cycle endpoints
# ---------------------------------------------------------------------------


@api_router.get("/scan-cycles")
async def get_scan_cycles(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """Scan cycle history from scan_cycles table.

    Returns cycles ordered newest-first for the dashboard activity log.
    """
    cycle_logger = request.app.state.scan_cycle_logger
    cycles = await cycle_logger.get_recent_cycles(limit=limit)
    return {
        "cycles": cycles,
        "total": len(cycles),
    }


# ---------------------------------------------------------------------------
# Signal endpoints
# ---------------------------------------------------------------------------


def _serialize_signal(signal: object) -> dict:
    """Serialize an :class:`EdgeSignal` to a JSON-safe dict.

    Converts the ``datetime`` timestamp to an ISO-8601 string and keeps
    the metadata dict as-is (caller is responsible for only inserting
    JSON-serialisable values into metadata).
    """
    ts = getattr(signal, "timestamp", None)
    ts_str = ts.isoformat() if ts is not None else None
    return {
        "engine": signal.engine,
        "ticker": signal.ticker,
        "side": signal.side,
        "model_prob": signal.model_prob,
        "market_prob": signal.market_prob,
        "edge_pp": signal.edge_pp,
        "fee_adjusted_edge": signal.fee_adjusted_edge,
        "confidence": signal.confidence,
        "thesis": signal.thesis,
        "metadata": dict(signal.metadata) if signal.metadata else {},
        "timestamp": ts_str,
    }


@api_router.get("/signals/recent")
async def get_recent_signals(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Recent EdgeSignals from in-memory buffer (HTMX-polled, no WebSocket).

    Reads from ``app.state.signal_buffer`` — a bounded deque populated by
    the scan loop in ``main.py``.  Returns newest first.
    """
    signal_buffer = getattr(request.app.state, "signal_buffer", None)
    if signal_buffer is None:
        return {"signals": [], "count": 0}

    recent = signal_buffer.recent(limit=limit)
    payload = [_serialize_signal(s) for s in recent]
    return {
        "signals": payload,
        "count": len(payload),
    }
