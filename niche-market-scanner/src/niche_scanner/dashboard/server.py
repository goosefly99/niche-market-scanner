"""Dashboard server lifecycle manager.

Runs a FastAPI/uvicorn ASGI server **in-process** with the niche-market-scanner,
sharing the same asyncio event loop. This avoids IPC complexity: the dashboard
reads RiskGuard, HealthMonitor, and TradeJournal state via direct Python object
references stored on ``FastAPI.app.state``.

Startup sequence (called from main.py):
    1. ``create_app()`` builds the FastAPI instance and mounts all routers.
    2. ``start_dashboard()`` launches uvicorn as an asyncio task alongside the
       scanner's scan loop.
    3. A done-callback on the task sends a Telegram alert via AlertManager if
       the dashboard dies unexpectedly (death watchdog).

The scanner continues to operate normally if the dashboard fails to start
(port conflict, uvicorn error) — graceful degradation with clear log messages.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from niche_scanner.alerts.telegram import AlertManager
    from niche_scanner.config import ScannerSettings
    from niche_scanner.execution.risk_guard import RiskGuard
    from niche_scanner.journal.trade_journal import TradeJournal
    from niche_scanner.monitor.health import HealthMonitor
    from niche_scanner.scanner.market_scanner import MarketScanner

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(
    *,
    risk_guard: RiskGuard,
    health_monitor: HealthMonitor,
    journal: TradeJournal,
    settings: ScannerSettings,
    scanner: MarketScanner,
    alert_manager: AlertManager,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Shared component references are stored on ``app.state`` so that route
    handlers can access them via ``request.app.state.<component>``.
    """
    app = FastAPI(
        title="Niche Market Scanner Dashboard",
        description="Monitoring and safety control surface for the Kalshi trading bot.",
        version="0.1.0",
    )

    # --- Shared state ---------------------------------------------------
    app.state.risk_guard = risk_guard
    app.state.health_monitor = health_monitor
    app.state.journal = journal
    app.state.settings = settings
    app.state.scanner = scanner
    app.state.alert_manager = alert_manager

    # --- Templates ------------------------------------------------------
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # --- Routers --------------------------------------------------------
    from niche_scanner.dashboard.routes import api_router

    app.include_router(api_router)

    # Phase 2: pages_router (HTML page routes)
    # Phase 5: kill_switch_router (POST endpoints for kill/reset/reload)

    return app


async def start_dashboard(
    app: FastAPI,
    *,
    host: str = "127.0.0.1",
    port: int = 8050,
    alert_manager: AlertManager | None = None,
) -> asyncio.Task[None]:
    """Start uvicorn as an asyncio task and return the task handle.

    Parameters
    ----------
    app:
        The FastAPI application created by :func:`create_app`.
    host:
        Bind address. Defaults to ``127.0.0.1`` (localhost only).
    port:
        Listen port. Defaults to ``8050``.
    alert_manager:
        If provided, the death watchdog will send a Telegram alert when the
        dashboard task dies unexpectedly.

    Returns
    -------
    asyncio.Task
        The running uvicorn server task. Caller is responsible for cancelling
        it during shutdown.
    """
    # Security warning for non-localhost binding
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Dashboard is bound to %s without authentication. "
            "Trading data and kill switch are accessible to anyone on the network.",
            host,
        )

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    async def _serve() -> None:
        try:
            await server.serve()
        except Exception:
            logger.critical("Dashboard server crashed", exc_info=True)
            raise

    task = asyncio.create_task(_serve(), name="dashboard-server")

    # --- Death watchdog -------------------------------------------------
    def _on_dashboard_done(t: asyncio.Task[None]) -> None:
        if t.cancelled():
            logger.info("Dashboard server task cancelled (expected during shutdown).")
            return
        exc = t.exception()
        if exc is not None:
            msg = f"Dashboard server died: {exc}"
            logger.critical(msg)
            if alert_manager is not None:
                # Schedule the async alert in the running loop
                asyncio.ensure_future(alert_manager.send_risk_warning(msg))

    task.add_done_callback(_on_dashboard_done)

    logger.info("Dashboard server starting on http://%s:%d", host, port)
    return task
