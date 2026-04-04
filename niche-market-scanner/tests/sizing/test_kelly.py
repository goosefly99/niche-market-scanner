"""Tests for Kelly position sizer."""

from __future__ import annotations

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.sizing.kelly import KellySizer, SizingConfig


def _make_signal(
    *,
    side: str = "yes",
    model_prob: float = 0.65,
    market_prob: float = 0.50,
    edge_pp: float = 25.0,
) -> EdgeSignal:
    return EdgeSignal(
        engine="test",
        ticker="TEST-TICKER",
        side=side,
        model_prob=model_prob,
        market_prob=market_prob,
        edge_pp=edge_pp,
        fee_adjusted_edge=edge_pp - 4.0,
        confidence=0.8,
        thesis="test thesis",
    )


def test_basic_kelly_sizing() -> None:
    sizer = KellySizer()
    signal = _make_signal(edge_pp=25.0)
    result = sizer.size(signal, bankroll_cents=1_000_000)
    assert result is not None
    assert result.contracts > 0
    assert result.side == "yes"


def test_below_min_edge_returns_none() -> None:
    sizer = KellySizer()
    signal = _make_signal(edge_pp=5.0)
    result = sizer.size(signal, bankroll_cents=1_000_000)
    assert result is None


def test_no_bet_capped_at_2_percent() -> None:
    sizer = KellySizer()
    signal = _make_signal(
        side="no",
        model_prob=0.35,
        market_prob=0.50,
        edge_pp=25.0,
    )
    result = sizer.size(signal, bankroll_cents=1_000_000)
    assert result is not None
    # 2% of 1M cents = 20000 cents
    assert result.cost_cents <= 20_000


def test_no_bet_aggregate_cap() -> None:
    sizer = KellySizer()
    signal = _make_signal(
        side="no",
        model_prob=0.35,
        market_prob=0.50,
        edge_pp=25.0,
    )
    # Already at 24% NO exposure, only 1% remaining
    result = sizer.size(signal, bankroll_cents=1_000_000, no_exposure_pct=0.24)
    assert result is not None
    # kelly_used capped to min(2%, 1%) = 1% → cost ≤ 10000 cents
    assert result.cost_cents <= 10_000


def test_no_bet_blocked_at_aggregate_cap() -> None:
    sizer = KellySizer()
    signal = _make_signal(
        side="no",
        model_prob=0.35,
        market_prob=0.50,
        edge_pp=25.0,
    )
    result = sizer.size(signal, bankroll_cents=1_000_000, no_exposure_pct=0.25)
    assert result is None


def test_negative_kelly_returns_none() -> None:
    sizer = KellySizer()
    # model_prob < market_prob → negative edge
    signal = _make_signal(
        side="yes",
        model_prob=0.40,
        market_prob=0.50,
        edge_pp=25.0,  # Claimed edge, but actual Kelly will be negative
    )
    result = sizer.size(signal, bankroll_cents=1_000_000)
    assert result is None
