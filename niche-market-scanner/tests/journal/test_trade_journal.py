"""Tests for the trade journal."""

from __future__ import annotations

import pytest

from niche_scanner.journal.trade_journal import TradeJournal, TradeRecord


@pytest.fixture
async def journal(tmp_path):
    """Create a temporary TradeJournal, initialize it, and tear down after."""
    db_path = str(tmp_path / "test_trades.db")
    tj = TradeJournal(db_path)
    await tj.initialize()
    yield tj
    await tj.close()


def _make_record(
    ticker: str = "KXHIGHNY-26APR4-T50",
    engine: str = "weather",
    side: str = "yes",
    action: str = "buy",
    contracts: int = 10,
    price_cents: int = 35,
    model_prob: float = 0.55,
    market_prob: float = 0.35,
    edge_pp: float = 20.0,
    kelly_fraction: float = 0.30,
    thesis: str = "NWS forecast above threshold",
) -> TradeRecord:
    return TradeRecord(
        ticker=ticker,
        engine=engine,
        side=side,
        action=action,
        contracts=contracts,
        price_cents=price_cents,
        model_prob=model_prob,
        market_prob=market_prob,
        edge_pp=edge_pp,
        kelly_fraction=kelly_fraction,
        thesis=thesis,
    )


async def test_record_trade(journal: TradeJournal) -> None:
    """Record a trade and verify the returned ID is positive."""
    trade_id = await journal.record_trade(_make_record())
    assert trade_id > 0


async def test_get_trades_by_engine(journal: TradeJournal) -> None:
    """Record 3 trades (2 weather, 1 economics) and filter by engine."""
    await journal.record_trade(_make_record(engine="weather"))
    await journal.record_trade(_make_record(engine="weather", ticker="KXHIGHNY-27APR4-T55"))
    await journal.record_trade(_make_record(engine="economics", ticker="CPI-DEC-2026"))

    weather_trades = await journal.get_trades(engine="weather")
    econ_trades = await journal.get_trades(engine="economics")

    assert len(weather_trades) == 2
    assert len(econ_trades) == 1
    assert econ_trades[0]["ticker"] == "CPI-DEC-2026"


async def test_win_rate(journal: TradeJournal) -> None:
    """Record 4 trades, mark 3 as wins and 1 as loss, check win_rate."""
    ids: list[int] = []
    for i in range(4):
        tid = await journal.record_trade(
            _make_record(ticker=f"TICKER-{i}", price_cents=40)
        )
        ids.append(tid)

    # 3 wins (payout > 0), 1 loss (payout == 0)
    await journal.record_outcome(ids[0], payout_cents=100)
    await journal.record_outcome(ids[1], payout_cents=100)
    await journal.record_outcome(ids[2], payout_cents=100)
    await journal.record_outcome(ids[3], payout_cents=0)

    stats = await journal.get_stats()
    assert stats["total_trades"] == 4
    assert stats["wins"] == 3
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(0.75)
