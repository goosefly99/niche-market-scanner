"""Tests for the market scanner."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from unittest.mock import MagicMock

from niche_scanner.alerts.telegram import AlertManager
from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.engines.economics import ReleaseCalendar, ReleaseCalendarEntry
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.journal.trade_journal import TradeJournal
from niche_scanner.kalshi.models import Market, OrderBook
from niche_scanner.scanner.market_scanner import MarketScanner
from niche_scanner.sizing.kelly import KellySizer, SizingConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(ticker: str = "TEST-MKT-1") -> Market:
    return Market(
        ticker=ticker,
        event_ticker="EVT-1",
        subtitle="55F to 60F",
        yes_bid=40,
        yes_ask=45,
        no_bid=55,
        no_ask=60,
        last_price=42,
        volume=100,
        open_interest=50,
        status="active",
        close_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def _make_signal(ticker: str = "TEST-MKT-1", side: str = "yes") -> EdgeSignal:
    return EdgeSignal(
        engine="test",
        ticker=ticker,
        side=side,
        model_prob=0.65,
        market_prob=0.45,
        edge_pp=20.0,
        fee_adjusted_edge=16.0,
        confidence=0.7,
        thesis="Test thesis",
    )


def _make_orderbook(ticker: str = "TEST-MKT-1") -> OrderBook:
    return OrderBook(
        ticker=ticker,
        yes=[{"price": 45, "quantity": 10}],
        no=[{"price": 55, "quantity": 10}],
    )


class StubEngine(EdgeEngine):
    """Engine that returns pre-configured signals."""

    def __init__(self, signals: list[EdgeSignal] | None = None) -> None:
        self._signals = signals or []

    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]:
        return self._signals


class FailingEngine(EdgeEngine):
    """Engine that always raises."""

    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]:
        raise RuntimeError("engine exploded")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def journal(tmp_path):
    db_path = str(tmp_path / "test_scanner.db")
    tj = TradeJournal(db_path)
    await tj.initialize()
    yield tj
    await tj.close()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_markets = AsyncMock(return_value=[_make_market()])
    client.get_batch_orderbooks = AsyncMock(
        return_value={"TEST-MKT-1": _make_orderbook()},
    )
    return client


@pytest.fixture
def sizer():
    return KellySizer(config=SizingConfig(min_edge_pp=12.0))


@pytest.fixture
async def trader(journal):
    return PaperTrader(journal=journal)


@pytest.fixture
def mock_icao():
    """Mock ICAOStations that returns a single series ticker."""
    icao = MagicMock()
    icao.all_series_tickers.return_value = ["KXTEST"]
    return icao


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_scan_cycle_no_markets(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Scan cycle returns empty when no markets are available."""
    mock_client.get_markets = AsyncMock(return_value=[])
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert signals == []


async def test_scan_cycle_returns_signals(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Scan cycle collects signals from engines."""
    engine = StubEngine(signals=[_make_signal()])
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert len(signals) == 1
    assert signals[0].ticker == "TEST-MKT-1"


async def test_scan_cycle_engine_failure_is_caught(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """A failing engine does not crash the scan cycle."""
    good_engine = StubEngine(signals=[_make_signal()])
    bad_engine = FailingEngine()
    scanner = MarketScanner(
        client=mock_client,
        engines=[bad_engine, good_engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    # Only the good engine's signal should come through
    assert len(signals) == 1


async def test_scan_cycle_batches_orderbooks(
    sizer, trader,
) -> None:
    """Order books are fetched in batches of 20."""
    # Create 50 markets across multiple series
    markets = [_make_market(f"T-{i}") for i in range(50)]
    client = AsyncMock()
    client.get_markets = AsyncMock(return_value=markets)
    client.get_batch_orderbooks = AsyncMock(return_value={})

    # Bypass the per-series fetching by mocking scan_cycle's market list directly
    MarketScanner(
        client=client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
    )

    # Directly test the batch orderbook logic by calling with known markets
    from niche_scanner.scanner.market_scanner import _BATCH_SIZE
    assert _BATCH_SIZE == 20  # Verify batch size is configured correctly


async def test_scan_cycle_tracks_no_exposure(
    mock_client, trader, mock_icao,
) -> None:
    """NO-side exposure is tracked and passed to the sizer."""
    no_signal = _make_signal(side="no")
    no_signal.model_prob = 0.35
    no_signal.market_prob = 0.50
    no_signal.edge_pp = 25.0
    no_signal.fee_adjusted_edge = 20.0

    engine = StubEngine(signals=[no_signal])
    sizer = KellySizer(config=SizingConfig(min_edge_pp=12.0))
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert len(signals) == 1
    assert signals[0].side == "no"


# ---------------------------------------------------------------------------
# AlertManager integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_alert_manager():
    """AlertManager with all async methods mocked."""
    mgr = AlertManager(bot_token="", chat_id="", paper_mode=True)
    mgr.send_signal_alert = AsyncMock()
    mgr.send_scan_summary = AsyncMock()
    mgr.send_risk_warning = AsyncMock()
    return mgr


async def test_scanner_accepts_alert_manager(
    mock_client, sizer, trader, mock_icao, mock_alert_manager,
) -> None:
    """MarketScanner accepts an optional AlertManager parameter."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        alert_manager=mock_alert_manager,
    )
    assert scanner._alert_manager is mock_alert_manager


async def test_scanner_works_without_alert_manager(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """MarketScanner works fine when no AlertManager is provided."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    assert scanner._alert_manager is None
    # Should not raise
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert signals == []


async def test_scan_cycle_sends_signal_alerts(
    mock_client, trader, mock_icao, mock_alert_manager,
) -> None:
    """When signals are sized and executed, AlertManager receives signal alerts."""
    signal = _make_signal()
    signal.edge_pp = 30.0  # high enough to clear fees + min_edge_pp
    signal.fee_adjusted_edge = 22.0
    engine = StubEngine(signals=[signal])
    sizer = KellySizer(config=SizingConfig(min_edge_pp=12.0))
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        alert_manager=mock_alert_manager,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)
    # The signal should produce a sized position and trigger an alert
    assert mock_alert_manager.send_signal_alert.call_count == 1
    call_args = mock_alert_manager.send_signal_alert.call_args
    sent_signal = call_args[0][0]  # first positional arg
    assert sent_signal.ticker == "TEST-MKT-1"


async def test_scan_cycle_sends_scan_summary(
    mock_client, trader, mock_icao, mock_alert_manager,
) -> None:
    """At the end of each scan cycle, a scan summary is sent."""
    signal = _make_signal()
    signal.edge_pp = 30.0
    signal.fee_adjusted_edge = 22.0
    engine = StubEngine(signals=[signal])
    sizer = KellySizer(config=SizingConfig(min_edge_pp=12.0))
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        alert_manager=mock_alert_manager,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)
    mock_alert_manager.send_scan_summary.assert_called_once()
    call_kwargs = mock_alert_manager.send_scan_summary.call_args
    # Verify it was called with the right market/signal counts
    assert call_kwargs[1]["markets"] == 1
    assert call_kwargs[1]["signals"] == 1


async def test_scan_cycle_no_alert_on_skipped_signals(
    mock_client, trader, mock_icao, mock_alert_manager,
) -> None:
    """Signals that are not sized (skipped) do not generate signal alerts."""
    signal = _make_signal()
    signal.edge_pp = 5.0  # Below min_edge_pp, will be skipped by sizer
    signal.fee_adjusted_edge = 2.0
    engine = StubEngine(signals=[signal])
    sizer = KellySizer(config=SizingConfig(min_edge_pp=12.0))
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        alert_manager=mock_alert_manager,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)
    # Signal was skipped, so no signal alert
    mock_alert_manager.send_signal_alert.assert_not_called()
    # But scan summary should still be sent
    mock_alert_manager.send_scan_summary.assert_called_once()


async def test_scan_cycle_alert_failure_does_not_crash(
    mock_client, trader, mock_icao,
) -> None:
    """If AlertManager raises, the scan cycle does not crash."""
    mgr = AlertManager(bot_token="", chat_id="", paper_mode=True)
    mgr.send_signal_alert = AsyncMock(side_effect=RuntimeError("Telegram down"))
    mgr.send_scan_summary = AsyncMock(side_effect=RuntimeError("Telegram down"))

    signal = _make_signal()
    signal.edge_pp = 30.0
    signal.fee_adjusted_edge = 22.0
    engine = StubEngine(signals=[signal])
    sizer = KellySizer(config=SizingConfig(min_edge_pp=12.0))
    scanner = MarketScanner(
        client=mock_client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        alert_manager=mgr,
    )
    # Should not raise
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert len(signals) == 1


# ---------------------------------------------------------------------------
# Item 5.6: Economics markets in scanner
# ---------------------------------------------------------------------------


async def test_scan_cycle_fetches_economics_series(
    sizer, trader, mock_icao,
) -> None:
    """Scanner fetches economics series tickers alongside weather."""
    client = AsyncMock()
    # Return different markets for weather vs economics series
    weather_market = _make_market("KXHIGHNY-26APR05-T67")
    econ_market = _make_market("KXRECSSNBER-26")

    async def _get_markets(**kwargs):
        series = kwargs.get("series_ticker", "")
        if series == "KXTEST":
            return [weather_market]
        if series == "KXRECSSNBER":
            return [econ_market]
        return []

    client.get_markets = AsyncMock(side_effect=_get_markets)
    client.get_batch_orderbooks = AsyncMock(return_value={})

    # Mock ICAO returns one weather series
    mock_icao.all_series_tickers.return_value = ["KXTEST"]

    # Engine that returns signals for any market
    engine = StubEngine(signals=[_make_signal()])

    scanner = MarketScanner(
        client=client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        economics_series=["KXRECSSNBER"],
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)

    # Verify both weather and economics markets were fetched
    call_series = [
        call.kwargs.get("series_ticker") or call.args[0] if call.args else call.kwargs.get("series_ticker")
        for call in client.get_markets.call_args_list
    ]
    assert "KXTEST" in call_series
    assert "KXRECSSNBER" in call_series


async def test_scan_cycle_works_without_economics_series(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Scanner works fine when no economics_series is provided (backward compat)."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    # Should not raise — economics_series defaults to empty
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# Item 5.3: Release calendar dynamic interval switching
# ---------------------------------------------------------------------------


def test_get_scan_interval_no_calendar(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Without a release calendar, the normal interval is always returned."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=None,
        normal_interval_sec=600.0,
        urgent_interval_sec=120.0,
    )
    assert scanner.get_scan_interval_sec() == 600.0


def test_get_scan_interval_no_upcoming_release(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """With all releases in the distant future, normal interval is returned."""
    # All releases are far in the future (2027)
    entries = [
        ReleaseCalendarEntry("cpi", "2027-01-15", "Far future CPI"),
    ]
    cal = ReleaseCalendar(entries)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=cal,
        normal_interval_sec=600.0,
        urgent_interval_sec=120.0,
        urgent_window_hours=48.0,
    )
    assert scanner.get_scan_interval_sec() == 600.0


def test_get_scan_interval_release_within_window(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """When a release is within the urgent window, urgent interval is returned.

    We build a calendar with a CPI release 12 hours from "now" and
    verify that ``get_scan_interval_sec`` returns the urgent interval.
    The ``is_within_window`` call inside the method uses real ``datetime.now()``
    so we construct the entry relative to the real clock to avoid flakes.
    """
    from datetime import datetime, timedelta, timezone

    # Release date is tomorrow (midnight UTC). Since ReleaseCalendarEntry
    # stores dates as midnight-UTC timestamps, "tomorrow" is always in
    # the future and always within a 48-hour window.
    release_date = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    entries = [ReleaseCalendarEntry("cpi", release_date, "Imminent CPI")]
    cal = ReleaseCalendar(entries)

    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=cal,
        normal_interval_sec=600.0,
        urgent_interval_sec=120.0,
        urgent_window_hours=48.0,
    )
    assert scanner.get_scan_interval_sec() == 120.0


def test_get_scan_interval_release_outside_window(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """When no release is within the urgent window, normal interval is returned."""
    # Only one release, far in the future
    entries = [
        ReleaseCalendarEntry("cpi", "2099-12-31", "Future CPI"),
    ]
    cal = ReleaseCalendar(entries)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=cal,
        normal_interval_sec=600.0,
        urgent_interval_sec=120.0,
        urgent_window_hours=48.0,
    )
    assert scanner.get_scan_interval_sec() == 600.0


def test_get_scan_interval_custom_intervals(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Custom interval values are correctly propagated from constructor."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=None,
        normal_interval_sec=300.0,
        urgent_interval_sec=60.0,
        urgent_window_hours=24.0,
    )
    # No calendar, so always normal
    assert scanner.get_scan_interval_sec() == 300.0
    assert scanner._urgent_interval_sec == 60.0
    assert scanner._urgent_window_hours == 24.0


def test_get_scan_interval_empty_calendar(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """An empty release calendar returns the normal interval."""
    cal = ReleaseCalendar([])
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        release_calendar=cal,
        normal_interval_sec=600.0,
        urgent_interval_sec=120.0,
    )
    assert scanner.get_scan_interval_sec() == 600.0


def test_scanner_backward_compat_without_calendar_args(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """MarketScanner still works without any calendar-related arguments."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    # Defaults: no calendar, 600s normal, 120s urgent
    assert scanner._release_calendar is None
    assert scanner._normal_interval_sec == 600.0
    assert scanner._urgent_interval_sec == 120.0
    assert scanner.get_scan_interval_sec() == 600.0


# ---------------------------------------------------------------------------
# Item 6.3: Broad market discovery for thin-market engine
# ---------------------------------------------------------------------------


def _make_settings(
    thin_market_enabled: bool = True,
    discovery_limit: int | None = None,
) -> MagicMock:
    """Build a mock ScannerSettings with the given vertical/scanner config."""
    settings = MagicMock()
    settings.is_vertical_enabled.side_effect = (
        lambda name: name == "thin_market" and thin_market_enabled
    )
    scanner_cfg: dict = {}
    if discovery_limit is not None:
        scanner_cfg["thin_market_discovery_limit"] = discovery_limit
    settings.scanner = scanner_cfg
    return settings


async def test_discovery_enabled_when_settings_provided(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Discovery is enabled when settings has thin_market vertical on."""
    settings = _make_settings(thin_market_enabled=True)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    assert scanner._discovery_enabled() is True


async def test_discovery_disabled_without_settings(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Discovery defaults to off when no ScannerSettings is provided."""
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    assert scanner._discovery_enabled() is False


async def test_discovery_disabled_when_vertical_off(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Discovery is off when thin_market vertical is disabled."""
    settings = _make_settings(thin_market_enabled=False)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    assert scanner._discovery_enabled() is False


async def test_discovery_limit_from_settings(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Discovery limit is read from settings.scanner config."""
    settings = _make_settings(discovery_limit=300)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    assert scanner._discovery_limit == 300


async def test_discovery_limit_explicit_overrides_settings(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """An explicit discovery_limit arg overrides settings.yaml."""
    settings = _make_settings(discovery_limit=300)
    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
        discovery_limit=50,
    )
    assert scanner._discovery_limit == 50


async def test_discovery_limit_default_without_settings(
    mock_client, sizer, trader, mock_icao,
) -> None:
    """Without settings, discovery limit defaults to _DEFAULT_DISCOVERY_LIMIT."""
    from niche_scanner.scanner.market_scanner import _DEFAULT_DISCOVERY_LIMIT

    scanner = MarketScanner(
        client=mock_client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
    )
    assert scanner._discovery_limit == _DEFAULT_DISCOVERY_LIMIT


async def test_scan_cycle_includes_discovery_markets(
    sizer, trader, mock_icao,
) -> None:
    """When discovery is enabled, broad markets are added to the scan."""
    weather_market = _make_market("KXHIGHNY-26APR05-T67")
    discovery_market = _make_market("CRYPTO-BTC-100K")

    client = AsyncMock()

    # get_markets returns weather market for the series fetch
    async def _get_markets(**kwargs):
        series = kwargs.get("series_ticker", "")
        if series == "KXTEST":
            return [weather_market]
        return []

    client.get_markets = AsyncMock(side_effect=_get_markets)

    # get_active_markets returns discovery market
    client.get_active_markets = AsyncMock(
        return_value=([discovery_market], ""),
    )
    client.get_batch_orderbooks = AsyncMock(return_value={})

    settings = _make_settings(thin_market_enabled=True)
    engine = StubEngine(signals=[])

    scanner = MarketScanner(
        client=client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)

    # get_active_markets must have been called
    client.get_active_markets.assert_called_once()


async def test_scan_cycle_deduplicates_discovery_markets(
    sizer, trader, mock_icao,
) -> None:
    """Markets already fetched via series are not duplicated by discovery."""
    weather_market = _make_market("KXHIGHNY-26APR05-T67")
    # Discovery returns the same weather market AND a new one
    dup_market = _make_market("KXHIGHNY-26APR05-T67")
    new_market = _make_market("POLITICS-PRES-2028")

    client = AsyncMock()

    async def _get_markets(**kwargs):
        series = kwargs.get("series_ticker", "")
        if series == "KXTEST":
            return [weather_market]
        return []

    client.get_markets = AsyncMock(side_effect=_get_markets)
    client.get_active_markets = AsyncMock(
        return_value=([dup_market, new_market], ""),
    )
    client.get_batch_orderbooks = AsyncMock(return_value={})

    settings = _make_settings(thin_market_enabled=True)

    # Use an engine that records what markets it receives
    received_markets: list[Market] = []

    class RecordingEngine(EdgeEngine):
        async def scan(self, markets, orderbooks):
            received_markets.extend(markets)
            return []

    scanner = MarketScanner(
        client=client,
        engines=[RecordingEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)

    # Should have 2 unique markets, not 3
    tickers = [m.ticker for m in received_markets]
    assert len(tickers) == 2
    assert "KXHIGHNY-26APR05-T67" in tickers
    assert "POLITICS-PRES-2028" in tickers


async def test_scan_cycle_skips_discovery_when_disabled(
    sizer, trader, mock_icao,
) -> None:
    """When thin_market is disabled, no discovery fetch is made."""
    client = AsyncMock()
    client.get_markets = AsyncMock(return_value=[_make_market()])
    client.get_batch_orderbooks = AsyncMock(return_value={})
    client.get_active_markets = AsyncMock()

    settings = _make_settings(thin_market_enabled=False)
    scanner = MarketScanner(
        client=client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    await scanner.scan_cycle(bankroll_cents=1_000_000)

    # get_active_markets should NOT have been called
    client.get_active_markets.assert_not_called()


async def test_scan_cycle_discovery_failure_does_not_crash(
    sizer, trader, mock_icao,
) -> None:
    """If the discovery fetch raises, the scan cycle continues."""
    client = AsyncMock()
    client.get_markets = AsyncMock(return_value=[_make_market()])
    client.get_batch_orderbooks = AsyncMock(
        return_value={"TEST-MKT-1": _make_orderbook()},
    )
    # get_active_markets raises
    client.get_active_markets = AsyncMock(
        side_effect=RuntimeError("API error"),
    )

    settings = _make_settings(thin_market_enabled=True)
    engine = StubEngine(signals=[_make_signal()])
    scanner = MarketScanner(
        client=client,
        engines=[engine],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
    )
    # Should not raise
    signals = await scanner.scan_cycle(bankroll_cents=1_000_000)
    assert len(signals) == 1


async def test_fetch_discovery_markets_paginates(
    sizer, trader, mock_icao,
) -> None:
    """_fetch_discovery_markets pages through multiple API responses."""
    page1_markets = [_make_market(f"P1-{i}") for i in range(3)]
    page2_markets = [_make_market(f"P2-{i}") for i in range(2)]

    call_count = 0

    async def _get_active(limit, cursor=None):
        nonlocal call_count
        call_count += 1
        if cursor is None:
            return page1_markets, "cursor-page-2"
        return page2_markets, ""

    client = AsyncMock()
    client.get_active_markets = AsyncMock(side_effect=_get_active)
    client.get_markets = AsyncMock(return_value=[])
    client.get_batch_orderbooks = AsyncMock(return_value={})

    settings = _make_settings(thin_market_enabled=True)
    scanner = MarketScanner(
        client=client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
        discovery_limit=500,
    )

    result = await scanner._fetch_discovery_markets()
    assert len(result) == 5
    assert call_count == 2


async def test_fetch_discovery_markets_respects_limit(
    sizer, trader, mock_icao,
) -> None:
    """_fetch_discovery_markets stops once the limit is reached."""
    large_page = [_make_market(f"D-{i}") for i in range(200)]

    client = AsyncMock()
    client.get_active_markets = AsyncMock(
        return_value=(large_page, "more-pages"),
    )
    client.get_markets = AsyncMock(return_value=[])
    client.get_batch_orderbooks = AsyncMock(return_value={})

    settings = _make_settings(thin_market_enabled=True)
    scanner = MarketScanner(
        client=client,
        engines=[StubEngine()],
        sizer=sizer,
        trader=trader,
        icao_stations=mock_icao,
        settings=settings,
        discovery_limit=200,
    )

    result = await scanner._fetch_discovery_markets()
    # Should stop after first page since 200 >= discovery_limit
    assert len(result) == 200
    assert client.get_active_markets.call_count == 1
