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


# ---------------------------------------------------------------------------
# connection property
# ---------------------------------------------------------------------------


async def test_connection_property_returns_conn(journal: TradeJournal) -> None:
    """The connection property returns the aiosqlite.Connection after init."""
    conn = journal.connection
    assert conn is not None
    # Verify it is a usable aiosqlite connection
    cursor = await conn.execute("SELECT 1")
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_connection_property_before_init() -> None:
    """Accessing connection before initialize() raises RuntimeError."""
    tj = TradeJournal("nonexistent.db")
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = tj.connection


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


# ---------------------------------------------------------------------------
# get_trade_by_id
# ---------------------------------------------------------------------------


async def test_get_trade_by_id_found(journal: TradeJournal) -> None:
    """Retrieve a trade by ID and verify all fields are present."""
    trade_id = await journal.record_trade(_make_record(ticker="KXHIGHNY-30APR4-T50"))
    result = await journal.get_trade_by_id(trade_id)

    assert result is not None
    assert result["id"] == trade_id
    assert result["ticker"] == "KXHIGHNY-30APR4-T50"
    assert result["engine"] == "weather"
    assert result["side"] == "yes"
    assert result["action"] == "buy"
    assert result["contracts"] == 10
    assert result["price_cents"] == 35
    # cost_cents is a generated column: side='yes' -> contracts * price_cents
    assert result["cost_cents"] == 10 * 35
    assert result["outcome"] is None  # not yet resolved


async def test_get_trade_by_id_not_found(journal: TradeJournal) -> None:
    """Return None when the trade ID does not exist."""
    result = await journal.get_trade_by_id(99999)
    assert result is None


# ---------------------------------------------------------------------------
# get_daily_stats
# ---------------------------------------------------------------------------


async def test_get_daily_stats_empty(journal: TradeJournal) -> None:
    """No resolved trades should yield an empty list."""
    # Insert an unresolved trade to prove it is excluded
    await journal.record_trade(_make_record())
    days = await journal.get_daily_stats()
    assert days == []


async def test_get_daily_stats_single_day(journal: TradeJournal) -> None:
    """All resolved trades on the same day should group into one row."""
    ids: list[int] = []
    for i in range(3):
        tid = await journal.record_trade(
            _make_record(ticker=f"DAY-{i}", price_cents=40)
        )
        ids.append(tid)

    # 2 wins (payout 100 each) and 1 loss (payout 0)
    await journal.record_outcome(ids[0], payout_cents=100)
    await journal.record_outcome(ids[1], payout_cents=100)
    await journal.record_outcome(ids[2], payout_cents=0)

    days = await journal.get_daily_stats()
    assert len(days) == 1

    day = days[0]
    assert day["trades"] == 3
    assert day["wins"] == 2
    assert day["losses"] == 1
    # cost_cents for side='yes', price=40, contracts=10 -> 400 each
    # net_pnl = sum(payout - cost) = (100-400) + (100-400) + (0-400) = -1000
    assert day["net_pnl_cents"] == (100 - 400) + (100 - 400) + (0 - 400)
    assert day["date"] is not None


async def test_get_daily_stats_multiple_days(journal: TradeJournal) -> None:
    """Trades inserted with different created_at dates produce multiple rows."""
    conn = journal.connection

    # Insert trades with explicit dates to simulate multiple days
    for date_str, payout in [
        ("2026-04-01 10:00:00", 100),
        ("2026-04-01 14:00:00", 0),
        ("2026-04-02 09:00:00", 100),
    ]:
        cursor = await conn.execute(
            """
            INSERT INTO trades
                (ticker, engine, side, action, contracts, price_cents,
                 model_prob, market_prob, edge_pp, kelly_fraction, thesis,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TICKER-X", "weather", "yes", "buy", 10, 50,
                0.6, 0.5, 10.0, 0.2, "test",
                date_str,
            ),
        )
        trade_id = cursor.lastrowid
        assert trade_id is not None
        await conn.execute(
            """
            UPDATE trades
               SET payout_cents = ?,
                   outcome      = CASE WHEN ? > 0 THEN 'win' ELSE 'loss' END,
                   resolved_at  = datetime('now')
             WHERE id = ?
            """,
            (payout, payout, trade_id),
        )
    await conn.commit()

    days = await journal.get_daily_stats()
    assert len(days) == 2
    # Ordered by date ASC
    assert days[0]["date"] == "2026-04-01"
    assert days[0]["trades"] == 2
    assert days[0]["wins"] == 1
    assert days[0]["losses"] == 1
    assert days[1]["date"] == "2026-04-02"
    assert days[1]["trades"] == 1
    assert days[1]["wins"] == 1
    assert days[1]["losses"] == 0
