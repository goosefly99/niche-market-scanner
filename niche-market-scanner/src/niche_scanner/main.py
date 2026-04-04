"""Main entry point for the niche-market-scanner."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from niche_scanner.alerts.telegram import AlertManager
from niche_scanner.config import (
    ICAOStations,
    KalshiConfig,
    ScannerSettings,
    TelegramConfig,
)
from niche_scanner.monitor.health import HealthMonitor
from niche_scanner.engines.base import EdgeEngine
from niche_scanner.engines.economics import EconomicsEdgeEngine
from niche_scanner.engines.weather import WeatherEdgeEngine
from niche_scanner.execution.paper_trader import PaperTrader
from niche_scanner.journal.trade_journal import TradeJournal
from niche_scanner.kalshi.auth import KalshiAuth
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.scanner.market_scanner import MarketScanner
from niche_scanner.sizing.kelly import KellySizer, SizingConfig

logger = logging.getLogger(__name__)

_DEFAULT_PAPER_BANKROLL_CENTS = 1_000_000
_DEFAULT_SCAN_INTERVAL_SEC = 60


async def main() -> None:
    """Load configuration, build components, and run the scan loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 1. Load configuration
    settings = ScannerSettings()
    kalshi_cfg = KalshiConfig()
    telegram_cfg = TelegramConfig()
    icao = ICAOStations()

    # 1b. Health monitor — validate startup before proceeding
    monitor = HealthMonitor(dead_man_timeout_sec=settings.scanner.get(
        "weather_interval_min", 15,
    ) * 60 * 3)  # 3x scan interval as dead-man timeout
    startup_errors = monitor.validate_startup()
    if startup_errors:
        logger.error(
            "Startup validation failed with %d error(s) — exiting",
            len(startup_errors),
        )
        return

    # 2. Create auth + client
    auth = KalshiAuth(
        api_key_id=kalshi_cfg.KALSHI_API_KEY_ID,
        private_key_path=kalshi_cfg.KALSHI_PRIVATE_KEY,
    )
    client = KalshiClient(
        base_url=kalshi_cfg.base_url,
        auth=auth,
        max_requests_per_second=10,
    )

    # 3. Create journal and initialize
    journal = TradeJournal(db_path="data/trades.db")
    await journal.initialize()

    # 4. Build sizing config and sizer
    sizing_data = settings.sizing
    sizing_config = SizingConfig(
        kelly_fraction=sizing_data.get("kelly_fraction", 0.5),
        min_edge_pp=sizing_data.get("min_edge_pp", 12.0),
        no_bet_per_position_cap=sizing_data.get("no_bet_per_position_cap", 0.02),
        no_bet_aggregate_cap=sizing_data.get("no_bet_aggregate_cap", 0.25),
        max_position_pct=sizing_data.get("max_position_pct", 0.05),
    )
    sizer = KellySizer(config=sizing_config)
    trader = PaperTrader(journal=journal)

    # 4b. Create Telegram alert manager
    paper_mode = sizing_data.get("paper_trade", True)
    alert_manager = AlertManager(
        bot_token=telegram_cfg.TELEGRAM_NICHE_MARKET_SCANNER_BOT_API_KEY,
        chat_id=telegram_cfg.TELEGRAM_NICHE_MARKET_SCANNER_BOT_CHAT_ID,
        paper_mode=paper_mode,
    )

    # 5. Build engine list from enabled verticals
    engines: list[EdgeEngine] = []

    # 6. Weather: only enable if there are verified ICAO stations
    if settings.is_vertical_enabled("weather"):
        verified = icao.all_verified()
        if verified:
            engines.append(
                WeatherEdgeEngine(
                    icao_stations=icao,
                    min_edge_pp=sizing_config.min_edge_pp,
                ),
            )
            logger.info(
                "Weather engine enabled with %d verified station(s)", len(verified),
            )
        else:
            logger.warning(
                "Weather vertical enabled but no verified ICAO stations; skipping",
            )

    if settings.is_vertical_enabled("economics"):
        engines.append(EconomicsEdgeEngine(min_edge_pp=sizing_config.min_edge_pp))
        logger.info("Economics engine enabled")

    if not engines:
        logger.warning("No engines enabled; scanner will produce no signals")

    # 7. Create scanner
    scanner = MarketScanner(
        client=client,
        engines=engines,
        sizer=sizer,
        trader=trader,
        icao_stations=icao,
        alert_manager=alert_manager,
    )

    # 8. Determine bankroll
    if paper_mode:
        bankroll_cents = _DEFAULT_PAPER_BANKROLL_CENTS
        logger.info("Paper trading mode: bankroll = %d cents", bankroll_cents)
    else:
        try:
            bankroll_cents = await client.get_balance()
            logger.info("Live bankroll: %d cents", bankroll_cents)
        except Exception:
            logger.exception("Failed to fetch balance; falling back to paper bankroll")
            bankroll_cents = _DEFAULT_PAPER_BANKROLL_CENTS

    # 9. Scan loop with graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)
    # On Windows, KeyboardInterrupt is raised directly by asyncio.

    scan_interval = settings.scanner.get(
        "weather_interval_min", _DEFAULT_SCAN_INTERVAL_SEC / 60,
    )
    interval_sec = float(scan_interval) * 60

    logger.info("Starting scan loop (interval=%.0fs)", interval_sec)
    monitor.heartbeat("main")
    try:
        while not shutdown_event.is_set():
            try:
                await scanner.scan_cycle(bankroll_cents)
                monitor.scan_loop_heartbeat()
                monitor.heartbeat("scanner")
            except Exception:
                logger.exception("Scan cycle failed")
                monitor.report_error("scanner", "Scan cycle exception")

            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=interval_sec,
                )
            except asyncio.TimeoutError:
                pass  # Normal: timeout means time for next cycle
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        # 11. Cleanup
        logger.info("Shutting down...")
        logger.info("Final health:\n%s", monitor.format_status())
        await client.close()
        await journal.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
