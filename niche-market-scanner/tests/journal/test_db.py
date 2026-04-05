"""Tests for journal database initialization."""

from __future__ import annotations

from niche_scanner.journal.db import init_db


async def test_init_db_enables_wal_mode(tmp_path) -> None:
    """init_db() should set SQLite journal_mode to WAL for concurrent reads."""
    db_path = str(tmp_path / "test_wal.db")
    conn = await init_db(db_path)
    try:
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "wal"
    finally:
        await conn.close()
