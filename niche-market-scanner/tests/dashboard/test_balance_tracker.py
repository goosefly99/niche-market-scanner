"""Tests for dashboard.balance_tracker.BalanceTracker."""

from __future__ import annotations

import pytest

from niche_scanner.journal.db import init_db
from niche_scanner.dashboard.balance_tracker import BalanceTracker


@pytest.fixture
async def conn(tmp_path):
    """Create a temporary database with the full schema and yield the connection."""
    db_path = str(tmp_path / "test_balance_history.db")
    connection = await init_db(db_path)
    yield connection
    await connection.close()


@pytest.fixture
async def tracker(conn) -> BalanceTracker:
    """Return a BalanceTracker backed by the temporary database."""
    return BalanceTracker(conn)


# ---------------------------------------------------------------------------
# record_snapshot
# ---------------------------------------------------------------------------


async def test_record_snapshot_returns_positive_id(tracker: BalanceTracker) -> None:
    """record_snapshot should return a positive auto-incremented row ID."""
    row_id = await tracker.record_snapshot(
        balance_cents=500_00,
        peak_cents=520_00,
        drawdown_pct=3.85,
        cumulative_spend_cents=150_00,
    )
    assert row_id > 0


async def test_record_snapshot_increments_id(tracker: BalanceTracker) -> None:
    """Successive inserts should yield monotonically increasing IDs."""
    id1 = await tracker.record_snapshot(
        balance_cents=500_00, peak_cents=500_00,
        drawdown_pct=0.0, cumulative_spend_cents=100_00,
    )
    id2 = await tracker.record_snapshot(
        balance_cents=490_00, peak_cents=500_00,
        drawdown_pct=2.0, cumulative_spend_cents=120_00,
    )
    assert id2 > id1


async def test_record_snapshot_persists_all_fields(tracker: BalanceTracker) -> None:
    """All fields passed to record_snapshot should be retrievable."""
    await tracker.record_snapshot(
        balance_cents=12345,
        peak_cents=15000,
        drawdown_pct=17.7,
        cumulative_spend_cents=8000,
    )
    history = await tracker.get_history(limit=1)
    assert len(history) == 1

    snap = history[0]
    assert snap["balance_cents"] == 12345
    assert snap["peak_cents"] == 15000
    assert snap["drawdown_pct"] == pytest.approx(17.7)
    assert snap["cumulative_spend_cents"] == 8000
    assert snap["created_at"] is not None


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


async def test_get_history_empty(tracker: BalanceTracker) -> None:
    """An empty table should return an empty list."""
    history = await tracker.get_history()
    assert history == []


async def test_get_history_ordered_asc(tracker: BalanceTracker) -> None:
    """Snapshots should be ordered by created_at ASC (oldest first for charting)."""
    for i in range(5):
        await tracker.record_snapshot(
            balance_cents=500_00 + i * 100,
            peak_cents=500_00 + i * 100,
            drawdown_pct=0.0,
            cumulative_spend_cents=100_00 + i * 50,
        )

    history = await tracker.get_history(limit=5)
    assert len(history) == 5
    # All rows have the same created_at (datetime('now')), so ordering
    # is stable by id ASC within the same second. Verify that the IDs
    # are in ascending order.
    ids = [h["id"] for h in history]
    assert ids == sorted(ids)


async def test_get_history_respects_limit(tracker: BalanceTracker) -> None:
    """Only the requested number of rows should be returned."""
    for i in range(10):
        await tracker.record_snapshot(
            balance_cents=500_00,
            peak_cents=500_00,
            drawdown_pct=0.0,
            cumulative_spend_cents=i * 100,
        )

    history = await tracker.get_history(limit=3)
    assert len(history) == 3


async def test_get_history_default_limit(tracker: BalanceTracker) -> None:
    """Default limit should be 200."""
    for i in range(210):
        await tracker.record_snapshot(
            balance_cents=500_00,
            peak_cents=500_00,
            drawdown_pct=0.0,
            cumulative_spend_cents=i * 10,
        )

    history = await tracker.get_history()
    assert len(history) == 200


async def test_get_history_returns_dicts(tracker: BalanceTracker) -> None:
    """Each item in the result should be a plain dict with expected keys."""
    await tracker.record_snapshot(
        balance_cents=750_00,
        peak_cents=800_00,
        drawdown_pct=6.25,
        cumulative_spend_cents=250_00,
    )
    history = await tracker.get_history(limit=1)
    assert len(history) == 1

    snap = history[0]
    assert isinstance(snap, dict)
    expected_keys = {
        "id", "balance_cents", "peak_cents", "drawdown_pct",
        "cumulative_spend_cents", "created_at",
    }
    assert expected_keys == set(snap.keys())
