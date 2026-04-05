"""Tests for the thin-market edge engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from niche_scanner.engines.thin_market import ThinMarketEngine
from niche_scanner.kalshi.models import Market, OrderBook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _market(
    ticker: str = "TEST-MKT-01",
    yes_bid: int = 30,
    yes_ask: int = 50,
    no_bid: int = 50,
    no_ask: int = 70,
    last_price: int = 40,
    volume: int = 5,
    status: str = "active",
    hours_to_close: float = 48.0,
) -> Market:
    """Build a Market with sensible defaults for thin-market testing."""
    return Market(
        ticker=ticker,
        event_ticker="EVT-TEST",
        subtitle="Test market",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        last_price=last_price,
        volume=volume,
        open_interest=10,
        status=status,
        close_time=datetime.now(timezone.utc) + timedelta(hours=hours_to_close),
    )


def _orderbook(
    ticker: str = "TEST-MKT-01",
    yes_levels: list[list[int]] | None = None,
    no_levels: list[list[int]] | None = None,
) -> OrderBook:
    """Build an OrderBook from raw [price, qty] lists."""
    return OrderBook(
        ticker=ticker,
        yes=yes_levels or [],
        no=no_levels or [],
    )


# ---------------------------------------------------------------------------
# Test 1: wide spread produces YES signal
# ---------------------------------------------------------------------------


async def test_wide_spread_yes_signal() -> None:
    """A wide spread with mid-price well above yes_ask should produce a YES signal."""
    # yes_bid=30, no_bid=10 => implied_yes_ask = 100-10 = 90
    # mid = (30 + 90) / 2 = 60
    # yes_ask=35 => raw edge = 60-35 = 25 pp, fee_pp(35) ≈ 11.4
    # adj ≈ 13.6 >= 5.0
    mkt = _market(yes_bid=30, yes_ask=35, no_bid=10, no_ask=70)
    ob = _orderbook(yes_levels=[[30, 10]], no_levels=[[10, 10]])

    engine = ThinMarketEngine(min_edge_pp=5.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert len(signals) == 1
    assert signals[0].side == "yes"
    assert signals[0].engine == "thin_market"
    assert signals[0].fee_adjusted_edge >= 5.0


# ---------------------------------------------------------------------------
# Test 2: narrow spread is skipped
# ---------------------------------------------------------------------------


async def test_narrow_spread_skipped() -> None:
    """Markets with spread below min_spread_cents should produce no signals."""
    mkt = _market(yes_bid=48, yes_ask=50, no_bid=50, no_ask=52)
    ob = _orderbook(yes_levels=[[48, 10]], no_levels=[[50, 10]])

    engine = ThinMarketEngine(min_edge_pp=1.0, min_spread_cents=8)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 3: spread too wide is skipped
# ---------------------------------------------------------------------------


async def test_spread_too_wide_skipped() -> None:
    """Markets with spread above max_spread_cents should produce no signals."""
    mkt = _market(yes_bid=10, yes_ask=70, no_bid=30, no_ask=90)
    ob = _orderbook(yes_levels=[[10, 5]], no_levels=[[30, 5]])

    engine = ThinMarketEngine(min_edge_pp=1.0, max_spread_cents=50)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 4: no orderbook means no signal
# ---------------------------------------------------------------------------


async def test_no_orderbook_skipped() -> None:
    """Markets without an orderbook should produce no signals."""
    mkt = _market()
    engine = ThinMarketEngine(min_edge_pp=5.0)
    signals = await engine.scan([mkt], {})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 5: inactive market is skipped
# ---------------------------------------------------------------------------


async def test_inactive_market_skipped() -> None:
    """Markets with status != 'active' should produce no signals."""
    mkt = _market(status="closed")
    ob = _orderbook(yes_levels=[[30, 10]], no_levels=[[40, 10]])

    engine = ThinMarketEngine(min_edge_pp=1.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 6: market outside time window is skipped
# ---------------------------------------------------------------------------


async def test_time_window_too_close_skipped() -> None:
    """Markets expiring too soon should produce no signals."""
    mkt = _market(hours_to_close=0.5)
    ob = _orderbook(yes_levels=[[30, 10]], no_levels=[[40, 10]])

    engine = ThinMarketEngine(
        min_edge_pp=1.0, min_spread_cents=1, min_time_to_close_hours=2.0,
    )
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


async def test_time_window_too_far_skipped() -> None:
    """Markets expiring too far out should produce no signals."""
    mkt = _market(hours_to_close=500.0)
    ob = _orderbook(yes_levels=[[30, 10]], no_levels=[[40, 10]])

    engine = ThinMarketEngine(
        min_edge_pp=1.0, min_spread_cents=1, max_time_to_close_hours=168.0,
    )
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 7: fee adjustment filters marginal edge
# ---------------------------------------------------------------------------


async def test_fee_adjustment_filters_marginal() -> None:
    """An edge that is positive pre-fee but negative post-fee should be skipped."""
    # mid = (40 + (100-55)) / 2 = (40 + 45) / 2 = 42.5
    # yes_ask = 41 => raw edge = 42.5 - 41 = 1.5 pp
    # After fees this should be below 12pp threshold
    mkt = _market(yes_bid=40, yes_ask=41, no_bid=55, no_ask=60)
    ob = _orderbook(yes_levels=[[40, 10]], no_levels=[[55, 10]])

    engine = ThinMarketEngine(min_edge_pp=12.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 8: NO-side signal when NO is cheaper than fair value
# ---------------------------------------------------------------------------


async def test_no_side_signal() -> None:
    """When the NO ask is cheap relative to fair NO value, produce a NO signal."""
    # yes_bid=10, no_bid=10 => implied_yes_ask = 100-10 = 90
    # mid = (10 + 90) / 2 = 50 => fair NO = 50 cents
    # no_ask=30 => raw NO edge = (50-30) = 20 pp, fee_pp(30) ≈ 13.3
    # adj ≈ 6.7 >= 5.0
    # YES side: yes_ask=60 => raw = (50-60) = -10 → no YES signal
    mkt = _market(yes_bid=10, yes_ask=60, no_bid=10, no_ask=30)
    ob = _orderbook(yes_levels=[[10, 10]], no_levels=[[10, 10]])

    engine = ThinMarketEngine(min_edge_pp=5.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert len(signals) == 1
    assert signals[0].side == "no"
    assert signals[0].fee_adjusted_edge >= 5.0


# ---------------------------------------------------------------------------
# Test 9: one-sided book falls back to last_price
# ---------------------------------------------------------------------------


async def test_one_sided_book_fallback() -> None:
    """When orderbook has only one side, fall back to last_price for fair value."""
    # Only YES bids in orderbook, no NO bids => mid_price is None
    # Fallback to last_price=60
    # yes_ask=35 => raw edge = (60-35) = 25 pp, fee_pp(35) ≈ 11.4
    # adj ≈ 13.6 >= 5.0
    mkt = _market(yes_bid=20, yes_ask=35, no_bid=0, no_ask=80, last_price=60, volume=5)
    ob = _orderbook(yes_levels=[[20, 10]], no_levels=[])

    engine = ThinMarketEngine(min_edge_pp=5.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert len(signals) == 1
    assert signals[0].engine == "thin_market"
    assert signals[0].metadata["detection"] == "wide_spread"


# ---------------------------------------------------------------------------
# Test 10: one-sided book with zero volume has no fallback
# ---------------------------------------------------------------------------


async def test_one_sided_book_no_volume_skipped() -> None:
    """One-sided book with volume=0 should not produce signals (no reliable fair value)."""
    mkt = _market(yes_bid=20, yes_ask=35, no_bid=0, no_ask=80, last_price=60, volume=0)
    ob = _orderbook(yes_levels=[[20, 10]], no_levels=[])

    engine = ThinMarketEngine(min_edge_pp=1.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert signals == []


# ---------------------------------------------------------------------------
# Test 11: confidence scoring
# ---------------------------------------------------------------------------


def test_confidence_tight_spread_high_volume_near_close() -> None:
    """Tight spread + volume + near close = highest confidence."""
    engine = ThinMarketEngine()
    conf = engine._confidence(spread=10, hours_to_close=12.0, volume=15)
    assert conf >= 0.7


def test_confidence_wide_spread_no_volume_far_close() -> None:
    """Wide spread + no volume + far close = lowest confidence."""
    engine = ThinMarketEngine()
    conf = engine._confidence(spread=40, hours_to_close=100.0, volume=0)
    assert conf <= 0.3


# ---------------------------------------------------------------------------
# Test 12: best side is selected when both have edge
# ---------------------------------------------------------------------------


async def test_best_side_selected() -> None:
    """When both YES and NO have edge, the better one is returned."""
    # yes_bid=20, no_bid=30 => implied_yes_ask = 100-30 = 70
    # mid = (20 + 70) / 2 = 45
    # yes_ask=25 => YES raw = 45-25 = 20, fee_pp(25) ≈ 16, adj ≈ 4.0
    # no_ask=35 => NO fair=55, raw = 55-35 = 20, fee_pp(35) ≈ 11.4, adj ≈ 8.6
    # Both pass at min_edge=3.0; NO side wins
    mkt = _market(yes_bid=20, yes_ask=25, no_bid=30, no_ask=35)
    ob = _orderbook(yes_levels=[[20, 10]], no_levels=[[30, 10]])

    engine = ThinMarketEngine(min_edge_pp=3.0, min_spread_cents=1)
    signals = await engine.scan([mkt], {mkt.ticker: ob})

    assert len(signals) == 1
    assert signals[0].fee_adjusted_edge >= 3.0


# ---------------------------------------------------------------------------
# Test 13: multiple markets produce multiple signals
# ---------------------------------------------------------------------------


async def test_multiple_markets() -> None:
    """Multiple qualifying markets should each produce signals."""
    # MKT-A: mid = (30+90)/2 = 60, yes_ask=35 => adj ≈ 13.6
    mkt1 = _market(ticker="MKT-A", yes_bid=30, yes_ask=35, no_bid=10, no_ask=70)
    # MKT-B: mid = (20+90)/2 = 55, yes_ask=25 => adj ≈ 14.0
    mkt2 = _market(ticker="MKT-B", yes_bid=20, yes_ask=25, no_bid=10, no_ask=80)

    ob1 = _orderbook(ticker="MKT-A", yes_levels=[[30, 10]], no_levels=[[10, 10]])
    ob2 = _orderbook(ticker="MKT-B", yes_levels=[[20, 10]], no_levels=[[10, 10]])

    engine = ThinMarketEngine(min_edge_pp=5.0, min_spread_cents=1)
    signals = await engine.scan(
        [mkt1, mkt2],
        {"MKT-A": ob1, "MKT-B": ob2},
    )

    tickers = {s.ticker for s in signals}
    assert len(tickers) == 2
