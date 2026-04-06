"""Tests for the Telegram alert manager."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import respx

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


# ---------------------------------------------------------------------------
# HTTP client lifecycle tests
# ---------------------------------------------------------------------------


class TestAlertManagerHTTPClient:
    """Tests for the shared httpx.AsyncClient lifecycle in AlertManager."""

    def test_http_client_is_none_initially(self) -> None:
        """The internal HTTP client is not created until first use."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        assert mgr._http_client is None

    def test_get_http_client_creates_on_first_call(self) -> None:
        """_get_http_client() lazily creates the client."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        client = mgr._get_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert mgr._http_client is client

    def test_get_http_client_returns_same_instance(self) -> None:
        """Subsequent calls return the same client instance."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        c1 = mgr._get_http_client()
        c2 = mgr._get_http_client()
        assert c1 is c2

    async def test_close_releases_client(self) -> None:
        """close() shuts down the HTTP client and resets to None."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        _ = mgr._get_http_client()
        assert mgr._http_client is not None

        await mgr.close()
        assert mgr._http_client is None

    async def test_close_is_idempotent(self) -> None:
        """Calling close() twice does not raise."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        await mgr.close()  # No client created yet
        await mgr.close()  # Still no error

    @respx.mock
    async def test_send_reuses_client(self) -> None:
        """_send() reuses the shared client across multiple calls."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")

        # Mock the Telegram API endpoint
        respx.post("https://api.telegram.org/botfake:token/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        await mgr._send("first message")
        client_after_first = mgr._http_client

        await mgr._send("second message")
        client_after_second = mgr._http_client

        assert client_after_first is client_after_second
        assert respx.calls.call_count == 2

    @respx.mock
    async def test_send_creates_client_lazily(self) -> None:
        """_send() creates the HTTP client if none exists yet."""
        from niche_scanner.alerts.telegram import AlertManager
        mgr = AlertManager(bot_token="fake:token", chat_id="123")
        assert mgr._http_client is None

        respx.post("https://api.telegram.org/botfake:token/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        await mgr._send("hello")
        assert mgr._http_client is not None
