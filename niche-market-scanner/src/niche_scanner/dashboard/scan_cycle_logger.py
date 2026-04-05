"""Scan cycle persistence for the dashboard.

Records scan cycle summaries (markets scanned, signals found, trades executed,
etc.) into the ``scan_cycles`` SQLite table.  Receives a shared
``aiosqlite.Connection`` from :pyattr:`TradeJournal.connection` — never opens
its own connection.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)


class ScanCycleLogger:
    """Write and query scan cycle metrics.

    Parameters
    ----------
    conn:
        A shared ``aiosqlite.Connection`` (from ``TradeJournal.connection``).
        Must already be initialised with the schema (``scan_cycles`` table).
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def record_cycle(
        self,
        markets_scanned: int,
        signals_found: int,
        executed: int,
        skipped: int,
        no_exposure_cents: int,
        duration_ms: int,
    ) -> int:
        """Insert a scan cycle summary and return the new row ID.

        Parameters
        ----------
        markets_scanned:
            Total number of markets evaluated in this cycle.
        signals_found:
            Number of EdgeSignals that met the edge threshold.
        executed:
            Number of trades placed (buy/sell).
        skipped:
            Number of signals skipped (risk guard, duplicate, etc.).
        no_exposure_cents:
            Net notional exposure avoided by skipping, in cents.
        duration_ms:
            Wall-clock duration of the scan cycle in milliseconds.

        Returns
        -------
        int
            The auto-incremented row ID of the inserted cycle.
        """
        cursor = await self._conn.execute(
            """
            INSERT INTO scan_cycles
                (markets_scanned, signals_found, executed, skipped,
                 no_exposure_cents, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (markets_scanned, signals_found, executed, skipped,
             no_exposure_cents, duration_ms),
        )
        await self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_recent_cycles(self, limit: int = 50) -> list[dict]:
        """Return the most recent scan cycles as a list of dicts.

        Results are ordered by ``created_at DESC`` (newest first), which is
        the natural order for a dashboard activity log.

        Parameters
        ----------
        limit:
            Maximum number of rows to return.  Defaults to 50.
        """
        self._conn.row_factory = aiosqlite.Row
        cursor = await self._conn.execute(
            "SELECT * FROM scan_cycles ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
