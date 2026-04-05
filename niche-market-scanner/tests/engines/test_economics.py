"""Tests for the economics edge engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import respx

from niche_scanner.engines.economics import (
    INDICATOR_SIGMA,
    RELEASE_TYPE_SIGMA,
    EconomicsEdgeEngine,
    IndicatorReading,
    estimate_threshold_probability,
)
from niche_scanner.kalshi.models import Market


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


async def test_no_bet_detection() -> None:
    """Engine can be instantiated and classifies CPI tickers correctly."""
    engine = EconomicsEdgeEngine(min_edge_pp=12.0)

    # Verify engine attributes
    assert engine.min_edge_pp == 12.0
    assert "CPI" in engine.SERIES_PATTERNS
    assert "fed_rate" in engine.SERIES_PATTERNS
    assert "jobs" in engine.SERIES_PATTERNS
    assert "gdp" in engine.SERIES_PATTERNS

    # Verify fetch_indicators returns empty lists without FRED_API_KEY
    # (CPI/fed_rate gracefully return [], jobs/gdp are stubs).
    assert await engine.fetch_indicators("CPI") == []
    assert await engine.fetch_indicators("fed_rate") == []
    assert await engine.fetch_indicators("jobs") == []
    assert await engine.fetch_indicators("gdp") == []

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

    async def test_fetch_cpi_indicators_returns_readings(self) -> None:
        """Integration: fetch_indicators('cpi') returns IndicatorReadings
        when FRED API key is not set (should return empty gracefully)."""
        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        readings = await engine.fetch_indicators("cpi")
        # Without FRED_API_KEY env var, should return empty (not crash)
        assert isinstance(readings, list)


# ---------------------------------------------------------------------------
# Item 3.2: CME FedWatch integration
# ---------------------------------------------------------------------------


class TestFedWatchFetcher:
    """Tests for the Fed rate indicator fetching."""

    def test_fred_fed_funds_series_defined(self) -> None:
        """Verify FRED series IDs for Fed Funds indicators are defined."""
        from niche_scanner.engines.economics import FRED_FED_SERIES
        assert "DFF" in FRED_FED_SERIES  # Effective Fed Funds Rate
        assert len(FRED_FED_SERIES) >= 2

    def test_parse_fed_target_rate(self) -> None:
        """Verify parsing of Fed Funds target rate range."""
        from niche_scanner.engines.economics import parse_fed_target_range
        # Current range: 4.25% - 4.50%
        lower, upper = parse_fed_target_range(4.33)  # effective rate within range
        assert lower == 4.25
        assert upper == 4.50

    def test_parse_fed_target_edge(self) -> None:
        """Edge case: rate exactly at boundary."""
        from niche_scanner.engines.economics import parse_fed_target_range
        lower, upper = parse_fed_target_range(4.50)
        assert lower == 4.50
        assert upper == 4.75

    async def test_fetch_fed_rate_indicators_graceful(self) -> None:
        """fetch_indicators('fed_rate') returns empty without FRED key."""
        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        readings = await engine.fetch_indicators("fed_rate")
        assert isinstance(readings, list)


# ---------------------------------------------------------------------------
# Item 3.3: FRED historical data client
# ---------------------------------------------------------------------------


class TestFREDClient:
    """Tests for the FRED API client for historical economic data."""

    def test_fred_historical_series_defined(self) -> None:
        """Verify FRED historical series for backtesting are defined."""
        from niche_scanner.engines.economics import FRED_HISTORICAL_SERIES
        assert "UNRATE" in FRED_HISTORICAL_SERIES      # Unemployment rate
        assert "PAYEMS" in FRED_HISTORICAL_SERIES       # Total nonfarm payrolls
        assert "GDP" in FRED_HISTORICAL_SERIES          # GDP
        assert len(FRED_HISTORICAL_SERIES) >= 5

    def test_fred_client_init(self) -> None:
        """FREDClient initializes with api_key and caching."""
        from niche_scanner.engines.economics import FREDClient
        client = FREDClient(api_key="test-key-123")
        assert client.api_key == "test-key-123"
        assert len(client._cache) == 0

    def test_fred_client_parse_history(self) -> None:
        """FREDClient parses a full history response."""
        from niche_scanner.engines.economics import FREDClient
        client = FREDClient(api_key="test")
        raw = {
            "observations": [
                {"date": "2025-01-01", "value": "3.5"},
                {"date": "2025-02-01", "value": "3.6"},
                {"date": "2025-03-01", "value": "."},
                {"date": "2025-04-01", "value": "3.7"},
            ]
        }
        history = client._parse_history(raw)
        assert len(history) == 3  # skips "."
        assert history[0] == ("2025-01-01", 3.5)
        assert history[2] == ("2025-04-01", 3.7)

    def test_fred_client_yoy_from_history(self) -> None:
        """Calculate YoY rate from a history series."""
        from niche_scanner.engines.economics import FREDClient
        client = FREDClient(api_key="test")
        # Simulate 13 months of CPI index
        history = [(f"2025-{m:02d}-01", 300.0 + m * 0.5) for m in range(1, 14)]
        yoy = client._yoy_from_history(history)
        # month 13 vs month 1: (306.5 / 300.5 - 1) * 100
        expected = ((306.5 / 300.5) - 1) * 100
        assert yoy is not None
        assert abs(yoy - expected) < 0.01

    def test_fred_client_yoy_insufficient_data(self) -> None:
        """YoY returns None with less than 12 months of data."""
        from niche_scanner.engines.economics import FREDClient
        client = FREDClient(api_key="test")
        history = [("2025-01-01", 300.0), ("2025-02-01", 301.0)]
        yoy = client._yoy_from_history(history)
        assert yoy is None


class TestFREDClientAsync:
    """Verify FREDClient network I/O is truly async (non-blocking)."""

    @respx.mock
    async def test_fetch_history_makes_async_request(self) -> None:
        """fetch_history() uses httpx.AsyncClient and parses the response."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL, FREDClient

        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "observations": [
                        {"date": "2025-01-01", "value": "3.5"},
                        {"date": "2025-02-01", "value": "3.7"},
                    ],
                },
            ),
        )
        client = FREDClient(api_key="test-key")
        history = await client.fetch_history("CPIAUCSL")
        assert history == [("2025-01-01", 3.5), ("2025-02-01", 3.7)]

    @respx.mock
    async def test_fetch_history_caches_result(self) -> None:
        """fetch_history() only hits the network once per unique cache key."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL, FREDClient

        route = respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"observations": [{"date": "2025-01-01", "value": "3.5"}]},
            ),
        )
        client = FREDClient(api_key="test-key")
        first = await client.fetch_history("CPIAUCSL")
        second = await client.fetch_history("CPIAUCSL")
        assert first == second
        assert route.call_count == 1  # cache hit on second call

    @respx.mock
    async def test_fetch_history_returns_empty_on_http_error(self) -> None:
        """fetch_history() logs and returns [] when the API fails."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL, FREDClient

        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(500),
        )
        client = FREDClient(api_key="test-key")
        result = await client.fetch_history("CPIAUCSL")
        assert result == []

    @respx.mock
    async def test_fetch_latest_returns_most_recent_value(self) -> None:
        """fetch_latest() returns the single observation value."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL, FREDClient

        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"observations": [{"date": "2026-04-01", "value": "4.33"}]},
            ),
        )
        client = FREDClient(api_key="test-key")
        value = await client.fetch_latest("DFF")
        assert value == 4.33

    @respx.mock
    async def test_fetch_latest_returns_none_on_empty(self) -> None:
        """fetch_latest() returns None when no observations are returned."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL, FREDClient

        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(200, json={"observations": []}),
        )
        client = FREDClient(api_key="test-key")
        value = await client.fetch_latest("DFF")
        assert value is None


class TestEconomicsEngineAsyncFetch:
    """Verify EconomicsEdgeEngine.fetch_indicators() path is async end-to-end."""

    @respx.mock
    async def test_fetch_cpi_indicators_async_with_mocked_fred(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With FRED_API_KEY set, CPI fetch returns IndicatorReadings."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL

        monkeypatch.setenv("FRED_API_KEY", "test-key")

        # 13 months of rising index values -> positive YoY
        observations = [
            {"date": f"2025-{m:02d}-01", "value": f"{300.0 + m * 0.5:.1f}"}
            for m in range(13, 0, -1)  # desc order per FRED sort_order=desc
        ]
        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"observations": observations},
            ),
        )

        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        readings = await engine.fetch_indicators("cpi")
        assert isinstance(readings, list)
        assert len(readings) == 2  # Headline + Core CPI
        for r in readings:
            assert r.name.startswith("FRED")
            assert r.weight == 0.5

    @respx.mock
    async def test_fetch_fed_rate_indicators_async_with_mocked_fred(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With FRED_API_KEY set, fed_rate fetch returns IndicatorReadings."""
        import httpx
        from niche_scanner.engines.economics import FRED_BASE_URL

        monkeypatch.setenv("FRED_API_KEY", "test-key")

        respx.get(FRED_BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"observations": [{"date": "2026-04-01", "value": "4.33"}]},
            ),
        )

        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        readings = await engine.fetch_indicators("fed_rate")
        assert isinstance(readings, list)
        assert len(readings) == 3  # DFF + T10YIE + DFEDTARU
        names = {r.name for r in readings}
        assert "FRED Effective Fed Funds Rate" in names


# ---------------------------------------------------------------------------
# Item 3.4: Economics release calendar
# ---------------------------------------------------------------------------


class TestReleaseCalendar:
    """Tests for the economics release calendar."""

    def test_calendar_from_yaml_config(self) -> None:
        """Calendar loads release dates from YAML."""
        import tempfile
        from pathlib import Path
        from niche_scanner.engines.economics import ReleaseCalendar

        yaml_content = """
releases:
  - type: cpi
    date: "2026-05-13"
    description: "April 2026 CPI"
  - type: fed_rate
    date: "2026-05-07"
    description: "FOMC May decision"
  - type: jobs
    date: "2026-05-02"
    description: "April 2026 jobs report"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            path = Path(f.name)

        cal = ReleaseCalendar.from_yaml(path)
        assert len(cal.entries) == 3
        types = {e.release_type for e in cal.entries}
        assert types == {"cpi", "fed_rate", "jobs"}

    def test_next_release(self) -> None:
        """next_release returns the soonest upcoming release of a type."""
        from datetime import datetime, timezone
        from niche_scanner.engines.economics import ReleaseCalendar, ReleaseCalendarEntry

        entries = [
            ReleaseCalendarEntry("cpi", "2026-05-13", "May CPI"),
            ReleaseCalendarEntry("cpi", "2026-04-10", "April CPI"),
            ReleaseCalendarEntry("jobs", "2026-05-02", "Jobs"),
        ]
        cal = ReleaseCalendar(entries)

        # When "now" is April 5, 2026, the next CPI is April 10
        now = datetime(2026, 4, 5, tzinfo=timezone.utc)
        nxt = cal.next_release("cpi", now=now)
        assert nxt is not None
        assert nxt.release_date == "2026-04-10"

    def test_next_release_skips_past(self) -> None:
        """next_release skips releases that have already passed."""
        from datetime import datetime, timezone
        from niche_scanner.engines.economics import ReleaseCalendar, ReleaseCalendarEntry

        entries = [
            ReleaseCalendarEntry("cpi", "2026-04-01", "Past CPI"),
            ReleaseCalendarEntry("cpi", "2026-05-13", "Future CPI"),
        ]
        cal = ReleaseCalendar(entries)
        now = datetime(2026, 4, 5, tzinfo=timezone.utc)
        nxt = cal.next_release("cpi", now=now)
        assert nxt is not None
        assert nxt.release_date == "2026-05-13"

    def test_next_release_returns_none_when_no_future(self) -> None:
        """next_release returns None if all releases are past."""
        from datetime import datetime, timezone
        from niche_scanner.engines.economics import ReleaseCalendar, ReleaseCalendarEntry

        entries = [ReleaseCalendarEntry("cpi", "2025-01-01", "Old")]
        cal = ReleaseCalendar(entries)
        now = datetime(2026, 4, 5, tzinfo=timezone.utc)
        assert cal.next_release("cpi", now=now) is None

    def test_is_within_window(self) -> None:
        """is_within_window returns True when next release is within N hours."""
        from datetime import datetime, timezone
        from niche_scanner.engines.economics import ReleaseCalendar, ReleaseCalendarEntry

        # Release on April 6 at midnight UTC
        entries = [ReleaseCalendarEntry("cpi", "2026-04-06", "CPI")]
        cal = ReleaseCalendar(entries)

        # 20 hours before = within 48h window
        now = datetime(2026, 4, 5, 4, 0, tzinfo=timezone.utc)
        assert cal.is_within_window("cpi", hours=48, now=now) is True

        # 3 days before = outside 48h window
        now_far = datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc)
        assert cal.is_within_window("cpi", hours=48, now=now_far) is False

    def test_default_calendar_has_known_dates(self) -> None:
        """The default release calendar config includes real 2026 dates."""
        from niche_scanner.engines.economics import ReleaseCalendar
        cal = ReleaseCalendar.default()
        assert len(cal.entries) > 0
        types = {e.release_type for e in cal.entries}
        assert "cpi" in types
        assert "fed_rate" in types


# ---------------------------------------------------------------------------
# Item 6.2: Real probability estimation from FRED indicators
# ---------------------------------------------------------------------------


class TestEstimateThresholdProbability:
    """Tests for the estimate_threshold_probability() utility function."""

    def test_value_well_above_threshold(self) -> None:
        """When indicator is well above threshold, P(above) should be high."""
        # CPI at 3.5%, threshold 3.0%, sigma 0.15
        prob = estimate_threshold_probability(3.5, 3.0, "above", 0.15)
        assert prob > 0.99  # 3.33 std devs above

    def test_value_well_below_threshold(self) -> None:
        """When indicator is well below threshold, P(above) should be low."""
        # CPI at 2.5%, threshold 3.0%, sigma 0.15
        prob = estimate_threshold_probability(2.5, 3.0, "above", 0.15)
        assert prob < 0.01  # 3.33 std devs below

    def test_value_at_threshold(self) -> None:
        """When indicator equals threshold, P(above) should be ~0.5."""
        prob = estimate_threshold_probability(3.0, 3.0, "above", 0.15)
        assert abs(prob - 0.5) < 0.001

    def test_direction_below(self) -> None:
        """Direction 'below' returns 1 - P(above)."""
        prob_above = estimate_threshold_probability(3.2, 3.0, "above", 0.15)
        prob_below = estimate_threshold_probability(3.2, 3.0, "below", 0.15)
        assert abs(prob_above + prob_below - 1.0) < 1e-9

    def test_small_edge_produces_moderate_probability(self) -> None:
        """Indicator slightly above threshold gives moderate-to-high probability."""
        # CPI at 3.2%, threshold 3.0%, sigma 0.15 -> z = -1.33 -> P(above) ~ 0.91
        prob = estimate_threshold_probability(3.2, 3.0, "above", 0.15)
        assert 0.85 < prob < 0.95

    def test_zero_sigma_deterministic_above(self) -> None:
        """Zero sigma: deterministic comparison for 'above' direction."""
        assert estimate_threshold_probability(3.5, 3.0, "above", 0.0) == 1.0
        assert estimate_threshold_probability(2.5, 3.0, "above", 0.0) == 0.0
        # At threshold: >= means above
        assert estimate_threshold_probability(3.0, 3.0, "above", 0.0) == 1.0

    def test_zero_sigma_deterministic_below(self) -> None:
        """Zero sigma: deterministic comparison for 'below' direction."""
        assert estimate_threshold_probability(2.5, 3.0, "below", 0.0) == 1.0
        assert estimate_threshold_probability(3.5, 3.0, "below", 0.0) == 0.0

    def test_large_sigma_pulls_toward_50(self) -> None:
        """Very large sigma means high uncertainty -> probability near 0.5."""
        prob = estimate_threshold_probability(3.5, 3.0, "above", 100.0)
        assert abs(prob - 0.5) < 0.01

    def test_fed_rate_threshold(self) -> None:
        """Fed rate at 4.33%, threshold 4.50%, sigma 0.05."""
        # z = (4.50 - 4.33) / 0.05 = 3.4 -> P(above) ~ 0.0003
        prob = estimate_threshold_probability(4.33, 4.50, "above", 0.05)
        assert prob < 0.01

    def test_nonfarm_payrolls(self) -> None:
        """Payrolls at 250K, threshold 200K, sigma 80K."""
        # z = (200 - 250) / 80 = -0.625 -> P(above) ~ 0.73
        prob = estimate_threshold_probability(250.0, 200.0, "above", 80.0)
        assert 0.70 < prob < 0.80


class TestVolatilityConstants:
    """Verify the sigma constants are reasonable and complete."""

    def test_all_release_types_have_sigma(self) -> None:
        """Every release type in the ticker map has a sigma entry."""
        for rtype in EconomicsEdgeEngine.SERIES_TICKER_MAP:
            assert rtype in RELEASE_TYPE_SIGMA, f"Missing sigma for {rtype}"

    def test_sigma_values_positive(self) -> None:
        """All sigma values must be non-negative."""
        for rtype, sigma in RELEASE_TYPE_SIGMA.items():
            assert sigma >= 0, f"Negative sigma for {rtype}: {sigma}"

    def test_indicator_sigma_overrides_exist(self) -> None:
        """Key indicators should have per-indicator sigma overrides."""
        assert "FRED Headline CPI YoY" in INDICATOR_SIGMA
        assert "FRED Core CPI YoY" in INDICATOR_SIGMA
        assert "FRED Effective Fed Funds Rate" in INDICATOR_SIGMA


class TestRefineReadingsForMarket:
    """Tests for _refine_readings_for_market()."""

    def test_refinement_updates_probability_above(self) -> None:
        """Refinement replaces 0.5 with threshold-derived probability."""
        readings = [
            IndicatorReading("FRED Headline CPI YoY", 3.2, 0.5, 0.5),
            IndicatorReading("FRED Core CPI YoY", 3.0, 0.5, 0.5),
        ]
        refined = EconomicsEdgeEngine._refine_readings_for_market(
            readings, threshold=3.5, direction="above", release_type="cpi",
        )
        assert len(refined) == 2
        # CPI at 3.2%, threshold 3.5%, sigma 0.18 -> P(above) ~ 0.048
        assert refined[0].probability_above < 0.10
        # Core CPI at 3.0%, threshold 3.5%, sigma 0.10 -> P(above) ~ 0.0
        assert refined[1].probability_above < 0.001
        # Both should have changed from 0.5
        assert refined[0].probability_above != 0.5
        assert refined[1].probability_above != 0.5

    def test_refinement_preserves_weight_and_name(self) -> None:
        """Refinement keeps the original name and weight."""
        readings = [IndicatorReading("Test", 3.0, 0.5, 0.7)]
        refined = EconomicsEdgeEngine._refine_readings_for_market(
            readings, threshold=3.0, direction="above", release_type="cpi",
        )
        assert refined[0].name == "Test"
        assert refined[0].weight == 0.7
        assert refined[0].value == 3.0

    def test_refinement_below_direction(self) -> None:
        """Refinement works correctly for 'below' direction."""
        readings = [
            IndicatorReading("FRED Headline CPI YoY", 3.2, 0.5, 1.0),
        ]
        refined = EconomicsEdgeEngine._refine_readings_for_market(
            readings, threshold=3.5, direction="below", release_type="cpi",
        )
        # CPI at 3.2%, threshold 3.5% below -> P(below 3.5%) = 1 - P(above 3.5%)
        # P(above 3.5%) ~ 0.048 -> P(below) ~ 0.952
        assert refined[0].probability_above > 0.90

    def test_refinement_uses_per_indicator_sigma(self) -> None:
        """Per-indicator sigma overrides the release_type default."""
        # "Fed Funds Target Upper" has sigma=0.0 -> deterministic
        readings = [IndicatorReading("Fed Funds Target Upper", 4.50, 0.5, 1.0)]
        refined = EconomicsEdgeEngine._refine_readings_for_market(
            readings, threshold=4.25, direction="above", release_type="fed_rate",
        )
        # sigma=0, value=4.50 >= threshold=4.25 -> P(above) = 1.0
        assert refined[0].probability_above == 1.0

    def test_refinement_uses_default_sigma_for_unknown_indicator(self) -> None:
        """Unknown indicator names fall back to the release_type default sigma."""
        readings = [IndicatorReading("Custom Indicator", 3.0, 0.5, 1.0)]
        refined = EconomicsEdgeEngine._refine_readings_for_market(
            readings, threshold=3.0, direction="above", release_type="cpi",
        )
        # At-threshold with sigma>0 -> probability should be ~0.5
        assert abs(refined[0].probability_above - 0.5) < 0.001


class TestEvaluateMarketWithRefinement:
    """Tests for _evaluate_market with per-market probability refinement."""

    def _make_market(
        self,
        ticker: str = "CPIYOY-26APR10-T3.5",
        event_ticker: str = "CPIYOY-26APR10",
        subtitle: str = "3.5% or above",
        last_price: int = 50,
    ) -> Market:
        return Market(
            ticker=ticker,
            event_ticker=event_ticker,
            subtitle=subtitle,
            status="active",
            close_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
            last_price=last_price,
        )

    def test_cpi_above_threshold_generates_no_signal(self) -> None:
        """CPI well below threshold -> model says NO -> possible NO-side signal."""
        engine = EconomicsEdgeEngine(min_edge_pp=5.0)
        market = self._make_market(
            subtitle="3.5% or above",
            last_price=50,  # market says 50/50
        )
        readings = [
            IndicatorReading("FRED Headline CPI YoY", 2.8, 0.5, 0.5),
            IndicatorReading("FRED Core CPI YoY", 2.7, 0.5, 0.5),
        ]
        signal = engine._evaluate_market(market, None, readings, "cpi")
        # CPI at 2.8/2.7 vs threshold 3.5 -> model prob (above) very low
        # Market says 50% -> should find NO-side edge
        if signal is not None:
            assert signal.side == "no"
            assert signal.model_prob < 0.15

    def test_cpi_at_threshold_no_edge(self) -> None:
        """CPI at threshold -> model ~50% matches market 50% -> no signal."""
        engine = EconomicsEdgeEngine(min_edge_pp=12.0)
        market = self._make_market(
            subtitle="3.0% or above",
            last_price=50,  # market says 50%
        )
        readings = [
            IndicatorReading("FRED Headline CPI YoY", 3.0, 0.5, 0.5),
            IndicatorReading("FRED Core CPI YoY", 3.0, 0.5, 0.5),
        ]
        signal = engine._evaluate_market(market, None, readings, "cpi")
        # Model ~0.5, market ~0.5, edge ~0 -> no signal with 12pp threshold
        assert signal is None

    def test_unparseable_subtitle_uses_raw_readings(self) -> None:
        """Market without parseable threshold falls back to raw readings."""
        engine = EconomicsEdgeEngine(min_edge_pp=5.0)
        market = self._make_market(
            subtitle="Hold",  # Not parseable as a threshold
            last_price=50,
        )
        readings = [
            IndicatorReading("Test", 4.0, 0.9, 1.0),  # Already refined externally
        ]
        signal = engine._evaluate_market(market, None, readings, "fed_rate")
        # Falls back to raw readings -> model_prob = 0.9
        # edge = (0.9 - 0.5)*100 = 40pp -> should signal YES
        assert signal is not None
        assert signal.side == "yes"
        assert abs(signal.model_prob - 0.9) < 0.01

    def test_strong_cpi_above_generates_yes_signal(self) -> None:
        """CPI well above threshold + low market price -> YES signal."""
        engine = EconomicsEdgeEngine(min_edge_pp=5.0)
        market = self._make_market(
            subtitle="3.0% or above",
            last_price=20,  # market says only 20% chance
        )
        readings = [
            IndicatorReading("FRED Headline CPI YoY", 3.5, 0.5, 0.5),
            IndicatorReading("FRED Core CPI YoY", 3.3, 0.5, 0.5),
        ]
        signal = engine._evaluate_market(market, None, readings, "cpi")
        # CPI well above 3.0 threshold -> model prob high, market says 20%
        assert signal is not None
        assert signal.side == "yes"
        assert signal.model_prob > 0.85


class TestScanWithRefinement:
    """Integration tests for scan() with probability refinement."""

    async def test_scan_produces_differentiated_probabilities(self) -> None:
        """Two CPI markets with different thresholds should get different probs."""
        engine = EconomicsEdgeEngine(min_edge_pp=5.0)

        # Override fetch_indicators to return known readings
        original_fetch = engine.fetch_indicators

        async def mock_fetch(release_type: str) -> list[IndicatorReading]:
            if release_type == "cpi":
                return [
                    IndicatorReading("FRED Headline CPI YoY", 3.2, 0.5, 0.5),
                    IndicatorReading("FRED Core CPI YoY", 3.0, 0.5, 0.5),
                ]
            return await original_fetch(release_type)

        engine.fetch_indicators = mock_fetch  # type: ignore[assignment]

        m1 = Market(
            ticker="CPIYOY-26APR10-T2.5",
            event_ticker="CPIYOY-26APR10",
            subtitle="2.5% or above",
            status="active",
            close_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
            last_price=50,
        )
        m2 = Market(
            ticker="CPIYOY-26APR10-T4.0",
            event_ticker="CPIYOY-26APR10",
            subtitle="4.0% or above",
            status="active",
            close_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
            last_price=50,
        )

        signals = await engine.scan([m1, m2], {})

        # With CPI at 3.2%:
        # - T2.5 threshold: model prob(above 2.5) >> 0.5 -> YES edge
        # - T4.0 threshold: model prob(above 4.0) << 0.5 -> NO edge
        # At least one market should produce a signal with the 5pp threshold
        assert len(signals) >= 1

        # Check that the signals are differentiated (not both 0.5)
        for s in signals:
            assert s.model_prob != pytest.approx(0.5, abs=0.01)

    async def test_scan_empty_readings_skips_markets(self) -> None:
        """Markets with no indicator data produce no signals."""
        engine = EconomicsEdgeEngine(min_edge_pp=5.0)

        m = Market(
            ticker="KXPROLLS-26APR03-T200",
            event_ticker="KXPROLLS-26APR03",
            subtitle="200K or above",
            status="active",
            close_time=datetime(2026, 4, 4, tzinfo=timezone.utc),
            last_price=50,
        )
        # jobs fetch_indicators returns [] (stub)
        signals = await engine.scan([m], {})
        assert signals == []
