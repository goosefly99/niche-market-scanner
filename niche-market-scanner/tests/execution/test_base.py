"""Tests for the shared Trader protocol."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.execution.base import Trader
from niche_scanner.execution.live_trader import LiveTrader
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.execution.risk_guard import LiveTradingConfig, RiskGuard
from niche_scanner.journal.trade_journal import TradeJournal
from niche_scanner.kalshi.models import Order
from niche_scanner.sizing.kelly import PositionSize


def _signal() -> EdgeSignal:
    return EdgeSignal(
        engine="economics",
        ticker="KXCPI-26",
        side="yes",
        model_prob=0.62,
        market_prob=0.50,
        edge_pp=12.0,
        fee_adjusted_edge=10.0,
        confidence=0.7,
        thesis="Trader-protocol test signal",
        timestamp=datetime.now(timezone.utc),
    )


def _size() -> PositionSize:
    return PositionSize(
        ticker="KXCPI-26",
        side="yes",
        contracts=4,
        price_cents=50,
        cost_cents=200,
        kelly_raw=0.08,
        kelly_fraction_used=0.04,
    )


@pytest.fixture
async def journal(tmp_path):
    j = TradeJournal(db_path=str(tmp_path / "trader_protocol.db"))
    await j.initialize()
    yield j
    await j.close()


def test_paper_trader_satisfies_trader_protocol(journal: TradeJournal) -> None:
    """PaperTrader must be a structural match for Trader at runtime."""
    trader = PaperTrader(journal)
    assert isinstance(trader, Trader)


def test_live_trader_satisfies_trader_protocol(journal: TradeJournal) -> None:
    """LiveTrader must be a structural match for Trader at runtime."""
    client = AsyncMock()
    guard = RiskGuard(
        LiveTradingConfig(enabled=True, max_bet_size_cents=10_000),
        initial_balance_cents=100_000,
    )
    trader = LiveTrader(client=client, journal=journal, risk_guard=guard)
    assert isinstance(trader, Trader)


def test_paper_trader_total_cost_starts_at_zero(journal: TradeJournal) -> None:
    trader = PaperTrader(journal)
    assert trader.total_cost_cents == 0


def test_live_trader_total_cost_starts_at_zero(journal: TradeJournal) -> None:
    client = AsyncMock()
    guard = RiskGuard(
        LiveTradingConfig(enabled=True, max_bet_size_cents=10_000),
        initial_balance_cents=100_000,
    )
    trader = LiveTrader(client=client, journal=journal, risk_guard=guard)
    assert trader.total_cost_cents == 0


async def test_paper_trader_total_cost_accumulates(journal: TradeJournal) -> None:
    """After execute(), total_cost_cents reflects cumulative cost."""
    trader = PaperTrader(journal)
    await trader.execute(_signal(), _size())
    assert trader.total_cost_cents == 200

    await trader.execute(_signal(), _size())
    assert trader.total_cost_cents == 400


async def test_live_trader_total_cost_only_counts_filled(
    journal: TradeJournal,
) -> None:
    """Only successfully filled live orders contribute to total_cost_cents."""
    client = AsyncMock()
    client.create_order = AsyncMock(
        return_value=Order(
            order_id="filled-1",
            ticker="KXCPI-26",
            side="yes",
            action="buy",
            type="limit",
            yes_price=50,
            count=4,
            status="filled",
            created_time="2026-04-04T12:00:00Z",
        ),
    )
    guard = RiskGuard(
        LiveTradingConfig(enabled=True, max_bet_size_cents=10_000),
        initial_balance_cents=100_000,
    )
    trader = LiveTrader(client=client, journal=journal, risk_guard=guard)

    await trader.execute(_signal(), _size())
    assert trader.total_cost_cents == 200

    # Kill the risk guard — next order is rejected and must NOT add cost.
    guard.kill("test kill")
    await trader.execute(_signal(), _size())
    assert trader.total_cost_cents == 200


def test_trader_protocol_rejects_unrelated_object() -> None:
    """Unrelated objects must fail the Trader isinstance check."""
    assert not isinstance(object(), Trader)
    assert not isinstance("not a trader", Trader)
