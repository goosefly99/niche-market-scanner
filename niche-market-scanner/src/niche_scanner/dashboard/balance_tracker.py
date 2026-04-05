"""Balance history persistence for the dashboard.

Records periodic balance snapshots (balance, peak, drawdown, cumulative spend)
into the ``balance_history`` SQLite table.  Receives a shared
``aiosqlite.Connection`` from :pyattr:`TradeJournal.connection` — never opens
its own connection.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)


class BalanceTracker:
    """Write and query balance history snapshots.

    Parameters
    ----------
    conn:
        A shared ``aiosqlite.Connection`` (from ``TradeJournal.connection``).
        Must already be initialised with the schema (``balance_history`` table).
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def record_snapshot(
        self,
        balance_cents: int,
        peak_cents: int,
        drawdown_pct: float,
        cumulative_spend_cents: int,
    ) -> int:
        """Insert a balance snapshot and return the new row ID.

        Parameters
        ----------
        balance_cents:
            Current portfolio balance in cents.
        peak_cents:
            All-time peak balance in cents.
        drawdown_pct:
            Current drawdown as a percentage (0.0–100.0).
        cumulative_spend_cents:
            Total amount spent on trades since inception, in cents.

        Returns
        -------
        int
            The auto-incremented row ID of the inserted snapshot.
        """
        cursor = await self._conn.execute(
            """
            INSERT INTO balance_history
                (balance_cents, peak_cents, drawdown_pct, cumulative_spend_cents)
            VALUES (?, ?, ?, ?)
            """,
            (balance_cents, peak_cents, drawdown_pct, cumulative_spend_cents),
        )
        await self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_history(self, limit: int = 200) -> list[dict]:
        """Return balance history snapshots as a list of dicts.

        Results are ordered by ``created_at ASC`` (oldest first), which is
        the natural order for charting a time series.

        Parameters
        ----------
        limit:
            Maximum number of rows to return.  Defaults to 200.
        """
        self._conn.row_factory = aiosqlite.Row
        cursor = await self._conn.execute(
            "SELECT * FROM balance_history ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
