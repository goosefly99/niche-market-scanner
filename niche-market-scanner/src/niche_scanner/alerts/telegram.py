"""Telegram alert manager for edge signals, daily P&L, and scanner control.

Sends notifications for:
- Edge signal detections with sizing details
- Paper trade executions
- Daily P&L summaries per vertical
- Risk warnings and kill switch activation

Commands:
- /status — Scanner status (running engines, scan count, uptime)
- /positions — Current paper positions
- /stop — Pause scanner
- /start — Resume scanner
- /help — List commands

Requires TELEGRAM_NICHE_MARKET_SCANNER_BOT_API_KEY and TELEGRAM_NICHE_MARKET_SCANNER_BOT_CHAT_ID env vars.
When credentials are missing, all operations are no-ops (no crash).
"""

from __future__ import annotations

import logging

import httpx

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.sizing.kelly import PositionSize

logger = logging.getLogger(__name__)


class AlertManager:
    """Telegram-based alerting and command interface.

    Initializes in disabled mode when credentials are empty.
    All public methods are safe to call regardless of enabled state.

    The manager holds a single long-lived :class:`httpx.AsyncClient`
    (created lazily on first send) to reuse TCP connections and TLS
    sessions across alert messages, matching the pattern used by the
    weather and economics engines.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        paper_mode: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.paper_mode = paper_mode
        self.enabled = bool(bot_token and chat_id)
        self._http_client: httpx.AsyncClient | None = None

        if self.enabled:
            logger.info("Telegram alerts enabled (chat_id=%s)", chat_id)
        else:
            logger.info("Telegram alerts disabled (no credentials)")

    # ------------------------------------------------------------------
    # Message formatting
    # ------------------------------------------------------------------

    def format_signal(self, signal: EdgeSignal, size: PositionSize) -> str:
        """Format an edge signal + position size into a Telegram message."""
        prefix = "[PAPER] " if self.paper_mode else ""
        return (
            f"{prefix}Edge Signal\n"
            f"{'─' * 30}\n"
            f"Engine: {signal.engine}\n"
            f"Ticker: {signal.ticker}\n"
            f"Side: {signal.side.upper()}\n"
            f"Edge: {signal.edge_pp:.1f}pp "
            f"(fee-adj: {signal.fee_adjusted_edge:.1f}pp)\n"
            f"Model: {signal.model_prob:.1%} vs "
            f"Market: {signal.market_prob:.1%}\n"
            f"Contracts: {size.contracts} @ {size.price_cents}c\n"
            f"Cost: ${size.cost_cents / 100:.2f}\n"
            f"Kelly: {size.kelly_fraction_used:.2%}\n"
            f"Thesis: {signal.thesis}\n"
            f"Confidence: {signal.confidence:.0%}"
        )

    def format_daily_summary(self, stats: dict) -> str:
        """Format daily performance stats into a Telegram message."""
        prefix = "[PAPER] " if self.paper_mode else ""
        total = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        win_rate = stats.get("win_rate", 0.0)
        pnl = stats.get("net_pnl_cents", 0)

        return (
            f"{prefix}Daily Summary\n"
            f"{'─' * 30}\n"
            f"Trades: {total} trades\n"
            f"Win Rate: {win_rate:.0%}\n"
            f"P&L: ${pnl / 100:.2f}\n"
            f"Wins: {wins} | Losses: {stats.get('losses', 0)}"
        )

    def format_scan_summary(
        self,
        markets: int,
        signals: int,
        executed: int,
        skipped: int,
    ) -> str:
        """Format a scan cycle summary."""
        prefix = "[PAPER] " if self.paper_mode else ""
        return (
            f"{prefix}Scan Cycle\n"
            f"Markets: {markets} | Signals: {signals}\n"
            f"Executed: {executed} | Skipped: {skipped}"
        )

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def close(self) -> None:
        """Release HTTP resources. Safe to call multiple times."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> None:
        """Send a text message to the configured chat. No-op if disabled."""
        if not self.enabled:
            return
        await self._send(text)

    async def _send(self, text: str) -> None:
        """Send a message via the Telegram Bot API.

        Reuses a shared :class:`httpx.AsyncClient` so consecutive
        messages keep the TCP connection warm instead of opening and
        tearing down a new TLS session for each alert.
        """
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            client = self._get_http_client()
            resp = await client.post(
                url,
                json={"chat_id": self.chat_id, "text": text},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Telegram send failed: %d %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def send_signal_alert(
        self, signal: EdgeSignal, size: PositionSize,
    ) -> None:
        """Format and send an edge signal alert."""
        msg = self.format_signal(signal, size)
        await self.send_message(msg)

    async def send_daily_summary(self, stats: dict) -> None:
        """Format and send the daily performance summary."""
        msg = self.format_daily_summary(stats)
        await self.send_message(msg)

    async def send_scan_summary(
        self, markets: int, signals: int, executed: int, skipped: int,
    ) -> None:
        """Send a brief scan cycle summary (only when signals found)."""
        if signals > 0:
            msg = self.format_scan_summary(markets, signals, executed, skipped)
            await self.send_message(msg)

    async def send_risk_warning(self, message: str) -> None:
        """Send a risk/kill-switch alert."""
        await self.send_message(f"RISK WARNING\n{'─' * 30}\n{message}")

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def handle_command(self, command: str) -> str:
        """Process a Telegram command and return the response text.

        Commands:
            /status — Scanner running state
            /positions — Paper positions summary
            /stop — Pause scanning
            /start — Resume scanning
            /help — List available commands
        """
        cmd = command.strip().lower().split()[0] if command.strip() else ""

        if cmd == "/status":
            mode = "PAPER" if self.paper_mode else "LIVE"
            return f"Scanner status: Running ({mode} mode)"

        if cmd == "/positions":
            return "Positions: Use trade journal for details"

        if cmd == "/stop":
            return "Scanner paused (send /start to resume)"

        if cmd == "/start":
            return "Scanner resumed"

        if cmd == "/help":
            return (
                "Commands:\n"
                "/status — Scanner status\n"
                "/positions — Paper positions\n"
                "/stop — Pause scanner\n"
                "/start — Resume scanner\n"
                "/help — This message"
            )

        return f"Unknown command: {cmd}\nSend /help for available commands"
