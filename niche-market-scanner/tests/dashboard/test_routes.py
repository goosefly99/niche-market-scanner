"""Tests for REST API endpoints in dashboard/routes.py.

Uses httpx.AsyncClient with ASGITransport to make real HTTP requests
against the FastAPI app backed by an in-memory SQLite database and
lightweight mocks for RiskGuard, HealthMonitor, and ScannerSettings.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from niche_scanner.dashboard.server import create_app
from niche_scanner.execution.risk_guard import LiveTradingConfig, RiskGuard
from niche_scanner.journal.db import SCHEMA
from niche_scanner.monitor.health import HealthMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_conn():
    """Create an in-memory SQLite database with the trades schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture()
def risk_guard() -> RiskGuard:
    """Create a RiskGuard with known state for assertion."""
    config = LiveTradingConfig(
        enabled=True,
        starting_cash_cents=100_000,
        max_bet_size_cents=1_000,
        max_buy_usd_cum_cents=10_000,
        max_portfolio_drawdown=0.15,
        max_daily_loss_cents=5_000,
        position_stop_loss=0.50,
        max_open_positions=10,
        max_daily_trades=50,
    )
    guard = RiskGuard(config, initial_balance_cents=100_000)
    return guard


@pytest.fixture()
def health_monitor() -> HealthMonitor:
    monitor = HealthMonitor()
    monitor.heartbeat("scanner")
    monitor.scan_loop_heartbeat()
    return monitor


@pytest.fixture()
def settings() -> MagicMock:
    """Mock ScannerSettings with realistic config dicts."""
    s = MagicMock()
    s.scanner = {
        "weather_interval_min": 5,
        "economics_interval_min": 10,
    }
    s.sizing = {
        "paper_trade": False,
        "kelly_fraction": 0.5,
    }
    s.verticals = {
        "weather": {"enabled": True},
        "economics": {"enabled": True},
        "thin_market": {"enabled": False},
    }
    return s


@pytest.fixture()
async def journal_mock(db_conn):
    """A lightweight mock that exposes the real aiosqlite connection.

    The mock satisfies ``journal._ensure_conn()`` and ``journal.get_stats()``.
    """
    journal = MagicMock()
    # Expose as property-like attribute for routes.py (journal.connection)
    journal.connection = db_conn
    # Keep legacy mock for any remaining _ensure_conn callers
    journal._ensure_conn.return_value = db_conn

    # Wire get_stats to the real DB (same logic as TradeJournal.get_stats)
    async def _get_stats(engine=None):
        db_conn.row_factory = None
        if engine is not None:
            cursor = await db_conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(outcome = 'win'), 0),
                       COALESCE(SUM(outcome = 'loss'), 0),
                       COALESCE(AVG(edge_pp), 0.0),
                       COALESCE(SUM(payout_cents - cost_cents), 0)
                FROM trades WHERE outcome IS NOT NULL AND engine = ?
                """,
                (engine,),
            )
        else:
            cursor = await db_conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(outcome = 'win'), 0),
                       COALESCE(SUM(outcome = 'loss'), 0),
                       COALESCE(AVG(edge_pp), 0.0),
                       COALESCE(SUM(payout_cents - cost_cents), 0)
                FROM trades WHERE outcome IS NOT NULL
                """,
            )
        row = await cursor.fetchone()
        total, wins, losses, avg_edge, net_pnl = row
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total > 0 else 0.0,
            "avg_edge_pp": avg_edge,
            "net_pnl_cents": net_pnl,
        }

    journal.get_stats = _get_stats

    # Wire query_trades to the real DB (same logic as TradeJournal.query_trades)
    async def _query_trades(
        *,
        engine=None,
        action=None,
        outcome=None,
        limit=50,
        offset=0,
    ):
        db_conn.row_factory = aiosqlite.Row

        clauses: list[str] = []
        params: list = []
        if engine is not None:
            clauses.append("engine = ?")
            params.append(engine)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if outcome is not None:
            if outcome == "pending":
                clauses.append("outcome IS NULL")
            else:
                clauses.append("outcome = ?")
                params.append(outcome)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        count_cursor = await db_conn.execute(
            f"SELECT COUNT(*) FROM trades{where}", params,
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0

        cursor = await db_conn.execute(
            f"SELECT * FROM trades{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = await cursor.fetchall()
        trades = [dict(row) for row in rows]
        return trades, total

    journal.query_trades = _query_trades
    return journal


@pytest.fixture()
async def client(journal_mock, risk_guard, health_monitor, settings):
    """AsyncClient wired to the FastAPI app with real-ish backends."""
    scanner = MagicMock()
    alert_manager = MagicMock()

    app = create_app(
        risk_guard=risk_guard,
        health_monitor=health_monitor,
        journal=journal_mock,
        settings=settings,
        scanner=scanner,
        alert_manager=alert_manager,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _insert_trade(
    conn: aiosqlite.Connection,
    *,
    ticker: str = "KXHIGHNY-26APR04-T45",
    engine: str = "weather",
    side: str = "yes",
    action: str = "buy",
    contracts: int = 5,
    price_cents: int = 60,
    model_prob: float = 0.72,
    market_prob: float = 0.60,
    edge_pp: float = 12.0,
    kelly_fraction: float = 0.08,
    thesis: str = "test trade",
    payout_cents: int | None = None,
    outcome: str | None = None,
) -> int:
    """Insert a trade and optionally resolve it."""
    cursor = await conn.execute(
        """
        INSERT INTO trades
            (ticker, engine, side, action, contracts, price_cents,
             model_prob, market_prob, edge_pp, kelly_fraction, thesis)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker, engine, side, action, contracts, price_cents,
         model_prob, market_prob, edge_pp, kelly_fraction, thesis),
    )
    trade_id = cursor.lastrowid
    if payout_cents is not None and outcome is not None:
        await conn.execute(
            """
            UPDATE trades
               SET payout_cents = ?, outcome = ?, resolved_at = datetime('now')
             WHERE id = ?
            """,
            (payout_cents, outcome, trade_id),
        )
    await conn.commit()
    return trade_id


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------


class TestPing:
    async def test_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class TestGetTrades:
    async def test_empty_db(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trades")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trades"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_returns_inserted_trades(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn)
        resp = await client.get("/api/trades")
        body = resp.json()
        assert body["total"] == 1
        assert len(body["trades"]) == 1
        assert body["trades"][0]["ticker"] == "KXHIGHNY-26APR04-T45"

    async def test_filter_by_engine(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn, engine="weather")
        await _insert_trade(db_conn, engine="economics", ticker="KXECON-1")
        resp = await client.get("/api/trades?engine=economics")
        body = resp.json()
        assert body["total"] == 1
        assert body["trades"][0]["engine"] == "economics"

    async def test_filter_by_action(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn, action="buy")
        await _insert_trade(db_conn, action="rejected", ticker="KXREJ-1")
        resp = await client.get("/api/trades?action=rejected")
        body = resp.json()
        assert body["total"] == 1
        assert body["trades"][0]["action"] == "rejected"

    async def test_filter_by_outcome_pending(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn)  # no outcome = pending
        await _insert_trade(
            db_conn, ticker="KXWIN-1",
            payout_cents=500, outcome="win",
        )
        resp = await client.get("/api/trades?outcome=pending")
        body = resp.json()
        assert body["total"] == 1
        assert body["trades"][0]["outcome"] is None

    async def test_filter_by_outcome_win(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn)  # pending
        await _insert_trade(
            db_conn, ticker="KXWIN-1",
            payout_cents=500, outcome="win",
        )
        resp = await client.get("/api/trades?outcome=win")
        body = resp.json()
        assert body["total"] == 1
        assert body["trades"][0]["outcome"] == "win"

    async def test_pagination_limit_offset(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        for i in range(5):
            await _insert_trade(db_conn, ticker=f"TICK-{i}")
        resp = await client.get("/api/trades?limit=2&offset=2")
        body = resp.json()
        assert body["total"] == 5
        assert len(body["trades"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 2

    async def test_combined_filters(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn, engine="weather", action="buy")
        await _insert_trade(
            db_conn, engine="weather", action="buy", ticker="KXWIN-1",
            payout_cents=500, outcome="win",
        )
        await _insert_trade(db_conn, engine="economics", action="buy", ticker="KXECON-1")
        resp = await client.get("/api/trades?engine=weather&outcome=win")
        body = resp.json()
        assert body["total"] == 1
        assert body["trades"][0]["ticker"] == "KXWIN-1"


class TestGetTradeById:
    async def test_found(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        trade_id = await _insert_trade(db_conn)
        resp = await client.get(f"/api/trades/{trade_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trade"]["id"] == trade_id

    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trades/9999")
        assert resp.status_code == 404
        assert resp.json()["error"] == "Trade not found"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestGetStats:
    async def test_empty_db(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_trades"] == 0
        assert body["win_rate"] == 0.0

    async def test_with_resolved_trades(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(
            db_conn, payout_cents=500, outcome="win",
        )
        await _insert_trade(
            db_conn, ticker="KXLOSS-1", payout_cents=0, outcome="loss",
        )
        resp = await client.get("/api/stats")
        body = resp.json()
        assert body["total_trades"] == 2
        assert body["wins"] == 1
        assert body["losses"] == 1
        assert body["win_rate"] == 0.5

    async def test_filter_by_engine(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(
            db_conn, engine="weather", payout_cents=500, outcome="win",
        )
        await _insert_trade(
            db_conn, engine="economics", ticker="KXECON-1",
            payout_cents=0, outcome="loss",
        )
        resp = await client.get("/api/stats?engine=weather")
        body = resp.json()
        assert body["total_trades"] == 1
        assert body["wins"] == 1


class TestGetDailyStats:
    async def test_empty_db(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats/daily")
        assert resp.status_code == 200
        assert resp.json()["days"] == []

    async def test_with_resolved_trades(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(
            db_conn, payout_cents=500, outcome="win",
        )
        resp = await client.get("/api/stats/daily")
        body = resp.json()
        assert len(body["days"]) == 1
        assert body["days"][0]["trade_count"] == 1


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class TestGetRiskState:
    async def test_returns_state(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_balance_cents"] == 100_000
        assert body["peak_balance_cents"] == 100_000
        assert body["killed"] is False
        assert body["positions"] == {}

    async def test_reflects_kill(
        self, client: AsyncClient, risk_guard: RiskGuard,
    ) -> None:
        risk_guard.kill("test kill")
        resp = await client.get("/api/risk/state")
        body = resp.json()
        assert body["killed"] is True
        assert body["kill_reason"] == "test kill"


class TestGetRiskConfig:
    async def test_returns_config(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["max_bet_size_cents"] == 1_000
        assert body["max_portfolio_drawdown"] == 0.15


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestGetHealth:
    async def test_returns_report(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scan_loop_alive"] is True
        assert "scanner" in body["components"]
        assert body["components"]["scanner"]["healthy"] is True

    async def test_unhealthy_component(
        self, client: AsyncClient, health_monitor: HealthMonitor,
    ) -> None:
        health_monitor.report_error("scanner", "API timeout")
        resp = await client.get("/api/health")
        body = resp.json()
        assert body["components"]["scanner"]["healthy"] is False
        assert body["components"]["scanner"]["last_error"] == "API timeout"
        assert len(body["errors"]) >= 1


# ---------------------------------------------------------------------------
# Scanner status
# ---------------------------------------------------------------------------


class TestGetScannerStatus:
    async def test_returns_status(self, client: AsyncClient) -> None:
        resp = await client.get("/api/scanner/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "live"
        assert "weather" in body["engines"]
        assert "economics" in body["engines"]
        assert "thin_market" not in body["engines"]
        assert body["killed"] is False


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


class TestGetBalance:
    async def test_returns_balance(self, client: AsyncClient) -> None:
        resp = await client.get("/api/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["balance_cents"] == 100_000
        assert body["peak_cents"] == 100_000
        assert body["drawdown_pct"] == 0.0

    async def test_reflects_drawdown(
        self, client: AsyncClient, risk_guard: RiskGuard,
    ) -> None:
        risk_guard.state.current_balance_cents = 90_000
        resp = await client.get("/api/balance")
        body = resp.json()
        assert body["balance_cents"] == 90_000
        assert body["drawdown_pct"] == pytest.approx(0.1, abs=1e-4)


class TestGetBalanceHistory:
    async def test_returns_empty_when_no_snapshots(self, client: AsyncClient) -> None:
        resp = await client.get("/api/balance/history")
        assert resp.status_code == 200
        assert resp.json()["snapshots"] == []

    async def test_returns_recorded_snapshots(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await db_conn.execute(
            """
            INSERT INTO balance_history
                (balance_cents, peak_cents, drawdown_pct, cumulative_spend_cents)
            VALUES (?, ?, ?, ?)
            """,
            (95_000, 100_000, 5.0, 5_000),
        )
        await db_conn.commit()

        resp = await client.get("/api/balance/history")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["snapshots"]) == 1
        assert body["snapshots"][0]["balance_cents"] == 95_000
        assert body["snapshots"][0]["peak_cents"] == 100_000

    async def test_respects_limit(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        for i in range(5):
            await db_conn.execute(
                """
                INSERT INTO balance_history
                    (balance_cents, peak_cents, drawdown_pct, cumulative_spend_cents)
                VALUES (?, ?, ?, ?)
                """,
                (100_000 - i * 1_000, 100_000, float(i), i * 1_000),
            )
        await db_conn.commit()

        resp = await client.get("/api/balance/history?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["snapshots"]) == 2


# ---------------------------------------------------------------------------
# Scan cycles
# ---------------------------------------------------------------------------


class TestGetScanCycles:
    async def test_returns_empty_when_no_cycles(self, client: AsyncClient) -> None:
        resp = await client.get("/api/scan-cycles")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cycles"] == []
        assert body["total"] == 0

    async def test_returns_recorded_cycles(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await db_conn.execute(
            """
            INSERT INTO scan_cycles
                (markets_scanned, signals_found, executed, skipped,
                 no_exposure_cents, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (200, 3, 2, 1, 500, 1234),
        )
        await db_conn.commit()

        resp = await client.get("/api/scan-cycles")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["cycles"]) == 1
        assert body["cycles"][0]["markets_scanned"] == 200
        assert body["cycles"][0]["signals_found"] == 3
        assert body["cycles"][0]["duration_ms"] == 1234

    async def test_respects_limit(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        for i in range(5):
            await db_conn.execute(
                """
                INSERT INTO scan_cycles
                    (markets_scanned, signals_found, executed, skipped,
                     no_exposure_cents, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (100 + i, i, 0, i, 0, 500 + i),
            )
        await db_conn.commit()

        resp = await client.get("/api/scan-cycles?limit=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["cycles"]) == 3


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


class TestGetRecentSignals:
    async def test_returns_empty_when_buffer_empty(
        self, client: AsyncClient,
    ) -> None:
        """Empty buffer returns an empty list."""
        resp = await client.get("/api/signals/recent")
        assert resp.status_code == 200
        body = resp.json()
        assert body["signals"] == []
        assert body["count"] == 0

    async def test_returns_buffered_signals_newest_first(
        self, client: AsyncClient,
    ) -> None:
        """Signals placed in the buffer are returned newest first."""
        from datetime import datetime, timezone

        from niche_scanner.engines.base import EdgeSignal

        buffer = client._transport.app.state.signal_buffer
        buffer.clear()
        for ticker in ["A", "B", "C"]:
            buffer.append(EdgeSignal(
                engine="weather",
                ticker=ticker,
                side="yes",
                model_prob=0.7,
                market_prob=0.6,
                edge_pp=10.0,
                fee_adjusted_edge=9.5,
                confidence=0.8,
                thesis=f"test {ticker}",
                metadata={"kelly_fraction": 0.05},
                timestamp=datetime(
                    2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc,
                ),
            ))

        resp = await client.get("/api/signals/recent")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        tickers = [s["ticker"] for s in body["signals"]]
        assert tickers == ["C", "B", "A"]
        # Each signal has expected fields
        first = body["signals"][0]
        assert first["engine"] == "weather"
        assert first["side"] == "yes"
        assert first["model_prob"] == pytest.approx(0.7)
        assert first["edge_pp"] == pytest.approx(10.0)
        assert first["thesis"] == "test C"
        assert first["metadata"] == {"kelly_fraction": 0.05}
        assert first["timestamp"] == "2026-04-04T12:00:00+00:00"

    async def test_respects_limit_parameter(
        self, client: AsyncClient,
    ) -> None:
        """The limit query parameter caps the returned count."""
        from niche_scanner.engines.base import EdgeSignal

        buffer = client._transport.app.state.signal_buffer
        buffer.clear()
        for i in range(10):
            buffer.append(EdgeSignal(
                engine="economics",
                ticker=f"T{i}",
                side="no",
                model_prob=0.4,
                market_prob=0.5,
                edge_pp=8.0,
                fee_adjusted_edge=7.5,
                confidence=0.7,
                thesis=f"test {i}",
            ))

        resp = await client.get("/api/signals/recent?limit=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        # newest first: T9, T8, T7
        tickers = [s["ticker"] for s in body["signals"]]
        assert tickers == ["T9", "T8", "T7"]

    async def test_limit_validation_rejects_zero(
        self, client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/signals/recent?limit=0")
        assert resp.status_code == 422

    async def test_limit_validation_rejects_too_large(
        self, client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/signals/recent?limit=1000")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Paper mode — risk_guard=None
# ---------------------------------------------------------------------------


@pytest.fixture()
def paper_settings() -> MagicMock:
    """Mock ScannerSettings configured for paper mode."""
    s = MagicMock()
    s.scanner = {
        "weather_interval_min": 5,
        "economics_interval_min": 10,
    }
    s.sizing = {
        "paper_trade": True,
        "kelly_fraction": 0.5,
    }
    s.verticals = {
        "weather": {"enabled": True},
        "economics": {"enabled": True},
        "thin_market": {"enabled": False},
    }
    return s


@pytest.fixture()
async def paper_client(journal_mock, health_monitor, paper_settings):
    """AsyncClient with risk_guard=None (paper mode)."""
    scanner = MagicMock()
    alert_manager = MagicMock()

    app = create_app(
        risk_guard=None,
        health_monitor=health_monitor,
        journal=journal_mock,
        settings=paper_settings,
        scanner=scanner,
        alert_manager=alert_manager,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestPaperModeRiskState:
    """risk_guard=None must not crash any endpoint."""

    async def test_risk_state_returns_zeroed_dict(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/risk/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["killed"] is False
        assert body["current_balance_cents"] == 0
        assert body["peak_balance_cents"] == 0
        assert body["positions"] == {}

    async def test_risk_config_returns_empty_dict(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/risk/config")
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_scanner_status_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/scanner/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "paper"
        assert body["killed"] is False

    async def test_balance_returns_zeroed_dict(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["balance_cents"] == 0
        assert body["peak_cents"] == 0
        assert body["drawdown_pct"] == 0.0

    async def test_balance_history_works_in_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/balance/history")
        assert resp.status_code == 200
        assert resp.json()["snapshots"] == []

    async def test_health_works_in_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scan_loop_alive"] is True

    async def test_trades_works_in_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_signals_works_in_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/signals/recent")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_ping_works_in_paper_mode(
        self, paper_client: AsyncClient,
    ) -> None:
        resp = await paper_client.get("/api/ping")
        assert resp.status_code == 200
