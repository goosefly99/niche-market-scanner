"""Market scanner that orchestrates edge detection across all engines."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from niche_scanner.config import ICAOStations, ScannerSettings
from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.engines.economics import ReleaseCalendar
from niche_scanner.execution.base import Trader
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.kalshi.models import Market, OrderBook
from niche_scanner.sizing.kelly import KellySizer, PositionSize

if TYPE_CHECKING:
    from niche_scanner.alerts.telegram import AlertManager

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20

# Default cap for the broad market discovery fetch used by the
# thin-market engine.  Overridden via ``thin_market_discovery_limit``
# in the ``scanner`` section of settings.yaml.
_DEFAULT_DISCOVERY_LIMIT = 200


@dataclass(frozen=True)
class ScanCycleStats:
    """Summary metrics captured during a single scan cycle.

    Populated at the end of :meth:`MarketScanner.scan_cycle` and exposed
    via :pyattr:`MarketScanner.last_cycle_stats`.  Consumed by the
    dashboard's ``ScanCycleLogger`` to persist cycle history.

    All monetary values are in cents.  ``duration_ms`` is measured via
    :func:`time.monotonic` around the body of ``scan_cycle``.
    """

    markets_scanned: int
    signals_found: int
    executed: int
    skipped: int
    no_exposure_cents: int
    duration_ms: int


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
        Paper or live trader that executes sized signals.  Typed
        against :class:`Trader` so ``PaperTrader`` and ``LiveTrader``
        are structurally interchangeable.
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
        trader: Trader,
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

        # Stats snapshot from the most recent ``scan_cycle`` call.  ``None``
        # until the first cycle completes.  Read by consumers (e.g. the
        # dashboard's ``ScanCycleLogger``) after each cycle.
        self._last_cycle_stats: ScanCycleStats | None = None

    @property
    def last_cycle_stats(self) -> ScanCycleStats | None:
        """Return metrics from the most recent completed scan cycle.

        Returns ``None`` until ``scan_cycle`` has been called at least
        once.  The snapshot is replaced at the end of every cycle.
        """
        return self._last_cycle_stats

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

    async def _fetch_series_markets(
        self,
        weather_tickers: list[str],
        economics_series: list[str],
    ) -> list[Market]:
        """Fetch markets for every configured series concurrently.

        Each series is fetched via one ``get_markets`` call.  All fetches
        are dispatched through ``asyncio.gather`` with
        ``return_exceptions=True`` so a single failing series (common —
        unknown or empty series return HTTP errors from Kalshi) does not
        abort the whole cycle.  Rate limiting is enforced by the
        underlying :class:`KalshiClient` semaphore.

        Results are concatenated in the order ``weather_tickers`` first,
        then ``economics_series``, matching the prior sequential
        implementation.
        """
        if not weather_tickers and not economics_series:
            return []

        # Launch one coroutine per series.  We tag each with its label
        # ("weather"/"economics") and ticker so debug logging on failure
        # mirrors the original sequential loop.
        labels: list[tuple[str, str]] = []
        tasks: list = []
        for series in weather_tickers:
            labels.append(("Weather", series))
            tasks.append(
                self._client.get_markets(series_ticker=series, limit=10),
            )
        for series in economics_series:
            labels.append(("Economics", series))
            tasks.append(
                self._client.get_markets(series_ticker=series, limit=10),
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        markets: list[Market] = []
        for (label, series), result in zip(labels, results):
            if isinstance(result, BaseException):
                logger.debug(
                    "%s series %s not found or empty", label, series,
                )
                continue
            markets.extend(result)

        return markets

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

        On completion, :pyattr:`last_cycle_stats` is populated with the
        per-cycle metrics (markets scanned, signals, executed, skipped,
        NO exposure, duration).  The snapshot is replaced on every call.
        """
        cycle_start = time.monotonic()

        # 1. Fetch markets from all configured series (weather + economics).
        #
        # Series fetches are dispatched concurrently via ``asyncio.gather``.
        # ``KalshiClient`` already bounds concurrency with an internal
        # semaphore (``max_requests_per_second``), so we can safely launch
        # all per-series fetches at once without exceeding the API rate
        # limit.  Per-series failures are caught via ``return_exceptions``
        # and logged at debug level (matching the prior sequential
        # behaviour where empty/unknown series are expected).
        weather_tickers = self._icao.all_series_tickers() if self._icao else []
        markets = await self._fetch_series_markets(
            weather_tickers, self._economics_series,
        )

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
            self._last_cycle_stats = ScanCycleStats(
                markets_scanned=0,
                signals_found=0,
                executed=0,
                skipped=0,
                no_exposure_cents=0,
                duration_ms=int((time.monotonic() - cycle_start) * 1000),
            )
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

        # 7. Snapshot cycle stats for consumers (dashboard, etc.)
        self._last_cycle_stats = ScanCycleStats(
            markets_scanned=len(markets),
            signals_found=len(all_signals),
            executed=executed_count,
            skipped=skipped_count,
            no_exposure_cents=no_exposure_cents,
            duration_ms=int((time.monotonic() - cycle_start) * 1000),
        )

        return all_signals
