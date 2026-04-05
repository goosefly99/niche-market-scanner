"""Tests for dashboard.scan_cycle_logger.ScanCycleLogger."""

from __future__ import annotations

import pytest

from niche_scanner.journal.db import init_db
from niche_scanner.dashboard.scan_cycle_logger import ScanCycleLogger


@pytest.fixture
async def conn(tmp_path):
    """Create a temporary database with the full schema and yield the connection."""
    db_path = str(tmp_path / "test_scan_cycles.db")
    connection = await init_db(db_path)
    yield connection
    await connection.close()


@pytest.fixture
async def logger(conn) -> ScanCycleLogger:
    """Return a ScanCycleLogger backed by the temporary database."""
    return ScanCycleLogger(conn)


# ---------------------------------------------------------------------------
# record_cycle
# ---------------------------------------------------------------------------


async def test_record_cycle_returns_positive_id(logger: ScanCycleLogger) -> None:
    """record_cycle should return a positive auto-incremented row ID."""
    row_id = await logger.record_cycle(
        markets_scanned=100,
        signals_found=5,
        executed=3,
        skipped=2,
        no_exposure_cents=500,
        duration_ms=3200,
    )
    assert row_id > 0


async def test_record_cycle_increments_id(logger: ScanCycleLogger) -> None:
    """Successive inserts should yield monotonically increasing IDs."""
    id1 = await logger.record_cycle(
        markets_scanned=50, signals_found=1, executed=1,
        skipped=0, no_exposure_cents=0, duration_ms=1000,
    )
    id2 = await logger.record_cycle(
        markets_scanned=60, signals_found=2, executed=1,
        skipped=1, no_exposure_cents=300, duration_ms=1500,
    )
    assert id2 > id1


async def test_record_cycle_persists_all_fields(logger: ScanCycleLogger) -> None:
    """All fields passed to record_cycle should be retrievable."""
    await logger.record_cycle(
        markets_scanned=538,
        signals_found=3,
        executed=2,
        skipped=1,
        no_exposure_cents=750,
        duration_ms=4500,
    )
    cycles = await logger.get_recent_cycles(limit=1)
    assert len(cycles) == 1

    cycle = cycles[0]
    assert cycle["markets_scanned"] == 538
    assert cycle["signals_found"] == 3
    assert cycle["executed"] == 2
    assert cycle["skipped"] == 1
    assert cycle["no_exposure_cents"] == 750
    assert cycle["duration_ms"] == 4500
    assert cycle["created_at"] is not None


# ---------------------------------------------------------------------------
# get_recent_cycles
# ---------------------------------------------------------------------------


async def test_get_recent_cycles_empty(logger: ScanCycleLogger) -> None:
    """An empty table should return an empty list."""
    cycles = await logger.get_recent_cycles()
    assert cycles == []


async def test_get_recent_cycles_ordered_desc(logger: ScanCycleLogger) -> None:
    """Cycles should be ordered by created_at DESC (newest first)."""
    for i in range(5):
        await logger.record_cycle(
            markets_scanned=10 * (i + 1),
            signals_found=i,
            executed=i,
            skipped=0,
            no_exposure_cents=0,
            duration_ms=1000 + i * 100,
        )

    cycles = await logger.get_recent_cycles(limit=5)
    assert len(cycles) == 5
    # All rows have the same created_at (datetime('now')), so ordering
    # is stable by id DESC within the same second. Verify that the IDs
    # are in descending order.
    ids = [c["id"] for c in cycles]
    assert ids == sorted(ids, reverse=True)


async def test_get_recent_cycles_respects_limit(logger: ScanCycleLogger) -> None:
    """Only the requested number of rows should be returned."""
    for i in range(10):
        await logger.record_cycle(
            markets_scanned=100,
            signals_found=i,
            executed=0,
            skipped=i,
            no_exposure_cents=0,
            duration_ms=2000,
        )

    cycles = await logger.get_recent_cycles(limit=3)
    assert len(cycles) == 3


async def test_get_recent_cycles_default_limit(logger: ScanCycleLogger) -> None:
    """Default limit should be 50."""
    for i in range(55):
        await logger.record_cycle(
            markets_scanned=10,
            signals_found=0,
            executed=0,
            skipped=0,
            no_exposure_cents=0,
            duration_ms=500,
        )

    cycles = await logger.get_recent_cycles()
    assert len(cycles) == 50


async def test_get_recent_cycles_returns_dicts(logger: ScanCycleLogger) -> None:
    """Each item in the result should be a plain dict with expected keys."""
    await logger.record_cycle(
        markets_scanned=200,
        signals_found=10,
        executed=5,
        skipped=5,
        no_exposure_cents=1200,
        duration_ms=5000,
    )
    cycles = await logger.get_recent_cycles(limit=1)
    assert len(cycles) == 1

    cycle = cycles[0]
    assert isinstance(cycle, dict)
    expected_keys = {
        "id", "markets_scanned", "signals_found", "executed",
        "skipped", "no_exposure_cents", "duration_ms", "created_at",
    }
    assert expected_keys == set(cycle.keys())
