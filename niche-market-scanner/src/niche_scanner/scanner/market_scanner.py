"""Market scanner that orchestrates edge detection across all engines."""

from __future__ import annotations

import logging

from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.kalshi.models import OrderBook
from niche_scanner.sizing.kelly import KellySizer, PositionSize

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
    """

    def __init__(
        self,
        client: KalshiClient,
        engines: list[EdgeEngine],
        sizer: KellySizer,
        trader: PaperTrader,
    ) -> None:
        self._client = client
        self._engines = engines
        self._sizer = sizer
        self._trader = trader

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
        # 1. Fetch markets from target weather series
        from niche_scanner.kalshi.models import Market
        markets: list[Market] = []
        weather_series = [
            "KXHIGHNY", "KXHIGHCHI", "KXHIGHTBOS", "KXHIGHTHOU",
            "KXHIGHTDAL", "KXHIGHDEN", "KXHIGHLAX", "KXHIGHMIA",
            "KXHIGHTSEA", "KXHIGHTATL", "KXHIGHTPHX", "KXHIGHPHIL",
            "KXHIGHTSFO", "KXHIGHTDC", "KXHIGHAUS", "KXHIGHTSATX",
            "KXHIGHTLV", "KXHIGHTMIN", "KXHIGHTNOLA", "KXHIGHTOKC",
            "KXLOWTNYC", "KXLOWTCHI", "KXLOWTBOS", "KXLOWTHOU",
            "KXLOWTDAL", "KXLOWTDEN", "KXLOWTLAX", "KXLOWTMIA",
            "KXLOWTSEA", "KXLOWTATL", "KXLOWTPHX", "KXLOWTPHIL",
            "KXLOWTSFO", "KXLOWTDC",
        ]
        for series in weather_series:
            try:
                batch = await self._client.get_markets(series_ticker=series, limit=10)
                markets.extend(batch)
            except Exception:
                logger.debug("Series %s not found or empty", series)
        logger.info("Fetched %d markets from %d weather series", len(markets), len(weather_series))
        if not markets:
            logger.info("No weather markets found")
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

        return all_signals
