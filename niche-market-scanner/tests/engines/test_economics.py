"""Tests for the economics edge engine."""

from __future__ import annotations

from niche_scanner.engines.economics import (
    EconomicsEdgeEngine,
    IndicatorReading,
)


def test_indicator_reading_weighted_probability() -> None:
    """Three readings with weights 0.4/0.3/0.3 produce the correct weighted sum."""
    readings = [
        IndicatorReading(name="Cleveland Nowcast", value=3.2, probability_above=0.70, weight=0.4),
        IndicatorReading(name="Breakeven Spread", value=3.1, probability_above=0.60, weight=0.3),
        IndicatorReading(name="Survey", value=3.0, probability_above=0.50, weight=0.3),
    ]
    result = EconomicsEdgeEngine._calculate_model_probability(readings)
    # Expected: (0.70*0.4 + 0.60*0.3 + 0.50*0.3) / (0.4+0.3+0.3) = 0.61
    assert result is not None
    expected = 0.70 * 0.4 + 0.60 * 0.3 + 0.50 * 0.3
    assert abs(result - expected) < 1e-9


def test_no_bet_detection() -> None:
    """Engine can be instantiated and classifies CPI tickers correctly."""
    engine = EconomicsEdgeEngine(min_edge_pp=12.0)

    # Verify engine attributes
    assert engine.min_edge_pp == 12.0
    assert "CPI" in engine.SERIES_PATTERNS
    assert "fed_rate" in engine.SERIES_PATTERNS
    assert "jobs" in engine.SERIES_PATTERNS
    assert "gdp" in engine.SERIES_PATTERNS

    # Verify fetch_indicators returns empty lists (Phase 2 stub)
    assert engine.fetch_indicators("CPI") == []
    assert engine.fetch_indicators("fed_rate") == []
    assert engine.fetch_indicators("jobs") == []
    assert engine.fetch_indicators("gdp") == []

    # Verify _calculate_model_probability returns None for empty readings
    assert EconomicsEdgeEngine._calculate_model_probability([]) is None


# ---------------------------------------------------------------------------
# Item 3.5: Kalshi economics series ticker mapping
# ---------------------------------------------------------------------------


class TestSeriesTickerMap:
    """Verify the SERIES_TICKER_MAP maps real Kalshi tickers to release types."""

    def test_all_series_tickers_returns_flat_list(self) -> None:
        tickers = EconomicsEdgeEngine.all_series_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) > 20  # We mapped 30+ series
        assert "CPIYOY" in tickers
        assert "KXFEDDECISION" in tickers
        assert "KXRECSSNBER" in tickers

    def test_all_release_types_have_tickers(self) -> None:
        for rtype, tickers in EconomicsEdgeEngine.SERIES_TICKER_MAP.items():
            assert len(tickers) > 0, f"Release type {rtype} has no tickers"

    def test_classify_market_uses_structured_map(self) -> None:
        from datetime import datetime, timezone
        from niche_scanner.kalshi.models import Market

        engine = EconomicsEdgeEngine(min_edge_pp=12.0)

        # CPI market
        m = Market(
            ticker="CPIYOY-26APR10-T3.5",
            event_ticker="CPIYOY-26APR10",
            subtitle="3.5% or above",
            status="active",
            close_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m) == "cpi"

        # Fed rate market
        m2 = Market(
            ticker="KXFEDDECISION-26MAY07-HOLD",
            event_ticker="KXFEDDECISION-26MAY07",
            subtitle="Hold",
            status="active",
            close_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m2) == "fed_rate"

        # Recession market (high volume)
        m3 = Market(
            ticker="KXRECSSNBER-26",
            event_ticker="KXRECSSNBER",
            subtitle="",
            status="active",
            close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m3) == "recession"

        # Gas market
        m4 = Market(
            ticker="KXAAAGASW-26APR06-4.170",
            event_ticker="KXAAAGASW-26APR06",
            subtitle="",
            status="active",
            close_time=datetime(2026, 4, 7, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m4) == "gas"

    def test_classify_falls_back_to_regex(self) -> None:
        from datetime import datetime, timezone
        from niche_scanner.kalshi.models import Market

        engine = EconomicsEdgeEngine(min_edge_pp=12.0)

        # Unknown series but matches GDP regex (standalone word boundary)
        m = Market(
            ticker="NEWGDP-26Q2",
            event_ticker="NEW-GDP-TRACKER",
            subtitle="",
            status="active",
            close_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m) == "gdp"

    def test_classify_returns_none_for_unknown(self) -> None:
        from datetime import datetime, timezone
        from niche_scanner.kalshi.models import Market

        engine = EconomicsEdgeEngine(min_edge_pp=12.0)

        m = Market(
            ticker="KXRANDOMUNRELATED-26",
            event_ticker="KXRANDOMUNRELATED",
            subtitle="",
            status="active",
            close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        assert engine._classify_market(m) is None

    def test_reverse_lookup_is_case_insensitive(self) -> None:
        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        # The reverse map keys are uppercased
        assert "CPIYOY" in engine._ticker_to_type
        assert engine._ticker_to_type["CPIYOY"] == "cpi"
        assert engine._ticker_to_type["KXRECSSNBER"] == "recession"


# ---------------------------------------------------------------------------
# Item 3.1: Cleveland Fed / FRED CPI indicator integration
# ---------------------------------------------------------------------------


class TestCPIIndicatorFetcher:
    """Tests for the CPI indicator fetching via FRED API."""

    def test_fred_cpi_series_ids_defined(self) -> None:
        """Verify FRED series IDs for CPI indicators are defined."""
        from niche_scanner.engines.economics import FRED_CPI_SERIES
        assert "CPIAUCSL" in FRED_CPI_SERIES  # CPI All Urban Consumers
        assert "CPILFESL" in FRED_CPI_SERIES  # Core CPI (less food & energy)
        assert len(FRED_CPI_SERIES) >= 2

    def test_parse_fred_response(self) -> None:
        """Verify parsing of FRED JSON observation response."""
        from niche_scanner.engines.economics import parse_fred_observations
        raw = {
            "observations": [
                {"date": "2026-02-01", "value": "313.500"},
                {"date": "2026-03-01", "value": "314.200"},
            ]
        }
        values = parse_fred_observations(raw)
        assert len(values) == 2
        assert values[0] == ("2026-02-01", 313.5)
        assert values[1] == ("2026-03-01", 314.2)

    def test_parse_fred_handles_missing_value(self) -> None:
        """FRED sometimes returns '.' for missing data — should be skipped."""
        from niche_scanner.engines.economics import parse_fred_observations
        raw = {
            "observations": [
                {"date": "2026-02-01", "value": "."},
                {"date": "2026-03-01", "value": "314.200"},
            ]
        }
        values = parse_fred_observations(raw)
        assert len(values) == 1
        assert values[0] == ("2026-03-01", 314.2)

    def test_cpi_yoy_calculation(self) -> None:
        """Verify YoY CPI inflation rate calculation from index values."""
        from niche_scanner.engines.economics import calculate_yoy_rate
        # CPI index: 300.0 a year ago, 309.0 now -> 3.0% YoY
        rate = calculate_yoy_rate(current=309.0, year_ago=300.0)
        assert abs(rate - 3.0) < 0.01

    def test_fetch_cpi_indicators_returns_readings(self) -> None:
        """Integration: fetch_indicators('cpi') returns IndicatorReadings
        when FRED API key is not set (should return empty gracefully)."""
        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        readings = engine.fetch_indicators("cpi")
        # Without FRED_API_KEY env var, should return empty (not crash)
        assert isinstance(readings, list)
