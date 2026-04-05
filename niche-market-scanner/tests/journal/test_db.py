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


async def test_scan_cycles_table_exists(tmp_path) -> None:
    """init_db() creates the scan_cycles table."""
    db_path = str(tmp_path / "test_tables.db")
    conn = await init_db(db_path)
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_cycles'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "scan_cycles"

        # Verify columns
        cursor = await conn.execute("PRAGMA table_info(scan_cycles)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "markets_scanned" in columns
        assert "signals_found" in columns
        assert "executed" in columns
        assert "skipped" in columns
        assert "duration_ms" in columns
        assert "created_at" in columns
    finally:
        await conn.close()


async def test_balance_history_table_exists(tmp_path) -> None:
    """init_db() creates the balance_history table."""
    db_path = str(tmp_path / "test_tables.db")
    conn = await init_db(db_path)
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='balance_history'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "balance_history"

        # Verify columns
        cursor = await conn.execute("PRAGMA table_info(balance_history)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "balance_cents" in columns
        assert "peak_cents" in columns
        assert "drawdown_pct" in columns
        assert "cumulative_spend_cents" in columns
        assert "created_at" in columns
    finally:
        await conn.close()


async def test_scan_cycles_insert_and_query(tmp_path) -> None:
    """Can insert into scan_cycles and query back."""
    db_path = str(tmp_path / "test_insert.db")
    conn = await init_db(db_path)
    try:
        await conn.execute(
            "INSERT INTO scan_cycles (markets_scanned, signals_found, executed, skipped, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (538, 3, 2, 1, 4500),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM scan_cycles")
        row = await cursor.fetchone()
        assert row is not None
        # id, markets_scanned, signals_found, executed, skipped, no_exposure_cents, duration_ms, created_at
        assert row[1] == 538  # markets_scanned
        assert row[2] == 3    # signals_found
        assert row[3] == 2    # executed
        assert row[4] == 1    # skipped
    finally:
        await conn.close()
