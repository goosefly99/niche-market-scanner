"""Tests for DashboardServer lifecycle (create_app, start_dashboard, death watchdog)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from niche_scanner.dashboard.server import create_app, start_dashboard


def _make_app() -> MagicMock:
    """Build a FastAPI app backed by lightweight mocks."""
    risk_guard = MagicMock()
    risk_guard.state = MagicMock()
    risk_guard.config = MagicMock()
    risk_guard.is_killed = False
    risk_guard.kill_reason = ""

    health_monitor = MagicMock()
    health_monitor.check_health.return_value = MagicMock(
        components={}, scan_loop_alive=True, errors=[],
    )

    journal = MagicMock()
    journal.get_trades = AsyncMock(return_value=[])
    journal.get_stats = AsyncMock(return_value={
        "total_trades": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "avg_edge_pp": 0.0, "net_pnl_cents": 0,
    })

    settings = MagicMock()
    scanner = MagicMock()
    alert_manager = MagicMock()
    alert_manager.send_risk_warning = AsyncMock()

    app = create_app(
        risk_guard=risk_guard,
        health_monitor=health_monitor,
        journal=journal,
        settings=settings,
        scanner=scanner,
        alert_manager=alert_manager,
    )
    return app


class TestCreateApp:
    """Verify that create_app() produces a correctly configured FastAPI app."""

    def test_returns_fastapi_instance(self) -> None:
        from fastapi import FastAPI

        app = _make_app()
        assert isinstance(app, FastAPI)

    def test_shared_state_attached(self) -> None:
        app = _make_app()
        assert hasattr(app.state, "risk_guard")
        assert hasattr(app.state, "health_monitor")
        assert hasattr(app.state, "journal")
        assert hasattr(app.state, "settings")
        assert hasattr(app.state, "scanner")
        assert hasattr(app.state, "alert_manager")
        assert hasattr(app.state, "templates")
        assert hasattr(app.state, "scan_cycle_logger")
        assert hasattr(app.state, "balance_tracker")

    def test_api_router_mounted(self) -> None:
        app = _make_app()
        paths = [route.path for route in app.routes]
        assert "/api/ping" in paths
        assert "/api/trades" in paths
        assert "/api/risk/state" in paths
        assert "/api/health" in paths


class TestPingEndpoint:
    """Verify the /api/ping health check endpoint works end-to-end."""

    async def test_ping_returns_ok(self) -> None:
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestStartDashboard:
    """Verify the uvicorn task launch and death watchdog behaviour."""

    async def test_returns_asyncio_task(self) -> None:
        app = _make_app()
        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_server_cls.return_value = mock_server

            task = await start_dashboard(app, host="127.0.0.1", port=0)
            assert isinstance(task, asyncio.Task)
            # Let the task run and complete
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_security_warning_on_public_host(self, caplog) -> None:
        app = _make_app()
        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_server_cls.return_value = mock_server

            with caplog.at_level(logging.WARNING, logger="niche_scanner.dashboard.server"):
                task = await start_dashboard(app, host="0.0.0.0", port=0)

            assert any("without authentication" in rec.message for rec in caplog.records)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_no_warning_on_localhost(self, caplog) -> None:
        app = _make_app()
        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_server_cls.return_value = mock_server

            with caplog.at_level(logging.WARNING, logger="niche_scanner.dashboard.server"):
                task = await start_dashboard(app, host="127.0.0.1", port=0)

            auth_warnings = [
                r for r in caplog.records if "without authentication" in r.message
            ]
            assert len(auth_warnings) == 0
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_death_watchdog_sends_alert(self) -> None:
        """When the dashboard task crashes, the watchdog should alert."""
        app = _make_app()
        alert_manager = MagicMock()
        alert_manager.send_risk_warning = AsyncMock()

        crash_error = RuntimeError("test crash")

        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock(side_effect=crash_error)
            mock_server_cls.return_value = mock_server

            task = await start_dashboard(
                app, host="127.0.0.1", port=0, alert_manager=alert_manager,
            )
            # Wait for task to fail
            await asyncio.sleep(0.1)
            assert task.done()
            # The watchdog should have called send_risk_warning
            alert_manager.send_risk_warning.assert_called_once()
            call_args = alert_manager.send_risk_warning.call_args[0][0]
            assert "Dashboard server died" in call_args
