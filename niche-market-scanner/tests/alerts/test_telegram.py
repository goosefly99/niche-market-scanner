"""Tests for the Telegram alert manager."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.sizing.kelly import PositionSize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal(
    engine: str = "weather",
    ticker: str = "KXHIGHNY-26APR05-T67",
    side: str = "yes",
    edge: float = 15.0,
) -> EdgeSignal:
    return EdgeSignal(
        engine=engine,
        ticker=ticker,
        side=side,
        model_prob=0.70,
        market_prob=0.55,
        edge_pp=edge,
        fee_adjusted_edge=edge - 3.0,
        confidence=0.8,
        thesis=f"NOAA says {side.upper()} on {ticker}",
        metadata={"icao": "KNYC"},
        timestamp=datetime.now(timezone.utc),
    )


def _size(
    ticker: str = "KXHIGHNY-26APR05-T67",
    contracts: int = 10,
) -> PositionSize:
    return PositionSize(
        ticker=ticker,
        side="yes",
        contracts=contracts,
        price_cents=55,
        cost_cents=550,
        kelly_raw=0.08,
        kelly_fraction_used=0.04,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAlertManager:
    """Tests for AlertManager Telegram integration."""

    def test_import(self) -> None:
        """AlertManager can be imported."""
        from niche_scanner.alerts.telegram import AlertManager
        assert AlertManager is not None

    def test_init_without_credentials(self) -> None:
        """AlertManager initializes gracefully without Telegram credentials."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        assert mgr.enabled is False

    def test_init_with_credentials(self) -> None:
        """AlertManager is enabled when credentials are provided."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="123:ABC", chat_id="456")
        assert mgr.enabled is True

    def test_format_edge_signal(self) -> None:
        """Edge signal is formatted into a readable Telegram message."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        msg = mgr.format_signal(_signal(), _size())
        assert "[PAPER]" in msg
        assert "KXHIGHNY" in msg
        assert "YES" in msg
        assert "15.0pp" in msg
        assert "10" in msg  # contracts

    def test_format_daily_summary(self) -> None:
        """Daily summary includes key metrics."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        stats = {
            "total_trades": 5,
            "wins": 3,
            "losses": 2,
            "win_rate": 0.6,
            "net_pnl_cents": 1500,
        }
        msg = mgr.format_daily_summary(stats)
        assert "5 trades" in msg
        assert "60%" in msg or "0.6" in msg
        assert "$15.00" in msg or "1500" in msg

    async def test_send_skips_when_disabled(self) -> None:
        """send_message does nothing when Telegram is disabled."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        # Should not raise even though no bot is configured
        await mgr.send_message("test")

    async def test_send_signal_alert(self) -> None:
        """send_signal_alert formats and sends a signal message."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        mgr._send = AsyncMock()  # type: ignore[assignment]

        await mgr.send_signal_alert(_signal(), _size())
        mgr._send.assert_called_once()
        msg = mgr._send.call_args[0][0]
        assert "KXHIGHNY" in msg

    async def test_command_status(self) -> None:
        """The /status command handler returns scanner status."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        response = mgr.handle_command("/status")
        assert "Scanner" in response or "status" in response.lower()

    async def test_command_unknown(self) -> None:
        """Unknown commands return a help message."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="", chat_id="")
        response = mgr.handle_command("/foobar")
        assert "Unknown" in response or "help" in response.lower()
