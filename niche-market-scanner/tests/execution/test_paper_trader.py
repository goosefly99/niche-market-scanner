"""Tests for the paper trader."""

from __future__ import annotations

import pytest

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.journal.trade_journal import TradeJournal
from niche_scanner.sizing.kelly import PositionSize


def _signal() -> EdgeSignal:
    """Return a representative EdgeSignal for testing."""
    return EdgeSignal(
        engine="economics",
        ticker="CPI-MAR-2026-T35",
        side="yes",
        model_prob=0.65,
        market_prob=0.50,
        edge_pp=15.0,
        fee_adjusted_edge=12.5,
        confidence=0.7,
        thesis="Cleveland Nowcast above threshold",
    )


def _size() -> PositionSize:
    """Return a representative PositionSize for testing."""
    return PositionSize(
        ticker="CPI-MAR-2026-T35",
        side="yes",
        contracts=5,
        price_cents=50,
        cost_cents=250,
        kelly_raw=0.10,
        kelly_fraction_used=0.05,
    )


@pytest.fixture
async def journal(tmp_path):
    """Create a temporary TradeJournal."""
    db_path = str(tmp_path / "paper_trades.db")
    tj = TradeJournal(db_path)
    await tj.initialize()
    yield tj
    await tj.close()


@pytest.fixture
async def trader(journal: TradeJournal):
    """Create a PaperTrader backed by the temporary journal."""
    return PaperTrader(journal)


async def test_paper_trade_records_to_journal(
    trader: PaperTrader,
    journal: TradeJournal,
) -> None:
    """Execute a paper trade and verify it appears in the journal."""
    trade_id = await trader.execute(_signal(), _size())
    assert trade_id > 0

    trades = await journal.get_trades()
    assert len(trades) == 1
    assert trades[0]["ticker"] == "CPI-MAR-2026-T35"
    assert trades[0]["engine"] == "economics"
    assert trades[0]["side"] == "yes"
    assert trades[0]["contracts"] == 5


async def test_paper_trade_tracks_pnl(trader: PaperTrader) -> None:
    """Execute a paper trade and verify summary counters update."""
    assert trader.summary() == {"total_signals": 0, "total_cost_cents": 0}

    await trader.execute(_signal(), _size())
    summary = trader.summary()
    assert summary["total_signals"] == 1
    assert summary["total_cost_cents"] == 250
