"""Tests for main._record_balance_snapshot helper.

These tests cover the snapshot helper in isolation, verifying drawdown
computation and pass-through of the other fields to
:class:`BalanceTracker.record_snapshot`.
"""

from __future__ import annotations

import pytest

from niche_scanner.dashboard.balance_tracker import BalanceTracker
from niche_scanner.journal.db import init_db
from niche_scanner.main import _record_balance_snapshot


@pytest.fixture
async def tracker(tmp_path):
    """Create a BalanceTracker wired to a fresh in-memory sqlite schema."""
    db_path = str(tmp_path / "balance.db")
    conn = await init_db(db_path)
    yield BalanceTracker(conn)
    await conn.close()


async def test_snapshot_zero_drawdown_at_peak(tracker: BalanceTracker) -> None:
    """When balance equals peak, drawdown_pct must be 0.0."""
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=1_000_000,
        peak_cents=1_000_000,
        cumulative_spend_cents=0,
    )
    history = await tracker.get_history(limit=1)
    assert len(history) == 1
    assert history[0]["drawdown_pct"] == pytest.approx(0.0)
    assert history[0]["balance_cents"] == 1_000_000
    assert history[0]["peak_cents"] == 1_000_000
    assert history[0]["cumulative_spend_cents"] == 0


async def test_snapshot_computes_drawdown_as_percentage(
    tracker: BalanceTracker,
) -> None:
    """Drawdown is (1 - balance/peak) * 100 when below peak."""
    # 850k from peak 1000k -> 15% drawdown
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=850_000,
        peak_cents=1_000_000,
        cumulative_spend_cents=200_000,
    )
    history = await tracker.get_history(limit=1)
    assert history[0]["drawdown_pct"] == pytest.approx(15.0)


async def test_snapshot_no_drawdown_when_balance_above_peak(
    tracker: BalanceTracker,
) -> None:
    """Guard: if caller passes balance > peak, drawdown stays at 0."""
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=1_200_000,
        peak_cents=1_000_000,
        cumulative_spend_cents=0,
    )
    history = await tracker.get_history(limit=1)
    assert history[0]["drawdown_pct"] == pytest.approx(0.0)


async def test_snapshot_handles_zero_peak(tracker: BalanceTracker) -> None:
    """Guard: peak_cents=0 must not divide-by-zero."""
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=0,
        peak_cents=0,
        cumulative_spend_cents=0,
    )
    history = await tracker.get_history(limit=1)
    assert history[0]["drawdown_pct"] == pytest.approx(0.0)


async def test_snapshot_returns_positive_row_id(tracker: BalanceTracker) -> None:
    """Helper must return the BalanceTracker row ID."""
    row_id = await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=500_000,
        peak_cents=500_000,
        cumulative_spend_cents=0,
    )
    assert isinstance(row_id, int)
    assert row_id > 0


async def test_snapshot_persists_cumulative_spend(
    tracker: BalanceTracker,
) -> None:
    """cumulative_spend_cents must be stored verbatim."""
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=950_000,
        peak_cents=1_000_000,
        cumulative_spend_cents=75_000,
    )
    history = await tracker.get_history(limit=1)
    assert history[0]["cumulative_spend_cents"] == 75_000


async def test_snapshot_fractional_drawdown(tracker: BalanceTracker) -> None:
    """Fractional drawdowns must be preserved with reasonable precision."""
    # 997_000 from peak 1_000_000 -> 0.3% drawdown
    await _record_balance_snapshot(
        tracker=tracker,
        balance_cents=997_000,
        peak_cents=1_000_000,
        cumulative_spend_cents=5_000,
    )
    history = await tracker.get_history(limit=1)
    assert history[0]["drawdown_pct"] == pytest.approx(0.3, rel=1e-3)


async def test_snapshot_multiple_snapshots_ordered_by_time(
    tracker: BalanceTracker,
) -> None:
    """Successive snapshots must appear in insertion order via get_history."""
    for balance in (1_000_000, 990_000, 980_000):
        await _record_balance_snapshot(
            tracker=tracker,
            balance_cents=balance,
            peak_cents=1_000_000,
            cumulative_spend_cents=1_000_000 - balance,
        )

    history = await tracker.get_history(limit=10)
    assert len(history) == 3
    # get_history returns oldest first (ASC)
    assert history[0]["balance_cents"] == 1_000_000
    assert history[1]["balance_cents"] == 990_000
    assert history[2]["balance_cents"] == 980_000
