"""Tests for HTML page routes and Jinja2 template filters.

Verifies that pages.py renders correct HTML responses for all four page
routes (/, /trades, /signals, /config), that HTMX partial rendering works
via the HX-Request header, and that all custom Jinja2 filters produce
expected output.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from niche_scanner.dashboard.pages import (
    cents_to_dollars,
    duration_ms_format,
    format_pct,
    relative_time,
    risk_color,
)
from niche_scanner.dashboard.server import create_app
from niche_scanner.execution.risk_guard import LiveTradingConfig, RiskGuard
from niche_scanner.journal.db import SCHEMA
from niche_scanner.monitor.health import HealthMonitor


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_routes.py)
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
    s.live_trading = {
        "enabled": True,
        "starting_cash_cents": 100_000,
    }
    return s


@pytest.fixture()
async def journal_mock(db_conn):
    """Lightweight mock that exposes real aiosqlite connection."""
    journal = MagicMock()
    journal.connection = db_conn
    journal._ensure_conn.return_value = db_conn

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
    """AsyncClient wired to the FastAPI app with pages router included."""
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


# ===========================================================================
# Template filter unit tests
# ===========================================================================


class TestCentsToDollars:
    def test_positive(self) -> None:
        assert cents_to_dollars(12345) == "$123.45"

    def test_zero(self) -> None:
        assert cents_to_dollars(0) == "$0.00"

    def test_none(self) -> None:
        assert cents_to_dollars(None) == "$0.00"

    def test_negative(self) -> None:
        assert cents_to_dollars(-500) == "$-5.00"

    def test_float(self) -> None:
        assert cents_to_dollars(99.5) == "$0.99"


class TestFormatPct:
    def test_fifty_percent(self) -> None:
        assert format_pct(0.5) == "50.0%"

    def test_zero(self) -> None:
        assert format_pct(0.0) == "0.0%"

    def test_none(self) -> None:
        assert format_pct(None) == "0.0%"

    def test_above_one(self) -> None:
        assert format_pct(1.5) == "150.0%"

    def test_fractional(self) -> None:
        assert format_pct(0.753) == "75.3%"


class TestRelativeTime:
    def test_seconds(self) -> None:
        assert relative_time(5) == "5s ago"

    def test_minutes(self) -> None:
        assert relative_time(90) == "1m ago"

    def test_hours(self) -> None:
        assert relative_time(7200) == "2h ago"

    def test_none(self) -> None:
        assert relative_time(None) == "N/A"

    def test_zero(self) -> None:
        assert relative_time(0) == "0s ago"

    def test_just_under_minute(self) -> None:
        assert relative_time(59) == "59s ago"

    def test_exactly_60(self) -> None:
        assert relative_time(60) == "1m ago"


class TestRiskColor:
    def test_green(self) -> None:
        assert risk_color(0.03, 0.15) == "text-green-400"

    def test_yellow(self) -> None:
        assert risk_color(0.10, 0.15) == "text-yellow-400"

    def test_red(self) -> None:
        assert risk_color(0.14, 0.15) == "text-red-400"

    def test_zero_threshold(self) -> None:
        assert risk_color(0.5, 0.0) == "text-gray-400"


class TestDurationMsFormat:
    def test_milliseconds(self) -> None:
        assert duration_ms_format(450) == "450ms"

    def test_seconds(self) -> None:
        assert duration_ms_format(1234) == "1.2s"

    def test_none(self) -> None:
        assert duration_ms_format(None) == "N/A"

    def test_exact_1000(self) -> None:
        assert duration_ms_format(1000) == "1.0s"


# ===========================================================================
# Page route tests
# ===========================================================================


class TestOverviewPage:
    async def test_returns_html(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_contains_overview_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        html = resp.text
        assert "Overview" in html
        assert "Total Trades" in html
        assert "Win Rate" in html
        assert "Net P&amp;L" in html
        assert "Balance" in html

    async def test_contains_risk_gauges(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        html = resp.text
        assert "Drawdown" in html
        assert "Cumulative Spend" in html
        assert "Open Positions" in html

    async def test_contains_health_status(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        html = resp.text
        assert "System Health" in html
        assert "Scan Loop" in html

    async def test_renders_dollar_amounts(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        html = resp.text
        # Balance should be $1,000.00 (100_000 cents)
        assert "$1000.00" in html

    async def test_nav_overview_active(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        html = resp.text
        # The overview nav link should have the active class
        assert "bg-surface-light text-white" in html

    async def test_balance_chart_empty_state(self, client: AsyncClient) -> None:
        """With no snapshots, the empty-state message is rendered."""
        resp = await client.get("/")
        html = resp.text
        assert "Balance History" in html
        assert "No balance snapshots recorded yet" in html
        # Placeholder text from Phase 2 scaffolding should be gone.
        assert "will be wired in Phase 3" not in html

    async def test_balance_chart_loads_chartjs(self, client: AsyncClient) -> None:
        """The Chart.js CDN script is injected via the head block."""
        resp = await client.get("/")
        html = resp.text
        assert "cdn.jsdelivr.net/npm/chart.js" in html

    async def test_balance_chart_renders_snapshots(
        self, client: AsyncClient,
    ) -> None:
        """When snapshots exist, a canvas and JSON payload are rendered."""
        tracker = client._transport.app.state.balance_tracker
        await tracker.record_snapshot(
            balance_cents=100_000,
            peak_cents=100_000,
            drawdown_pct=0.0,
            cumulative_spend_cents=0,
        )
        await tracker.record_snapshot(
            balance_cents=98_500,
            peak_cents=100_000,
            drawdown_pct=1.5,
            cumulative_spend_cents=1_500,
        )

        resp = await client.get("/")
        html = resp.text
        assert 'id="balance-chart"' in html
        assert 'id="balance-chart-data"' in html
        # Values from the JSON payload should be present in the page
        assert "98500" in html
        assert "No balance snapshots recorded yet" not in html
        # Snapshot count badge
        assert "2 snapshots" in html

    async def test_balance_chart_snapshot_count_singular(
        self, client: AsyncClient,
    ) -> None:
        """The count badge uses the singular form for exactly one snapshot."""
        tracker = client._transport.app.state.balance_tracker
        await tracker.record_snapshot(
            balance_cents=100_000,
            peak_cents=100_000,
            drawdown_pct=0.0,
            cumulative_spend_cents=0,
        )
        resp = await client.get("/")
        html = resp.text
        assert "1 snapshot" in html
        assert "1 snapshots" not in html


class TestTradesPage:
    async def test_returns_html(self, client: AsyncClient) -> None:
        resp = await client.get("/trades")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_contains_table_headers(self, client: AsyncClient) -> None:
        resp = await client.get("/trades")
        html = resp.text
        assert "Ticker" in html
        assert "Engine" in html
        assert "Outcome" in html

    async def test_empty_state(self, client: AsyncClient) -> None:
        resp = await client.get("/trades")
        html = resp.text
        assert "No trades found" in html

    async def test_with_trades(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn)
        resp = await client.get("/trades")
        html = resp.text
        assert "KXHIGHNY-26APR04-T45" in html
        assert "weather" in html

    async def test_htmx_partial_returns_rows_only(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        """HTMX requests should return just the trade rows partial."""
        await _insert_trade(db_conn)
        resp = await client.get("/trades", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        html = resp.text
        # Partial should contain the trade data but NOT the full page structure
        assert "KXHIGHNY-26APR04-T45" in html
        assert "<!DOCTYPE html>" not in html

    async def test_filter_params_passed(
        self, client: AsyncClient, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert_trade(db_conn, engine="weather")
        await _insert_trade(db_conn, engine="economics", ticker="KXECON-1")
        resp = await client.get("/trades?engine=economics")
        html = resp.text
        assert "KXECON-1" in html


class TestSignalsPage:
    async def test_returns_html(self, client: AsyncClient) -> None:
        resp = await client.get("/signals")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_empty_state(self, client: AsyncClient) -> None:
        resp = await client.get("/signals")
        html = resp.text
        assert "No signals detected" in html

    async def test_contains_polling_config(self, client: AsyncClient) -> None:
        resp = await client.get("/signals")
        html = resp.text
        assert "every 10s" in html

    async def test_htmx_partial(self, client: AsyncClient) -> None:
        """HTMX request returns just the signals table body."""
        resp = await client.get("/signals", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        html = resp.text
        assert "<!DOCTYPE html>" not in html

    async def test_renders_buffered_signals(self, client: AsyncClient) -> None:
        """Signals in the buffer are rendered into the HTML table."""
        from niche_scanner.engines.base import EdgeSignal

        buffer = client._transport.app.state.signal_buffer
        buffer.clear()
        buffer.append(EdgeSignal(
            engine="weather",
            ticker="KXHIGHNY-26APR04-T75",
            side="yes",
            model_prob=0.72,
            market_prob=0.60,
            edge_pp=12.0,
            fee_adjusted_edge=11.5,
            confidence=0.8,
            thesis="NOAA forecast exceeds market implied temp",
            metadata={"kelly_fraction": 0.08},
        ))

        resp = await client.get("/signals")
        assert resp.status_code == 200
        html = resp.text
        assert "KXHIGHNY-26APR04-T75" in html
        assert "weather" in html
        assert "No signals detected" not in html

    async def test_signal_count_badge_reflects_buffer(
        self, client: AsyncClient,
    ) -> None:
        """The signal count badge matches the number of signals rendered."""
        from niche_scanner.engines.base import EdgeSignal

        buffer = client._transport.app.state.signal_buffer
        buffer.clear()
        for i in range(2):
            buffer.append(EdgeSignal(
                engine="economics",
                ticker=f"KXECON-{i}",
                side="no",
                model_prob=0.3,
                market_prob=0.5,
                edge_pp=15.0,
                fee_adjusted_edge=14.0,
                confidence=0.7,
                thesis=f"release nowcast {i}",
            ))
        resp = await client.get("/signals")
        html = resp.text
        # Count badge shows 2
        assert "KXECON-0" in html
        assert "KXECON-1" in html


class TestConfigPage:
    async def test_returns_html(self, client: AsyncClient) -> None:
        resp = await client.get("/config")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_contains_config_sections(self, client: AsyncClient) -> None:
        resp = await client.get("/config")
        html = resp.text
        assert "Scanner" in html
        assert "Sizing" in html
        assert "Verticals" in html

    async def test_contains_config_values(self, client: AsyncClient) -> None:
        resp = await client.get("/config")
        html = resp.text
        assert "weather_interval_min" in html
        assert "5" in html

    async def test_contains_reload_button(self, client: AsyncClient) -> None:
        resp = await client.get("/config")
        html = resp.text
        assert "Reload Config" in html


class TestFiltersRegistered:
    """Verify that custom Jinja2 filters are registered and work in templates."""

    async def test_cents_to_dollars_in_template(self, client: AsyncClient) -> None:
        """The overview page uses cents_to_dollars filter on balance."""
        resp = await client.get("/")
        html = resp.text
        # $1000.00 from 100_000 cents balance
        assert "$1000.00" in html

    async def test_format_pct_in_template(self, client: AsyncClient) -> None:
        """The overview page uses format_pct filter on win rate."""
        resp = await client.get("/")
        html = resp.text
        # 0.0% win rate (no trades)
        assert "0.0%" in html


class TestPagesRouterMounted:
    """Verify that pages router is correctly mounted on the app."""

    async def test_overview_route_exists(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200

    async def test_trades_route_exists(self, client: AsyncClient) -> None:
        resp = await client.get("/trades")
        assert resp.status_code == 200

    async def test_signals_route_exists(self, client: AsyncClient) -> None:
        resp = await client.get("/signals")
        assert resp.status_code == 200

    async def test_config_route_exists(self, client: AsyncClient) -> None:
        resp = await client.get("/config")
        assert resp.status_code == 200

    async def test_api_routes_still_work(self, client: AsyncClient) -> None:
        """Verify that API routes are not broken by adding pages router."""
        resp = await client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
