"""Market scanner that orchestrates edge detection across all engines."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from niche_scanner.config import ICAOStations, ScannerSettings
from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.engines.economics import ReleaseCalendar
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.kalshi.models import OrderBook
from niche_scanner.sizing.kelly import KellySizer, PositionSize

if TYPE_CHECKING:
    from niche_scanner.alerts.telegram import AlertManager
    from niche_scanner.kalshi.models import Market

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20

# Default cap for the broad market discovery fetch used by the
# thin-market engine.  Overridden via ``thin_market_discovery_limit``
# in the ``scanner`` section of settings.yaml.
_DEFAULT_DISCOVERY_LIMIT = 200


class MarketScanner:
    """Orchestrate a single scan cycle across all registered engines.

    Parameters
    ----------
    client:
        The Kalshi API client used to fetch markets and order books.
    engines:
        Edge-detection engines to run on each scan cycle.
    sizer:
        Kelly position sizer for computing trade sizes.
    trader:
        Paper (or live) trader that executes sized signals.
    icao_stations:
        ICAO station config providing series_tickers for weather scanning.
    alert_manager:
        Optional Telegram alert manager for sending signal and scan
        summary notifications. When ``None``, alerting is silently skipped.
    settings:
        Optional :class:`ScannerSettings` used to check vertical flags
        and read ``thin_market_discovery_limit``.  When ``None``,
        discovery is controlled solely by *discovery_limit*.
    economics_series:
        List of Kalshi series tickers for economics markets.
    release_calendar:
        Optional release calendar for dynamic scan interval switching.
        When provided, ``get_scan_interval_sec()`` returns the urgent
        interval if any tracked release type is within the configured
        window, otherwise the normal interval.
    discovery_limit:
        Maximum number of markets to fetch during broad discovery.
        Only used when the ``thin_market`` vertical is enabled (or
        when *settings* is ``None``).  Defaults to 200.
    normal_interval_sec:
        Default scan interval in seconds when no release is imminent.
    urgent_interval_sec:
        Shorter scan interval in seconds used when a release is imminent.
    urgent_window_hours:
        Hours before a release at which the scanner switches to
        the urgent interval.
    """

    def __init__(
        self,
        client: KalshiClient,
        engines: list[EdgeEngine],
        sizer: KellySizer,
        trader: PaperTrader,
        icao_stations: ICAOStations | None = None,
        alert_manager: AlertManager | None = None,
        settings: ScannerSettings | None = None,
        economics_series: list[str] | None = None,
        release_calendar: ReleaseCalendar | None = None,
        discovery_limit: int | None = None,
        normal_interval_sec: float = 600.0,
        urgent_interval_sec: float = 120.0,
        urgent_window_hours: float = 48.0,
    ) -> None:
        self._client = client
        self._engines = engines
        self._sizer = sizer
        self._trader = trader
        self._icao = icao_stations
        self._alert_manager = alert_manager
        self._settings = settings
        self._economics_series = economics_series or []
        self._release_calendar = release_calendar
        self._normal_interval_sec = normal_interval_sec
        self._urgent_interval_sec = urgent_interval_sec
        self._urgent_window_hours = urgent_window_hours

        # Resolve discovery limit: explicit arg > settings.yaml > default
        if discovery_limit is not None:
            self._discovery_limit = discovery_limit
        elif settings is not None:
            self._discovery_limit = int(
                settings.scanner.get(
                    "thin_market_discovery_limit", _DEFAULT_DISCOVERY_LIMIT,
                ),
            )
        else:
            self._discovery_limit = _DEFAULT_DISCOVERY_LIMIT

    def get_scan_interval_sec(self) -> float:
        """Return the current scan interval in seconds.

        When a ``ReleaseCalendar`` is configured and any tracked release
        type is within the urgent window, returns the urgent interval.
        Otherwise returns the normal interval.

        The release types checked are all top-level keys in the
        ``EconomicsEdgeEngine.SERIES_TICKER_MAP`` (cpi, fed_rate, jobs,
        gdp, unemployment, gas, recession, credit).
        """
        if self._release_calendar is None:
            return self._normal_interval_sec

        from niche_scanner.engines.economics import EconomicsEdgeEngine

        for release_type in EconomicsEdgeEngine.SERIES_TICKER_MAP:
            if self._release_calendar.is_within_window(
                release_type, hours=self._urgent_window_hours,
            ):
                logger.info(
                    "Release calendar: %s within %.0fh window — "
                    "using urgent interval (%.0fs)",
                    release_type,
                    self._urgent_window_hours,
                    self._urgent_interval_sec,
                )
                return self._urgent_interval_sec

        return self._normal_interval_sec

    def _discovery_enabled(self) -> bool:
        """Return ``True`` when the broad market discovery fetch should run.

        Discovery is gated on the ``thin_market`` vertical.  When no
        :class:`ScannerSettings` was provided at construction time the
        feature defaults to **off** to avoid unexpected API traffic.
        """
        if self._settings is None:
            return False
        return self._settings.is_vertical_enabled("thin_market")

    async def _fetch_discovery_markets(self) -> list[Market]:
        """Fetch open markets across all Kalshi categories.

        Pages through ``KalshiClient.get_active_markets`` until
        *discovery_limit* markets have been collected or the API
        returns no further results.
        """
        collected: list[Market] = []
        cursor: str | None = None
        remaining = self._discovery_limit

        while remaining > 0:
            page_size = min(remaining, 200)
            try:
                batch, next_cursor = await self._client.get_active_markets(
                    limit=page_size, cursor=cursor,
                )
            except Exception:
                logger.debug("Discovery fetch failed (cursor=%s)", cursor)
                break

            if not batch:
                break

            collected.extend(batch)
            remaining -= len(batch)

            if not next_cursor:
                break
            cursor = next_cursor

        return collected

    async def scan_cycle(self, bankroll_cents: int) -> list[EdgeSignal]:
        """Run one full scan cycle and return all detected signals.

        Steps
        -----
        1. Fetch active markets from Kalshi (weather + economics series).
        1b. Optionally fetch broad discovery markets for the thin-market
            engine and deduplicate against already-fetched tickers.
        2. Batch-fetch order books in chunks of 20.
        3. Run each engine's ``scan()`` method (errors are caught per-engine).
        4. Size each signal via Kelly and execute if sized.
        5. Track aggregate NO-side exposure across the cycle.
        6. Log a summary of the cycle.
        """
        # 1. Fetch markets from all configured series (weather + economics)
        markets: list[Market] = []

        # Weather series from ICAO config
        weather_tickers = self._icao.all_series_tickers() if self._icao else []
        for series in weather_tickers:
            try:
                batch = await self._client.get_markets(series_ticker=series, limit=10)
                markets.extend(batch)
            except Exception:
                logger.debug("Weather series %s not found or empty", series)

        # Economics series
        for series in self._economics_series:
            try:
                batch = await self._client.get_markets(series_ticker=series, limit=10)
                markets.extend(batch)
            except Exception:
                logger.debug("Economics series %s not found or empty", series)

        total_series = len(weather_tickers) + len(self._economics_series)
        logger.info(
            "Fetched %d markets from %d series (%d weather, %d economics)",
            len(markets), total_series,
            len(weather_tickers), len(self._economics_series),
        )

        # 1b. Broad market discovery for thin-market engine
        discovery_count = 0
        if self._discovery_enabled():
            known_tickers = {m.ticker for m in markets}
            discovery_markets = await self._fetch_discovery_markets()
            for dm in discovery_markets:
                if dm.ticker not in known_tickers:
                    markets.append(dm)
                    known_tickers.add(dm.ticker)
                    discovery_count += 1
            logger.info(
                "Discovery fetch: %d new markets added (%d total after dedup)",
                discovery_count,
                len(markets),
            )

        if not markets:
            logger.info("No markets found across any series")
            return []

        # 2. Batch-fetch order books in chunks of 20
        tickers = [m.ticker for m in markets]
        orderbooks: dict[str, OrderBook] = {}
        for i in range(0, len(tickers), _BATCH_SIZE):
            chunk = tickers[i : i + _BATCH_SIZE]
            batch = await self._client.get_batch_orderbooks(chunk)
            orderbooks.update(batch)

        # 3. Run each engine
        all_signals: list[EdgeSignal] = []
        for engine in self._engines:
            engine_name = type(engine).__name__
            try:
                signals = await engine.scan(markets, orderbooks)
                all_signals.extend(signals)
                logger.info(
                    "%s produced %d signal(s)", engine_name, len(signals),
                )
            except Exception:
                logger.exception("Engine %s failed", engine_name)

        # 4. Size and execute each signal, tracking NO exposure
        no_exposure_cents = 0
        executed_count = 0
        skipped_count = 0

        for signal in all_signals:
            no_exposure_pct = (
                no_exposure_cents / bankroll_cents if bankroll_cents > 0 else 0.0
            )
            size: PositionSize | None = self._sizer.size(
                signal, bankroll_cents, no_exposure_pct=no_exposure_pct,
            )
            if size is None:
                skipped_count += 1
                continue

            await self._trader.execute(signal, size)
            executed_count += 1

            # 4a. Send signal alert via Telegram (best-effort)
            if self._alert_manager is not None:
                try:
                    await self._alert_manager.send_signal_alert(signal, size)
                except Exception:
                    logger.exception("Failed to send signal alert for %s", signal.ticker)

            # 5. Track NO exposure
            if signal.side == "no":
                no_exposure_cents += size.cost_cents

        # 6. Log summary
        logger.info(
            "Scan cycle complete: %d markets (%d discovery), %d signals, "
            "%d executed, %d skipped, NO exposure %d cents",
            len(markets),
            discovery_count,
            len(all_signals),
            executed_count,
            skipped_count,
            no_exposure_cents,
        )

        # 6a. Send scan summary via Telegram (best-effort)
        if self._alert_manager is not None:
            try:
                await self._alert_manager.send_scan_summary(
                    markets=len(markets),
                    signals=len(all_signals),
                    executed=executed_count,
                    skipped=skipped_count,
                )
            except Exception:
                logger.exception("Failed to send scan summary alert")

        return all_signals
