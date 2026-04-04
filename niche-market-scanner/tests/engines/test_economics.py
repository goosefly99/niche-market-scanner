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
