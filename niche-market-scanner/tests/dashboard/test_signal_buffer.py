"""Tests for the in-memory SignalBuffer."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from niche_scanner.dashboard.signal_buffer import DEFAULT_MAX_SIGNALS, SignalBuffer
from niche_scanner.engines.base import EdgeSignal


def _make_signal(ticker: str = "KXHIGHNY-26APR04-T45") -> EdgeSignal:
    """Build a minimal EdgeSignal for tests."""
    return EdgeSignal(
        engine="weather",
        ticker=ticker,
        side="yes",
        model_prob=0.72,
        market_prob=0.60,
        edge_pp=12.0,
        fee_adjusted_edge=11.5,
        confidence=0.8,
        thesis="test edge",
        metadata={"kelly_fraction": 0.08},
        timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestSignalBufferConstruction:
    def test_default_maxlen(self) -> None:
        buf = SignalBuffer()
        assert buf.maxlen == DEFAULT_MAX_SIGNALS
        assert len(buf) == 0

    def test_custom_maxlen(self) -> None:
        buf = SignalBuffer(maxlen=5)
        assert buf.maxlen == 5
        assert len(buf) == 0

    def test_maxlen_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            SignalBuffer(maxlen=0)

    def test_maxlen_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            SignalBuffer(maxlen=-1)


class TestSignalBufferAppend:
    def test_append_increases_length(self) -> None:
        buf = SignalBuffer(maxlen=10)
        buf.append(_make_signal("A"))
        assert len(buf) == 1
        buf.append(_make_signal("B"))
        assert len(buf) == 2

    def test_append_beyond_capacity_evicts_oldest(self) -> None:
        buf = SignalBuffer(maxlen=3)
        buf.append(_make_signal("A"))
        buf.append(_make_signal("B"))
        buf.append(_make_signal("C"))
        buf.append(_make_signal("D"))  # evicts A
        assert len(buf) == 3
        tickers = [s.ticker for s in buf.recent(limit=10)]
        # newest first: D, C, B
        assert tickers == ["D", "C", "B"]

    def test_extend_preserves_order(self) -> None:
        buf = SignalBuffer(maxlen=10)
        buf.extend([_make_signal("A"), _make_signal("B"), _make_signal("C")])
        assert len(buf) == 3
        tickers = [s.ticker for s in buf.recent(limit=10)]
        assert tickers == ["C", "B", "A"]

    def test_extend_with_eviction(self) -> None:
        buf = SignalBuffer(maxlen=3)
        buf.extend([_make_signal(str(i)) for i in range(5)])
        # Only last 3 remain: 2, 3, 4 -> newest first: 4, 3, 2
        assert len(buf) == 3
        tickers = [s.ticker for s in buf.recent(limit=10)]
        assert tickers == ["4", "3", "2"]


class TestSignalBufferRecent:
    def test_empty_returns_empty_list(self) -> None:
        buf = SignalBuffer()
        assert buf.recent() == []

    def test_newest_first(self) -> None:
        buf = SignalBuffer(maxlen=5)
        for t in ["X", "Y", "Z"]:
            buf.append(_make_signal(t))
        result = buf.recent(limit=3)
        assert [s.ticker for s in result] == ["Z", "Y", "X"]

    def test_respects_limit(self) -> None:
        buf = SignalBuffer(maxlen=10)
        for i in range(5):
            buf.append(_make_signal(str(i)))
        result = buf.recent(limit=2)
        assert len(result) == 2
        assert [s.ticker for s in result] == ["4", "3"]

    def test_limit_larger_than_size_returns_all(self) -> None:
        buf = SignalBuffer(maxlen=10)
        buf.append(_make_signal("solo"))
        result = buf.recent(limit=100)
        assert len(result) == 1
        assert result[0].ticker == "solo"

    def test_limit_zero_raises(self) -> None:
        buf = SignalBuffer()
        with pytest.raises(ValueError):
            buf.recent(limit=0)

    def test_limit_negative_raises(self) -> None:
        buf = SignalBuffer()
        with pytest.raises(ValueError):
            buf.recent(limit=-1)

    def test_recent_does_not_modify_buffer(self) -> None:
        buf = SignalBuffer(maxlen=5)
        for t in ["a", "b"]:
            buf.append(_make_signal(t))
        buf.recent()
        assert len(buf) == 2
        buf.recent(limit=1)
        assert len(buf) == 2


class TestSignalBufferClear:
    def test_clear_empties_buffer(self) -> None:
        buf = SignalBuffer(maxlen=3)
        for t in ["a", "b", "c"]:
            buf.append(_make_signal(t))
        assert len(buf) == 3
        buf.clear()
        assert len(buf) == 0
        assert buf.recent() == []

    def test_clear_on_empty_is_noop(self) -> None:
        buf = SignalBuffer()
        buf.clear()
        assert len(buf) == 0
