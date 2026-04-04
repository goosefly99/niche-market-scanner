"""Market scanner that orchestrates edge detection across all engines."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from niche_scanner.config import ICAOStations
from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.kalshi.models import OrderBook
from niche_scanner.sizing.kelly import KellySizer, PositionSize

if TYPE_CHECKING:
    from niche_scanner.alerts.telegram import AlertManager

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20


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
    """

    def __init__(
        self,
        client: KalshiClient,
        engines: list[EdgeEngine],
        sizer: KellySizer,
        trader: PaperTrader,
        icao_stations: ICAOStations | None = None,
        alert_manager: AlertManager | None = None,
        economics_series: list[str] | None = None,
    ) -> None:
        self._client = client
        self._engines = engines
        self._sizer = sizer
        self._trader = trader
        self._icao = icao_stations
        self._alert_manager = alert_manager
        self._economics_series = economics_series or []

    async def scan_cycle(self, bankroll_cents: int) -> list[EdgeSignal]:
        """Run one full scan cycle and return all detected signals.

        Steps
        -----
        1. Fetch active markets from Kalshi.
        2. Batch-fetch order books in chunks of 100.
        3. Run each engine's ``scan()`` method (errors are caught per-engine).
        4. Size each signal via Kelly and execute if sized.
        5. Track aggregate NO-side exposure across the cycle.
        6. Log a summary of the cycle.
        """
        # 1. Fetch markets from all configured series (weather + economics)
        from niche_scanner.kalshi.models import Market
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
        if not markets:
            logger.info("No markets found across any series")
            return []

        # 2. Batch-fetch order books in chunks of 100
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
            "Scan cycle complete: %d markets, %d signals, "
            "%d executed, %d skipped, NO exposure %d cents",
            len(markets),
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
