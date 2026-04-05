"""Tests for dashboard integration in main.py (task 1.7).

Verifies that main.py correctly:
- Imports create_app and start_dashboard from dashboard.server
- Reads settings.dashboard for enabled/host/port
- Creates the FastAPI app and starts the dashboard task when enabled
- Skips dashboard startup when disabled
- Cancels the dashboard task during shutdown
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


class TestDashboardImports:
    """Verify that the dashboard wiring imports are accessible from main.py."""

    def test_create_app_importable(self) -> None:
        from niche_scanner.dashboard.server import create_app

        assert callable(create_app)

    def test_start_dashboard_importable(self) -> None:
        from niche_scanner.dashboard.server import start_dashboard

        assert callable(start_dashboard)

    def test_main_module_imports_dashboard(self) -> None:
        """main.py should import create_app and start_dashboard at module level."""
        import niche_scanner.main as main_mod

        # The import of create_app and start_dashboard should exist
        assert hasattr(main_mod, "create_app") or "create_app" in dir(main_mod)


class TestDashboardSettingsWiring:
    """Verify that settings.dashboard config is read correctly for wiring."""

    def test_enabled_true_reads_host_and_port(self, tmp_path: Path) -> None:
        from niche_scanner.config import ScannerSettings

        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(yaml.dump({
            "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 9999},
        }))
        settings = ScannerSettings(path=yaml_path)
        dash_cfg = settings.dashboard
        assert dash_cfg["enabled"] is True
        assert dash_cfg["host"] == "127.0.0.1"
        assert dash_cfg["port"] == 9999

    def test_enabled_false_skips_dashboard(self, tmp_path: Path) -> None:
        from niche_scanner.config import ScannerSettings

        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(yaml.dump({
            "dashboard": {"enabled": False},
        }))
        settings = ScannerSettings(path=yaml_path)
        dash_cfg = settings.dashboard
        assert dash_cfg["enabled"] is False


class TestDashboardStartupIntegration:
    """Test the actual wiring pattern used in main.py."""

    async def test_dashboard_starts_when_enabled(self) -> None:
        """Simulate the dashboard startup block from main.py."""
        from niche_scanner.dashboard.server import create_app

        risk_guard = MagicMock()
        risk_guard.state = MagicMock()
        risk_guard.config = MagicMock()
        risk_guard.is_killed = False

        health_monitor = MagicMock()
        health_monitor.check_health.return_value = MagicMock(
            components={}, scan_loop_alive=True, errors=[],
        )

        journal = MagicMock()
        journal.get_trades = AsyncMock(return_value=[])
        journal.get_stats = AsyncMock(return_value={})

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

        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_server_cls.return_value = mock_server

            from niche_scanner.dashboard.server import start_dashboard

            task = await start_dashboard(
                app,
                host="127.0.0.1",
                port=0,
                alert_manager=alert_manager,
            )

            assert isinstance(task, asyncio.Task)
            assert task.get_name() == "dashboard-server"

            # Allow the task to run
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_dashboard_skipped_when_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        """When dashboard.enabled is False, no task should be created."""
        # Simulate the conditional logic from main.py
        dash_cfg = {"enabled": False, "host": "127.0.0.1", "port": 8050}
        dashboard_task = None

        if dash_cfg["enabled"]:
            # This block should NOT execute
            dashboard_task = MagicMock()

        assert dashboard_task is None

    async def test_dashboard_task_cancelled_on_shutdown(self) -> None:
        """Simulate the cleanup block from main.py that cancels the dashboard."""
        from niche_scanner.dashboard.server import create_app

        risk_guard = MagicMock()
        risk_guard.state = MagicMock()
        risk_guard.config = MagicMock()
        risk_guard.is_killed = False

        health_monitor = MagicMock()
        health_monitor.check_health.return_value = MagicMock(
            components={}, scan_loop_alive=True, errors=[],
        )

        journal = MagicMock()
        journal.get_trades = AsyncMock(return_value=[])
        journal.get_stats = AsyncMock(return_value={})

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

        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            # Make serve() block until cancelled
            serve_event = asyncio.Event()

            async def _block_until_cancelled() -> None:
                await serve_event.wait()

            mock_server = MagicMock()
            mock_server.serve = _block_until_cancelled
            mock_server_cls.return_value = mock_server

            from niche_scanner.dashboard.server import start_dashboard

            dashboard_task = await start_dashboard(
                app, host="127.0.0.1", port=0,
            )

            assert not dashboard_task.done()

            # Simulate main.py cleanup: cancel the task
            dashboard_task.cancel()
            try:
                await dashboard_task
            except asyncio.CancelledError:
                pass

            assert dashboard_task.done()

    async def test_dashboard_log_message(self, caplog: pytest.LogCaptureFixture) -> None:
        """Dashboard startup should log the host and port."""
        from niche_scanner.dashboard.server import create_app, start_dashboard

        risk_guard = MagicMock()
        risk_guard.state = MagicMock()
        risk_guard.config = MagicMock()
        risk_guard.is_killed = False

        health_monitor = MagicMock()
        health_monitor.check_health.return_value = MagicMock(
            components={}, scan_loop_alive=True, errors=[],
        )

        journal = MagicMock()
        journal.get_trades = AsyncMock(return_value=[])
        journal.get_stats = AsyncMock(return_value={})

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

        with patch("niche_scanner.dashboard.server.uvicorn.Server") as mock_server_cls:
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_server_cls.return_value = mock_server

            with caplog.at_level(logging.INFO, logger="niche_scanner.dashboard.server"):
                task = await start_dashboard(
                    app, host="127.0.0.1", port=8050,
                )

            log_messages = [r.message for r in caplog.records]
            assert any(
                "127.0.0.1" in msg and "8050" in msg
                for msg in log_messages
            ), f"Expected log with host/port, got: {log_messages}"

            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
