"""HTML page route handlers for the dashboard.

Serves the four dashboard views (/, /trades, /signals, /config) via Jinja2
template rendering. Each route builds a template context from shared app state
and renders either a full page (initial navigation) or an HTMX partial
fragment (subsequent swap) based on the ``HX-Request`` header.

Jinja2 template filters (cents_to_dollars, format_pct, relative_time,
risk_color, duration_ms_format) are registered at module level and added
to the template environment when the router is included.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

pages_router = APIRouter(tags=["pages"])


# ---------------------------------------------------------------------------
# Jinja2 template filters
# ---------------------------------------------------------------------------


def cents_to_dollars(value: int | float | None) -> str:
    """Convert cents to dollars, formatted as ``$X.XX``.

    >>> cents_to_dollars(12345)
    '$123.45'
    >>> cents_to_dollars(0)
    '$0.00'
    >>> cents_to_dollars(None)
    '$0.00'
    """
    if value is None:
        value = 0
    return f"${value / 100:.2f}"


def format_pct(value: float | None) -> str:
    """Format a decimal ratio as a percentage string ``X.X%``.

    >>> format_pct(0.753)
    '75.3%'
    >>> format_pct(0.0)
    '0.0%'
    >>> format_pct(None)
    '0.0%'
    """
    if value is None:
        value = 0.0
    return f"{value * 100:.1f}%"


def relative_time(seconds: float | int | None) -> str:
    """Convert seconds to a human-readable relative time string.

    >>> relative_time(5)
    '5s ago'
    >>> relative_time(90)
    '1m ago'
    >>> relative_time(7200)
    '2h ago'
    >>> relative_time(None)
    'N/A'
    """
    if seconds is None:
        return "N/A"
    seconds = abs(float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{int(seconds // 3600)}h ago"


def risk_color(value: float, threshold: float) -> str:
    """Return a Tailwind CSS color class based on proximity to a risk threshold.

    - Green (< 50% of threshold)
    - Yellow (50-80% of threshold)
    - Red (>= 80% of threshold)

    >>> risk_color(0.03, 0.15)
    'text-green-400'
    >>> risk_color(0.10, 0.15)
    'text-yellow-400'
    >>> risk_color(0.14, 0.15)
    'text-red-400'
    """
    if threshold <= 0:
        return "text-gray-400"
    ratio = value / threshold
    if ratio >= 0.8:
        return "text-red-400"
    if ratio >= 0.5:
        return "text-yellow-400"
    return "text-green-400"


def duration_ms_format(ms: float | int | None) -> str:
    """Format milliseconds as a human-readable duration.

    >>> duration_ms_format(1234)
    '1.2s'
    >>> duration_ms_format(450)
    '450ms'
    >>> duration_ms_format(None)
    'N/A'
    """
    if ms is None:
        return "N/A"
    ms = float(ms)
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{int(ms)}ms"


TEMPLATE_FILTERS: dict[str, Any] = {
    "cents_to_dollars": cents_to_dollars,
    "format_pct": format_pct,
    "relative_time": relative_time,
    "risk_color": risk_color,
    "duration_ms_format": duration_ms_format,
}


def register_filters(app: Any) -> None:
    """Register all custom Jinja2 filters on the app's template environment.

    Called from ``server.py`` after templates are configured.
    """
    templates = app.state.templates
    for name, func in TEMPLATE_FILTERS.items():
        templates.env.filters[name] = func


# ---------------------------------------------------------------------------
# Helper: detect HTMX partial request
# ---------------------------------------------------------------------------


def _is_htmx(request: Request) -> bool:
    """Return True if the request was made by HTMX (partial swap)."""
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@pages_router.get("/", response_class=HTMLResponse)
async def overview_page(request: Request) -> HTMLResponse:
    """Overview page: P&L cards, risk gauges, health lights, open positions.

    On initial load, renders the full ``overview.html`` template with data
    fetched from TradeJournal, RiskGuard, and HealthMonitor. HTMX partial
    requests are handled by dedicated partial endpoints (Phase 2.7).

    The balance chart is populated from ``BalanceTracker.get_history()``
    — snapshots are returned oldest-first and rendered client-side by a
    Chart.js line chart. The chart degrades gracefully to an empty state
    when no snapshots have been recorded yet.
    """
    journal = request.app.state.journal
    risk_guard = request.app.state.risk_guard
    health_monitor = request.app.state.health_monitor
    balance_tracker = getattr(request.app.state, "balance_tracker", None)

    # Fetch stats
    stats: dict = await journal.get_stats()

    # Risk state and config — None in paper mode (no RiskGuard).
    if risk_guard is not None:
        risk_state = risk_guard.state
        risk_config = risk_guard.config
        peak = risk_state.peak_balance_cents
        current = risk_state.current_balance_cents
        drawdown_pct = (1.0 - current / peak) if peak > 0 else 0.0
        cumulative_spend = risk_state.cumulative_spend_cents
        killed = risk_state.killed
        kill_reason = risk_state.kill_reason
    else:
        risk_state = None
        risk_config = None
        peak = 0
        current = 0
        drawdown_pct = 0.0
        cumulative_spend = 0
        killed = False
        kill_reason = ""

    # Health report
    health_report = health_monitor.check_health()

    # Balance history for the chart (oldest first).  Best-effort: if the
    # tracker isn't configured or the query fails we still render the page
    # with an empty series so the user sees the rest of the dashboard.
    balance_history: list[dict] = []
    if balance_tracker is not None:
        try:
            balance_history = await balance_tracker.get_history(limit=200)
        except Exception:
            logger.exception("Failed to load balance history for overview page")

    context = {
        "request": request,
        "active_page": "overview",
        "stats": stats,
        "risk_state": risk_state,
        "risk_config": risk_config,
        "balance_cents": current,
        "peak_cents": peak,
        "drawdown_pct": drawdown_pct,
        "cumulative_spend_cents": cumulative_spend,
        "health_report": health_report,
        "balance_history": balance_history,
        "killed": killed,
        "kill_reason": kill_reason,
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "overview.html", context)


@pages_router.get("/trades", response_class=HTMLResponse)
async def trades_page(
    request: Request,
    engine: str | None = Query(None),
    action: str | None = Query(None),
    outcome: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> HTMLResponse:
    """Trade history page: filterable, paginated table.

    When an HTMX request targets the trade table body, renders only the
    ``_trade_rows.html`` partial. Otherwise renders the full page.

    Delegates filtering and pagination to ``TradeJournal.query_trades()``.
    """
    journal = request.app.state.journal
    risk_guard = request.app.state.risk_guard

    trades, total = await journal.query_trades(
        engine=engine,
        action=action,
        outcome=outcome,
        limit=limit,
        offset=offset,
    )

    context = {
        "request": request,
        "active_page": "trades",
        "trades": trades,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filter_engine": engine,
        "filter_action": action,
        "filter_outcome": outcome,
        "killed": risk_guard.state.killed if risk_guard is not None else False,
        "kill_reason": risk_guard.state.kill_reason if risk_guard is not None else "",
    }

    templates = request.app.state.templates
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/_trade_rows.html", context)
    return templates.TemplateResponse(request, "trades.html", context)


@pages_router.get("/signals", response_class=HTMLResponse)
async def signals_page(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> HTMLResponse:
    """Signals page: HTMX-polled recent signals table (every 10s).

    Renders from the in-memory ``signal_buffer`` on ``app.state`` —
    populated by the scan loop in ``main.py``.  Newest first.
    """
    risk_guard = request.app.state.risk_guard
    signal_buffer = getattr(request.app.state, "signal_buffer", None)

    signals: list[dict] = []
    if signal_buffer is not None:
        for s in signal_buffer.recent(limit=limit):
            signals.append({
                "engine": s.engine,
                "ticker": s.ticker,
                "side": s.side,
                "model_prob": s.model_prob,
                "market_prob": s.market_prob,
                "edge_pp": s.edge_pp,
                "fee_adjusted_edge": s.fee_adjusted_edge,
                "confidence": s.confidence,
                "thesis": s.thesis,
                "kelly_fraction": float(
                    s.metadata.get("kelly_fraction", 0.0),
                ) if s.metadata else 0.0,
                "timestamp": s.timestamp,
            })

    context = {
        "request": request,
        "active_page": "signals",
        "signals": signals,
        "signal_count": len(signals),
        "killed": risk_guard.state.killed if risk_guard is not None else False,
        "kill_reason": risk_guard.state.kill_reason if risk_guard is not None else "",
    }

    templates = request.app.state.templates
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/_signals_table.html", context)
    return templates.TemplateResponse(request, "signals.html", context)


@pages_router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """Config page: read-only settings display with reload button."""
    settings = request.app.state.settings
    risk_guard = request.app.state.risk_guard

    context = {
        "request": request,
        "active_page": "config",
        "settings": settings,
        "killed": risk_guard.state.killed if risk_guard is not None else False,
        "kill_reason": risk_guard.state.kill_reason if risk_guard is not None else "",
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "config.html", context)
