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


# ---------------------------------------------------------------------------
# Test 5: parse_city_from_ticker with real Kalshi formats
# ---------------------------------------------------------------------------


class TestParseCityFromTicker:
    """Tests for _parse_city_from_ticker with actual Kalshi ticker formats.

    Real Kalshi weather tickers follow the pattern:
        KXHIGH<CITY>-<DATE>-<STRIKE>
    e.g. KXHIGHNY-26APR04-T75, KXHIGHCHI-26APR04-B74.5
    """

    def test_new_york_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHNY-26APR04-T75") == "new_york"

    def test_chicago_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHCHI-26APR04-B74.5") == "chicago"

    def test_dallas_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHDAL-26APR04-T80") == "dallas"

    def test_miami_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHMIA-26APR04-T85") == "miami"

    def test_denver_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHDEN-26APR04-T60") == "denver"

    def test_atlanta_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHATL-26APR04-T70") == "atlanta"

    def test_austin_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHAUS-26APR04-T78") == "austin"

    def test_los_angeles_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHLA-26APR04-T80") == "los_angeles"

    def test_phoenix_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHPHX-26APR04-T100") == "phoenix"

    def test_seattle_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHSEA-26APR04-T55") == "seattle"

    def test_boston_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHBOS-26APR04-T58") == "boston"

    def test_houston_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHHOU-26APR04-T85") == "houston"

    def test_philadelphia_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHPHL-26APR04-T65") == "philadelphia"

    def test_san_francisco_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHSFO-26APR04-T62") == "san_francisco"

    def test_washington_dc_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHDCA-26APR04-T68") == "washington_dc"

    def test_san_antonio_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHSAT-26APR04-T90") == "san_antonio"

    def test_las_vegas_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHLAS-26APR04-T95") == "las_vegas"

    def test_minneapolis_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHMSP-26APR04-T50") == "minneapolis"

    def test_new_orleans_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHMSY-26APR04-T82") == "new_orleans"

    def test_oklahoma_city_ticker(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHOKC-26APR04-T75") == "oklahoma_city"

    def test_unknown_city_returns_none(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHZZZ-26APR04-T75") is None

    def test_non_weather_ticker_returns_none(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("CPI-26APR04-250") is None

    def test_case_insensitive(self) -> None:
        assert WeatherEdgeEngine._parse_city_from_ticker("kxhighny-26apr04-t75") == "new_york"

    def test_lowtemp_series_prefix(self) -> None:
        """KXLOW series tickers should also parse correctly."""
        assert WeatherEdgeEngine._parse_city_from_ticker("KXLOWNY-26APR04-T30") == "new_york"

    def test_event_ticker_format(self) -> None:
        """Event tickers like KXHIGHNY without date/strike should still parse."""
        assert WeatherEdgeEngine._parse_city_from_ticker("KXHIGHNY") == "new_york"


# ---------------------------------------------------------------------------
# Test 6: parse_strike_from_ticker
# ---------------------------------------------------------------------------


class TestParseStrikeFromTicker:
    """Tests for _parse_strike_from_ticker.

    Extracts the strike info from the last segment of Kalshi weather tickers:
    - T75 -> ("above", 75.0) — threshold, YES = "75 or above"
    - B74.5 -> ("boundary", 74.5) — bucket boundary value
    """

    def test_threshold_integer(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHNY-26APR04-T75")
        assert result == ("above", 75.0)

    def test_threshold_three_digits(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHPHX-26APR04-T100")
        assert result == ("above", 100.0)

    def test_boundary_decimal(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHCHI-26APR04-B74.5")
        assert result == ("boundary", 74.5)

    def test_boundary_integer(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHNY-26APR04-B60")
        assert result == ("boundary", 60.0)

    def test_no_strike_segment_returns_none(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHNY")
        assert result is None

    def test_unknown_prefix_returns_none(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHNY-26APR04-X50")
        assert result is None

    def test_case_insensitive(self) -> None:
        result = WeatherEdgeEngine._parse_strike_from_ticker("kxhighny-26apr04-t75")
        assert result == ("above", 75.0)

    def test_negative_temperature(self) -> None:
        """Some cities can have sub-zero thresholds in winter."""
        result = WeatherEdgeEngine._parse_strike_from_ticker("KXHIGHMSP-26JAN15-T-5")
        assert result == ("above", -5.0)


# ---------------------------------------------------------------------------
# Test 7: _evaluate_market uses floor_strike/cap_strike
# ---------------------------------------------------------------------------


class TestEvaluateMarketWithStrikes:
    """Verify _evaluate_market reads floor_strike/cap_strike from Market model."""

    def _make_engine(self) -> WeatherEdgeEngine:
        stations = _make_icao_stations(
            {
                "new_york": {
                    "icao": "KNYC",
                    "verified": True,
                    "office": "OKX",
                    "grid_x": 34,
                    "grid_y": 38,
                },
            },
        )
        return WeatherEdgeEngine(icao_stations=stations, min_edge_pp=0.1)

    def test_range_market_with_strikes(self) -> None:
        """A range market (floor=60, cap=65) should use strikes for probability."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-B62",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="62F to 64F",
            yes_bid=40,
            yes_ask=45,
            no_bid=50,
            no_ask=55,
            last_price=42,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=62.0,
            cap_strike=64.0,
        )

        signal = engine._evaluate_market(market, fc)
        # With mean=62 and std=3, P(62-64) ~ 0.24, market price 45c = 0.45
        # Edge should be on the NO side
        assert signal is not None
        assert signal.side == "no"

    def test_above_market_with_floor_strike(self) -> None:
        """An 'above' market (floor=80, no cap) should use floor_strike."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-T80",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="80 or above",
            yes_bid=5,
            yes_ask=10,
            no_bid=85,
            no_ask=90,
            last_price=8,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=80.0,
            cap_strike=None,
        )

        signal = engine._evaluate_market(market, fc)
        # With mean=62 and std=3, P(>=80) is essentially 0
        # Market prices YES at 10c = 0.10, huge NO edge
        assert signal is not None
        assert signal.side == "no"

    def test_below_market_with_cap_strike(self) -> None:
        """A 'below' market (no floor, cap=50) should use cap_strike."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-T50",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="Below 50",
            yes_bid=2,
            yes_ask=5,
            no_bid=90,
            no_ask=95,
            last_price=3,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=None,
            cap_strike=50.0,
        )

        signal = engine._evaluate_market(market, fc)
        # With mean=62 and std=3, P(<50) is essentially 0
        # Market prices YES at 5c = 0.05, NO edge
        assert signal is not None
        assert signal.side == "no"


# ---------------------------------------------------------------------------
# Item 2.3: floor_strike/cap_strike preferred over subtitle parsing
# ---------------------------------------------------------------------------


class TestStrikePreferredOverSubtitle:
    """Verify _evaluate_market uses floor_strike/cap_strike when available,
    falling back to subtitle only when strikes are absent."""

    def _make_engine(self) -> WeatherEdgeEngine:
        stations = _make_icao_stations(
            {
                "new_york": {
                    "icao": "KNYC",
                    "station": "NYC",
                    "office": "OKX",
                    "grid_x": 34,
                    "grid_y": 38,
                    "verified": True,
                },
            },
        )
        return WeatherEdgeEngine(icao_stations=stations, min_edge_pp=0.1)

    def test_strikes_used_when_subtitle_empty(self) -> None:
        """Market with strikes but no subtitle should still produce a signal."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-B60.5",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="",  # Empty subtitle — must use strikes
            yes_bid=30,
            yes_ask=35,
            no_bid=60,
            no_ask=65,
            last_price=32,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=60.0,
            cap_strike=62.0,
        )

        signal = engine._evaluate_market(market, fc)
        assert signal is not None
        assert signal.ticker == "KXHIGHNY-26APR04-B60.5"

    def test_strikes_override_misleading_subtitle(self) -> None:
        """When strikes disagree with subtitle, strikes win."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        # Subtitle says "70F to 72F" but strikes say 60-62
        market = Market(
            ticker="KXHIGHNY-26APR04-B60.5",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="70F to 72F",  # Misleading!
            yes_bid=30,
            yes_ask=35,
            no_bid=60,
            no_ask=65,
            last_price=32,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=60.0,
            cap_strike=62.0,
        )

        signal = engine._evaluate_market(market, fc)
        assert signal is not None
        # If strikes are used: P(60-62) ~ 0.24, market=0.35
        # If subtitle were used: P(70-72) ~ 0.003, market=0.35, huge NO edge
        # The model_prob should reflect 60-62 (strikes), not 70-72 (subtitle)
        assert signal.metadata["bucket"] == (60.0, 62.0, "range")

    def test_subtitle_fallback_when_no_strikes(self) -> None:
        """Market without strikes should fall back to subtitle parsing."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-B60.5",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="60F to 62F",
            yes_bid=30,
            yes_ask=35,
            no_bid=60,
            no_ask=65,
            last_price=32,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=None,
            cap_strike=None,
        )

        signal = engine._evaluate_market(market, fc)
        assert signal is not None
        assert signal.metadata["bucket"] == (60.0, 62.0, "range")

    def test_above_threshold_from_strikes_no_subtitle(self) -> None:
        """Above-threshold market using floor_strike only, no subtitle."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-T75",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="",
            yes_bid=2,
            yes_ask=5,
            no_bid=90,
            no_ask=95,
            last_price=3,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=75.0,
            cap_strike=None,
        )

        signal = engine._evaluate_market(market, fc)
        assert signal is not None
        assert signal.metadata["bucket"][2] == "above"

    def test_no_signal_when_neither_strikes_nor_subtitle(self) -> None:
        """Market with no strikes and empty subtitle returns None."""
        from datetime import datetime, timezone

        from niche_scanner.kalshi.models import Market

        engine = self._make_engine()
        fc = NOAAForecast(temperature_f=62.0, uncertainty_f=3.0)

        market = Market(
            ticker="KXHIGHNY-26APR04-X99",
            event_ticker="KXHIGHNY-26APR04",
            subtitle="",
            yes_bid=30,
            yes_ask=35,
            no_bid=60,
            no_ask=65,
            last_price=32,
            volume=100,
            open_interest=50,
            status="active",
            close_time=datetime(2026, 4, 5, tzinfo=timezone.utc),
            floor_strike=None,
            cap_strike=None,
        )

        signal = engine._evaluate_market(market, fc)
        assert signal is None


# ---------------------------------------------------------------------------
# Item 6.10: Shared httpx.AsyncClient reuse
# ---------------------------------------------------------------------------


class TestHttpClientReuse:
    """Verify WeatherEdgeEngine reuses a single long-lived AsyncClient."""

    def _engine(self) -> WeatherEdgeEngine:
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
        return WeatherEdgeEngine(icao_stations=stations, min_edge_pp=5.0)

    def test_owns_client_by_default(self) -> None:
        """When no client is injected the engine owns its own client."""
        engine = self._engine()
        assert engine._owns_client is True
        assert engine._http_client is None  # lazy: not yet created

    async def test_external_client_disables_ownership(self) -> None:
        """When a client is injected the engine does not own it."""
        import httpx

        external = httpx.AsyncClient()
        try:
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
            engine = WeatherEdgeEngine(
                icao_stations=stations,
                min_edge_pp=5.0,
                http_client=external,
            )
            assert engine._owns_client is False
            assert engine._http_client is external
        finally:
            await external.aclose()

    def test_get_http_client_reuses_single_instance(self) -> None:
        """Successive calls to _get_http_client return the same client."""
        engine = self._engine()
        first = engine._get_http_client()
        second = engine._get_http_client()
        assert first is second

    async def test_close_is_idempotent(self) -> None:
        """close() may be called multiple times safely."""
        engine = self._engine()
        # Force client creation
        _ = engine._get_http_client()
        assert engine._http_client is not None

        await engine.close()
        assert engine._http_client is None

        # Second close() is a no-op
        await engine.close()
        assert engine._http_client is None

    async def test_close_noop_when_external_client(self) -> None:
        """close() does not aclose an externally supplied client."""
        import httpx

        external = httpx.AsyncClient()
        try:
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
            engine = WeatherEdgeEngine(
                icao_stations=stations,
                min_edge_pp=5.0,
                http_client=external,
            )
            await engine.close()
            # External client remains usable
            assert not external.is_closed
        finally:
            await external.aclose()
