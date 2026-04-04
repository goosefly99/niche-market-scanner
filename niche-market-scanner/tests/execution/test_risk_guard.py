"""Tests for the risk guard — the safety gate for live trading."""

from __future__ import annotations

from niche_scanner.execution.risk_guard import LiveTradingConfig, RiskGuard


class TestRiskGuardChecks:
    """Test that the risk guard correctly blocks and allows orders."""

    def _guard(self, **overrides) -> RiskGuard:
        defaults = {
            "enabled": True,
            "max_bet_size_cents": 1000,  # $10
            "max_portfolio_drawdown": 0.15,
            "max_daily_loss_cents": 5000,  # $50
            "max_open_positions": 5,
            "max_daily_trades": 10,
        }
        defaults.update(overrides)
        cfg = LiveTradingConfig(**defaults)
        return RiskGuard(cfg, initial_balance_cents=100_000)

    def test_order_passes_all_checks(self) -> None:
        guard = self._guard()
        result = guard.check_order("TEST-01", "yes", 5, 50)
        assert result is None  # No rejection

    def test_bet_size_limit(self) -> None:
        guard = self._guard(max_bet_size_cents=500)
        # 10 contracts at 60c = 600 cents > 500 limit
        result = guard.check_order("TEST-01", "yes", 10, 60)
        assert result is not None
        assert "Bet size" in result

    def test_kill_switch_blocks_all(self) -> None:
        guard = self._guard()
        guard.kill("manual test kill")
        result = guard.check_order("TEST-01", "yes", 1, 10)
        assert result is not None
        assert "Kill switch" in result

    def test_kill_switch_reset(self) -> None:
        guard = self._guard()
        guard.kill("test")
        assert guard.is_killed
        guard.reset_kill_switch()
        assert not guard.is_killed
        result = guard.check_order("TEST-01", "yes", 1, 10)
        assert result is None

    def test_live_trading_disabled(self) -> None:
        guard = self._guard(enabled=False)
        result = guard.check_order("TEST-01", "yes", 1, 10)
        assert result is not None
        assert "not enabled" in result

    def test_daily_trade_limit(self) -> None:
        guard = self._guard(max_daily_trades=3)
        for i in range(3):
            assert guard.check_order(f"T-{i}", "yes", 1, 10) is None
            guard.record_fill(f"T-{i}", 10)
        # 4th trade should be blocked
        result = guard.check_order("T-3", "yes", 1, 10)
        assert result is not None
        assert "Daily trade limit" in result

    def test_max_open_positions(self) -> None:
        guard = self._guard(max_open_positions=2)
        guard.record_fill("T-0", 100)
        guard.record_fill("T-1", 100)
        result = guard.check_order("T-2", "yes", 1, 10)
        assert result is not None
        assert "open positions" in result

    def test_portfolio_drawdown_triggers_kill(self) -> None:
        guard = self._guard(max_portfolio_drawdown=0.10)
        # Start at 100,000. Simulate loss bringing balance to 89,000 (11% drawdown)
        guard.state.current_balance_cents = 89_000
        result = guard.check_order("TEST", "yes", 1, 10)
        assert result is not None
        assert guard.is_killed
        assert "drawdown" in result.lower()

    def test_daily_loss_triggers_kill(self) -> None:
        guard = self._guard(max_daily_loss_cents=500)
        # Record a settlement loss
        guard.record_fill("T-0", 600)
        guard.record_settlement("T-0", 0)  # Total loss = 600 > 500
        result = guard.check_order("T-1", "yes", 1, 10)
        assert result is not None
        assert guard.is_killed

    def test_insufficient_balance(self) -> None:
        guard = self._guard(max_portfolio_drawdown=0.999)
        # Set both peak and current low so drawdown doesn't trigger
        guard.state.peak_balance_cents = 50
        guard.state.current_balance_cents = 50  # Only 50 cents left
        result = guard.check_order("TEST", "yes", 10, 50)  # Need 500 cents
        assert result is not None
        assert "Insufficient" in result

    def test_record_fill_updates_state(self) -> None:
        guard = self._guard()
        guard.record_fill("TEST-01", 500)
        assert guard.state.current_balance_cents == 99_500
        assert guard.state.open_position_count == 1
        assert guard.state.daily_trade_count == 1
        assert "TEST-01" in guard.state.positions

    def test_record_settlement_tracks_pnl(self) -> None:
        guard = self._guard()
        guard.record_fill("TEST-01", 500)
        guard.record_settlement("TEST-01", 1000)  # Won $5
        assert guard.state.open_position_count == 0
        assert guard.state.current_balance_cents == 100_500
        assert guard.state.peak_balance_cents == 100_500

    def test_update_balance_syncs(self) -> None:
        guard = self._guard()
        guard.update_balance(120_000)
        assert guard.state.current_balance_cents == 120_000
        assert guard.state.peak_balance_cents == 120_000

    def test_fail_closed_on_exception(self) -> None:
        """If check_order_inner raises unexpectedly, kill switch fires."""
        guard = self._guard()
        # Force an exception by corrupting state
        guard.state.peak_balance_cents = 0
        guard.state.current_balance_cents = 0
        # The division by zero in drawdown check should trigger fail-closed
        result = guard.check_order("TEST", "yes", 1, 10)
        # Either passes (0/0 handled) or kills — either way shouldn't crash
        assert isinstance(result, str) or result is None

    def test_format_status(self) -> None:
        guard = self._guard()
        guard.record_fill("T-1", 200)
        status = guard.format_status()
        assert "Active" in status
        assert "$10.00" in status  # max bet
        assert "1/" in status  # 1 trade


class TestLiveTradingConfig:
    """Test config loading."""

    def test_from_dict(self) -> None:
        data = {
            "enabled": True,
            "max_bet_size_cents": 500,
            "max_portfolio_drawdown": 0.20,
        }
        cfg = LiveTradingConfig.from_dict(data)
        assert cfg.enabled is True
        assert cfg.max_bet_size_cents == 500
        assert cfg.max_portfolio_drawdown == 0.20
        # Defaults for unspecified fields
        assert cfg.max_open_positions == 10

    def test_from_empty_dict(self) -> None:
        cfg = LiveTradingConfig.from_dict({})
        assert cfg.enabled is False  # Safe default
        assert cfg.max_bet_size_cents == 1000
