"""Tests for the live trader."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.execution.live_trader import LiveTrader
from niche_scanner.execution.risk_guard import LiveTradingConfig, RiskGuard
from niche_scanner.journal.trade_journal import TradeJournal
from niche_scanner.kalshi.models import Order
from niche_scanner.sizing.kelly import PositionSize


def _signal() -> EdgeSignal:
    return EdgeSignal(
        engine="economics",
        ticker="KXRECSSNBER-26",
        side="no",
        model_prob=0.75,
        market_prob=0.71,
        edge_pp=14.0,
        fee_adjusted_edge=12.5,
        confidence=0.7,
        thesis="Model says 25% recession prob vs market 29%",
        timestamp=datetime.now(timezone.utc),
    )


def _size() -> PositionSize:
    return PositionSize(
        ticker="KXRECSSNBER-26",
        side="no",
        contracts=3,
        price_cents=29,
        cost_cents=87,
        kelly_raw=0.06,
        kelly_fraction_used=0.03,
    )


@pytest.fixture
async def journal(tmp_path):
    j = TradeJournal(db_path=str(tmp_path / "live_test.db"))
    await j.initialize()
    yield j
    await j.close()


@pytest.fixture
def guard():
    cfg = LiveTradingConfig(
        enabled=True,
        max_bet_size_cents=1000,
        max_daily_trades=50,
        max_open_positions=10,
    )
    return RiskGuard(cfg, initial_balance_cents=100_000)


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.create_order = AsyncMock(return_value=Order(
        order_id="test-order-001",
        ticker="KXRECSSNBER-26",
        side="no",
        action="buy",
        type="limit",
        no_price=71,
        count=3,
        status="filled",
        created_time="2026-04-04T12:00:00Z",
    ))
    return client


async def test_live_trade_executes_and_records(mock_client, journal, guard):
    trader = LiveTrader(client=mock_client, journal=journal, risk_guard=guard)
    trade_id = await trader.execute(_signal(), _size())

    assert trade_id > 0
    mock_client.create_order.assert_called_once()
    trades = await journal.get_trades()
    assert len(trades) == 1
    assert "LIVE order=test-order-001" in trades[0]["thesis"]


async def test_live_trade_rejected_by_risk_guard(mock_client, journal, guard):
    guard.kill("test kill")
    trader = LiveTrader(client=mock_client, journal=journal, risk_guard=guard)
    trade_id = await trader.execute(_signal(), _size())

    assert trade_id > 0
    mock_client.create_order.assert_not_called()  # No order placed
    trades = await journal.get_trades()
    assert "REJECTED" in trades[0]["thesis"]


async def test_live_trade_bet_size_rejected(mock_client, journal):
    cfg = LiveTradingConfig(enabled=True, max_bet_size_cents=50)  # $0.50 max
    guard = RiskGuard(cfg, initial_balance_cents=100_000)
    trader = LiveTrader(client=mock_client, journal=journal, risk_guard=guard)

    await trader.execute(_signal(), _size())  # 87 cents > 50
    mock_client.create_order.assert_not_called()
    trades = await journal.get_trades()
    assert "REJECTED" in trades[0]["thesis"]
    assert "Bet size" in trades[0]["thesis"]


async def test_live_trade_api_failure_triggers_kill(mock_client, journal, guard):
    mock_client.create_order = AsyncMock(side_effect=RuntimeError("API down"))
    trader = LiveTrader(client=mock_client, journal=journal, risk_guard=guard)

    await trader.execute(_signal(), _size())
    assert guard.is_killed
    assert "Order placement failed" in guard.kill_reason
    trades = await journal.get_trades()
    assert "FAILED" in trades[0]["thesis"]


async def test_summary_tracks_counts(mock_client, journal, guard):
    trader = LiveTrader(client=mock_client, journal=journal, risk_guard=guard)
    await trader.execute(_signal(), _size())
    summary = trader.summary()
    assert summary["total_signals"] == 1
    assert summary["executed"] == 1
    assert summary["rejected"] == 0
    assert summary["risk_killed"] is False
