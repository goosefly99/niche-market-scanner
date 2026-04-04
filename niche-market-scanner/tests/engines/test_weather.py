"""Tests for the weather edge engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from niche_scanner.config import ICAOStations
from niche_scanner.engines.weather import NOAAForecast, WeatherEdgeEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_icao_stations(stations: dict) -> ICAOStations:
    """Build an ICAOStations instance backed by a temporary YAML file."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
        encoding="utf-8",
    ) as fh:
        yaml.safe_dump({"stations": stations}, fh)
        path = Path(fh.name)
    return ICAOStations(path=path)


# ---------------------------------------------------------------------------
# Test 1: bucket probability
# ---------------------------------------------------------------------------


def test_noaa_forecast_bucket_probability() -> None:
    """Mean=62, std=3. P(55-60) should be 0.15-0.30; P(60-65) should be higher."""
    fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

    p_55_60 = fc.bucket_probability(55.0, 60.0)
    p_60_65 = fc.bucket_probability(60.0, 65.0)

    assert 0.15 <= p_55_60 <= 0.30, f"P(55-60)={p_55_60:.4f} outside [0.15, 0.30]"
    assert p_60_65 > p_55_60, "P(60-65) should exceed P(55-60) since mean is 62"
    # P(60-65) should contain the mean, so it should be well above P(55-60)
    assert p_60_65 > 0.55


# ---------------------------------------------------------------------------
# Test 2: above threshold
# ---------------------------------------------------------------------------


def test_noaa_forecast_above_threshold() -> None:
    """Mean=62, std=3. P(T>=58) should be >0.85 (58 is ~1.33 std below mean)."""
    fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

    p_above_58 = fc.threshold_probability(58.0, direction="above")
    assert p_above_58 > 0.85, f"P(T>=58)={p_above_58:.4f}, expected >0.85"

    # Sanity: P(T < 58) should be the complement
    p_below_58 = fc.threshold_probability(58.0, direction="below")
    assert abs(p_above_58 + p_below_58 - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Test 3: engine refuses unverified city
# ---------------------------------------------------------------------------


async def test_engine_refuses_unverified_city() -> None:
    """scan() should produce no signals for a city not in ICAOStations."""
    # Only new_york is verified; los_angeles is absent entirely.
    stations = _make_icao_stations(
        {
            "new_york": {
                "icao": "KJFK",
                "verified": True,
                "office": "OKX",
                "grid_x": 33,
                "grid_y": 37,
            },
        },
    )

    engine = WeatherEdgeEngine(icao_stations=stations, min_edge_pp=5.0)

    from datetime import datetime, timezone

    from niche_scanner.kalshi.models import Market

    # A market ticker referencing LA (not in stations)
    market = Market(
        ticker="KXHIGHLA-26APR4-T80",
        event_ticker="KXHIGHLA",
        subtitle="80 or above",
        yes_bid=30,
        yes_ask=35,
        no_bid=60,
        no_ask=65,
        last_price=33,
        volume=100,
        open_interest=50,
        status="active",
        close_time=datetime(2026, 4, 4, tzinfo=timezone.utc),
    )

    signals = await engine.scan([market], {})
    assert signals == [], f"Expected no signals for unverified LA, got {signals}"


# ---------------------------------------------------------------------------
# Test 4: bucket boundary detection
# ---------------------------------------------------------------------------


def test_bucket_boundary_detection() -> None:
    """Mean=60 (exactly on a 5F boundary), std=3.

    P(55-60) and P(60-65) should be approximately equal since the mean
    sits exactly at the boundary.
    """
    fc = NOAAForecast(temperature_f=60.0, uncertainty_f=3.0)

    assert fc.is_boundary_adjacent, "60F is exactly on a 5F boundary"

    p_55_60 = fc.bucket_probability(55.0, 60.0)
    p_60_65 = fc.bucket_probability(60.0, 65.0)

    # Symmetric around the mean: both should be ~0.4525
    assert abs(p_55_60 - p_60_65) < 0.01, (
        f"P(55-60)={p_55_60:.4f} vs P(60-65)={p_60_65:.4f} should be ~equal"
    )

    # Non-boundary example: mean=62, std=3 -> nearest boundary is 60, distance=2
    fc2 = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)
    assert fc2.is_boundary_adjacent, "62F is within 1 std dev (3) of 60F boundary"

    # Mean far from boundary: mean=67.5, std=1 -> nearest boundary is 65 or 70, distance=2.5
    fc3 = NOAAForecast(temperature_f=67.5, uncertainty_f=1.0)
    assert not fc3.is_boundary_adjacent, "67.5F is 2.5F from nearest boundary (>1 std dev)"
