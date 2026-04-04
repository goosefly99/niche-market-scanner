"""Tests for the health monitor."""

from __future__ import annotations

import time

from niche_scanner.monitor.health import HealthMonitor


class TestHealthMonitor:
    """Tests for HealthMonitor heartbeats and dead-man's-switch."""

    def test_heartbeat_registers_component(self) -> None:
        mon = HealthMonitor()
        mon.heartbeat("scanner")
        assert "scanner" in mon._components
        assert mon._components["scanner"].healthy is True

    def test_report_error(self) -> None:
        mon = HealthMonitor()
        mon.heartbeat("weather")
        mon.report_error("weather", "NOAA timeout")
        assert mon._components["weather"].healthy is False
        assert mon._components["weather"].last_error == "NOAA timeout"

    def test_check_health_includes_components(self) -> None:
        mon = HealthMonitor()
        mon.heartbeat("scanner")
        mon.heartbeat("weather")
        report = mon.check_health()
        assert len(report.components) == 2
        assert report.components["scanner"].healthy is True

    def test_check_health_reports_errors(self) -> None:
        mon = HealthMonitor()
        mon.report_error("kalshi_api", "401 Unauthorized")
        report = mon.check_health()
        assert len(report.errors) == 1
        assert "401" in report.errors[0]

    def test_dead_man_switch_alive(self) -> None:
        mon = HealthMonitor(dead_man_timeout_sec=60)
        mon.scan_loop_heartbeat()
        report = mon.check_health()
        assert report.scan_loop_alive is True

    def test_dead_man_switch_dead(self) -> None:
        mon = HealthMonitor(dead_man_timeout_sec=0.001)
        mon.scan_loop_heartbeat()
        time.sleep(0.01)  # Exceed the tiny timeout
        report = mon.check_health()
        assert report.scan_loop_alive is False
        assert any("dead" in e.lower() for e in report.errors)

    def test_validate_startup_missing_key(self, monkeypatch) -> None:
        monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
        monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
        mon = HealthMonitor()
        errors = mon.validate_startup()
        assert len(errors) >= 2
        assert any("KALSHI_API_KEY_ID" in e for e in errors)

    def test_validate_startup_ok(self, monkeypatch, tmp_path) -> None:
        key_file = tmp_path / "test.pem"
        key_file.write_text("fake key")
        monkeypatch.setenv("KALSHI_API_KEY_ID", "test-id")
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", str(key_file))
        mon = HealthMonitor()
        errors = mon.validate_startup()
        assert errors == []

    def test_format_status(self) -> None:
        mon = HealthMonitor()
        mon.heartbeat("scanner")
        mon.heartbeat("weather")
        mon.report_error("economics", "FRED timeout")
        status = mon.format_status()
        assert "scanner" in status
        assert "weather" in status
        assert "ERROR" in status
        assert "FRED timeout" in status
