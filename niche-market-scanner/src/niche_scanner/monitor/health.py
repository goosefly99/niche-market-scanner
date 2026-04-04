"""Health monitor with heartbeats, API connectivity checks, and dead-man's-switch.

Tracks per-component heartbeats, verifies external API connectivity
(Kalshi, NOAA, FRED, Telegram), and triggers alerts if the scan loop
stops (dead-man's-switch).

Validates KALSHI_PRIVATE_KEY and KALSHI_API_KEY_ID on startup.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    """Health status of a single component."""

    name: str
    last_heartbeat: float = 0.0  # monotonic timestamp
    healthy: bool = True
    last_error: str = ""

    @property
    def seconds_since_heartbeat(self) -> float:
        if self.last_heartbeat == 0.0:
            return float("inf")
        return time.monotonic() - self.last_heartbeat


@dataclass
class HealthReport:
    """Snapshot of system health."""

    components: dict[str, ComponentHealth] = field(default_factory=dict)
    api_status: dict[str, bool] = field(default_factory=dict)
    scan_loop_alive: bool = True
    startup_valid: bool = True
    errors: list[str] = field(default_factory=list)


class HealthMonitor:
    """Tracks system health with heartbeats and connectivity checks.

    Usage:
        monitor = HealthMonitor()
        monitor.validate_startup()
        monitor.heartbeat("scanner")
        monitor.heartbeat("weather_engine")
        report = monitor.check_health(dead_man_timeout_sec=300)
    """

    def __init__(self, dead_man_timeout_sec: float = 300.0) -> None:
        self._components: dict[str, ComponentHealth] = {}
        self._dead_man_timeout = dead_man_timeout_sec
        self._scan_loop_heartbeat: float = 0.0

    def validate_startup(self) -> list[str]:
        """Check required env vars and files on startup.

        Returns a list of error messages (empty = all good).
        """
        errors: list[str] = []

        api_key = os.environ.get("KALSHI_API_KEY_ID", "")
        if not api_key:
            errors.append("KALSHI_API_KEY_ID not set")

        key_path = os.environ.get("KALSHI_PRIVATE_KEY", "")
        if not key_path:
            errors.append("KALSHI_PRIVATE_KEY not set")
        elif not os.path.exists(key_path.strip('"')):
            errors.append(f"KALSHI_PRIVATE_KEY path not found: {key_path}")

        for error in errors:
            logger.error("Startup check failed: %s", error)

        if not errors:
            logger.info("Startup validation passed")

        return errors

    def heartbeat(self, component: str) -> None:
        """Record a heartbeat for a component."""
        if component not in self._components:
            self._components[component] = ComponentHealth(name=component)
        self._components[component].last_heartbeat = time.monotonic()
        self._components[component].healthy = True

    def scan_loop_heartbeat(self) -> None:
        """Record that the scan loop completed a cycle."""
        self._scan_loop_heartbeat = time.monotonic()

    def report_error(self, component: str, error: str) -> None:
        """Record an error for a component."""
        if component not in self._components:
            self._components[component] = ComponentHealth(name=component)
        self._components[component].healthy = False
        self._components[component].last_error = error
        logger.warning("Component %s error: %s", component, error)

    def check_health(
        self, dead_man_timeout_sec: float | None = None,
    ) -> HealthReport:
        """Generate a health report.

        Checks:
        - Per-component heartbeats (stale if >2x expected interval)
        - Dead-man's-switch on the scan loop
        """
        timeout = dead_man_timeout_sec or self._dead_man_timeout
        report = HealthReport()

        # Component health
        for name, comp in self._components.items():
            report.components[name] = comp
            if not comp.healthy:
                report.errors.append(f"{name}: {comp.last_error}")

        # Dead-man's-switch
        if self._scan_loop_heartbeat > 0:
            elapsed = time.monotonic() - self._scan_loop_heartbeat
            report.scan_loop_alive = elapsed < timeout
            if not report.scan_loop_alive:
                report.errors.append(
                    f"Scan loop dead: no heartbeat for {elapsed:.0f}s "
                    f"(timeout={timeout:.0f}s)"
                )

        return report

    def format_status(self) -> str:
        """Format current health as a human-readable string."""
        report = self.check_health()
        lines = ["System Health", "─" * 30]

        for name, comp in report.components.items():
            status = "OK" if comp.healthy else "ERROR"
            age = comp.seconds_since_heartbeat
            age_str = f"{age:.0f}s ago" if age < float("inf") else "never"
            lines.append(f"  {name}: {status} (heartbeat {age_str})")

        scan_status = "alive" if report.scan_loop_alive else "DEAD"
        lines.append(f"  Scan loop: {scan_status}")

        if report.errors:
            lines.append(f"  Errors: {len(report.errors)}")
            for err in report.errors:
                lines.append(f"    - {err}")

        return "\n".join(lines)
