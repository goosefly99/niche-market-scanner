"""Trade journal for recording and querying trades."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from niche_scanner.journal.db import init_db


@dataclass
class TradeRecord:
    """Data for a single trade entry."""

    ticker: str
    engine: str
    side: str
    action: str
    contracts: int
    price_cents: int
    model_prob: float
    market_prob: float
    edge_pp: float
    kelly_fraction: float
    thesis: str = ""


class TradeJournal:
    """Async trade journal backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database and ensure the schema is applied."""
        self._conn = await init_db(self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return the shared aiosqlite.Connection.

        Dashboard components (ScanCycleLogger, BalanceTracker, routes) use
        this property to share a single connection for concurrent reads
        under WAL mode.

        Raises ``RuntimeError`` if :meth:`initialize` has not been called.
        """
        if self._conn is None:
            raise RuntimeError("TradeJournal not initialized; call initialize() first")
        return self._conn

    def _ensure_conn(self) -> aiosqlite.Connection:
        """Internal helper — prefer the ``connection`` property for new code."""
        return self.connection

    async def record_trade(self, record: TradeRecord) -> int:
        """Insert a trade and return the new trade ID."""
        conn = self._ensure_conn()
        cursor = await conn.execute(
            """
            INSERT INTO trades
                (ticker, engine, side, action, contracts, price_cents,
                 model_prob, market_prob, edge_pp, kelly_fraction, thesis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.ticker,
                record.engine,
                record.side,
                record.action,
                record.contracts,
                record.price_cents,
                record.model_prob,
                record.market_prob,
                record.edge_pp,
                record.kelly_fraction,
                record.thesis,
            ),
        )
        await conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def record_outcome(self, trade_id: int, payout_cents: int) -> None:
        """Set the payout and outcome (win/loss) for a resolved trade."""
        conn = self._ensure_conn()
        await conn.execute(
            """
            UPDATE trades
               SET payout_cents = ?,
                   outcome      = CASE WHEN ? > 0 THEN 'win' ELSE 'loss' END,
                   resolved_at  = datetime('now')
             WHERE id = ?
            """,
            (payout_cents, payout_cents, trade_id),
        )
        await conn.commit()

    async def get_trades(
        self,
        engine: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent trades, optionally filtered by engine."""
        conn = self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        if engine is not None:
            cursor = await conn.execute(
                "SELECT * FROM trades WHERE engine = ? ORDER BY id DESC LIMIT ?",
                (engine, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_trade_by_id(self, trade_id: int) -> dict | None:
        """Return a single trade as a dict, or None if not found."""
        conn = self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_daily_stats(self) -> list[dict]:
        """Return per-day aggregates for resolved trades.

        Each item has keys: date, trades, wins, losses, net_pnl_cents.
        Results are ordered by date ascending.
        """
        conn = self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT
                date(created_at)                             AS date,
                COUNT(*)                                     AS trades,
                COALESCE(SUM(outcome = 'win'), 0)            AS wins,
                COALESCE(SUM(outcome = 'loss'), 0)           AS losses,
                COALESCE(SUM(payout_cents - cost_cents), 0)  AS net_pnl_cents
            FROM trades
            WHERE outcome IS NOT NULL
            GROUP BY date(created_at)
            ORDER BY date ASC
            """,
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_stats(self, engine: str | None = None) -> dict:
        """Return aggregate statistics for resolved trades.

        Returns a dict with keys: total_trades, wins, losses, win_rate,
        avg_edge_pp, net_pnl_cents.
        """
        conn = self._ensure_conn()
        if engine is not None:
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(*)                                     AS total_trades,
                    COALESCE(SUM(outcome = 'win'), 0)            AS wins,
                    COALESCE(SUM(outcome = 'loss'), 0)           AS losses,
                    COALESCE(AVG(edge_pp), 0.0)                  AS avg_edge_pp,
                    COALESCE(SUM(payout_cents - cost_cents), 0)  AS net_pnl_cents
                FROM trades
                WHERE outcome IS NOT NULL AND engine = ?
                """,
                (engine,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(*)                                     AS total_trades,
                    COALESCE(SUM(outcome = 'win'), 0)            AS wins,
                    COALESCE(SUM(outcome = 'loss'), 0)           AS losses,
                    COALESCE(AVG(edge_pp), 0.0)                  AS avg_edge_pp,
                    COALESCE(SUM(payout_cents - cost_cents), 0)  AS net_pnl_cents
                FROM trades
                WHERE outcome IS NOT NULL
                """
            )
        row = await cursor.fetchone()
        assert row is not None
        total = row[0]
        wins = row[1]
        losses = row[2]
        avg_edge = row[3]
        net_pnl = row[4]
        win_rate = wins / total if total > 0 else 0.0
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_edge_pp": avg_edge,
            "net_pnl_cents": net_pnl,
        }
