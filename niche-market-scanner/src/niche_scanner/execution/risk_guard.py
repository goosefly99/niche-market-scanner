"""Risk guard — enforces hard limits on live trading and provides a kill switch.

This is the last line of defense before real money leaves the account.
Every order must pass through the RiskGuard before execution. If any
check fails, the order is rejected and (optionally) the entire scanner
is shut down.

Kill switch triggers:
- Portfolio drawdown exceeds max_portfolio_drawdown
- Single position loss exceeds position_stop_loss
- Daily loss exceeds max_daily_loss
- Manual kill via kill() method
- Any unexpected error during risk checks (fail-closed)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LiveTradingConfig:
    """Configuration for live trading risk limits.

    All monetary values are in cents. All percentages are decimals (0.05 = 5%).
    """

    enabled: bool = False  # Master switch — must be explicitly True
    starting_cash_cents: int = 100_000  # $1,000 default
    max_bet_size_cents: int = 1_000  # $10 max per trade
    max_buy_usd_cum_cents: int = 10_000  # $100 cumulative spend cap (all positions)
    max_portfolio_drawdown: float = 0.15  # 15% from peak
    max_daily_loss_cents: int = 5_000  # $50/day
    position_stop_loss: float = 0.50  # 50% of position value
    max_open_positions: int = 10
    max_daily_trades: int = 50
    require_confirmation: bool = True  # Require human confirmation for first trade

    @classmethod
    def from_dict(cls, data: dict) -> LiveTradingConfig:
        """Create from a settings dict, converting dollar amounts to cents."""
        # Support max_buy_usd_cum in dollars (convert to cents)
        max_buy_usd = data.get("max_buy_usd_cum", None)
        max_buy_cents = int(max_buy_usd * 100) if max_buy_usd is not None else int(
            data.get("max_buy_usd_cum_cents", 10_000)
        )
        return cls(
            enabled=data.get("enabled", False),
            starting_cash_cents=int(data.get("starting_cash_cents", 100_000)),
            max_bet_size_cents=int(data.get("max_bet_size_cents", 1_000)),
            max_buy_usd_cum_cents=max_buy_cents,
            max_portfolio_drawdown=float(data.get("max_portfolio_drawdown", 0.15)),
            max_daily_loss_cents=int(data.get("max_daily_loss_cents", 5_000)),
            position_stop_loss=float(data.get("position_stop_loss", 0.50)),
            max_open_positions=int(data.get("max_open_positions", 10)),
            max_daily_trades=int(data.get("max_daily_trades", 50)),
            require_confirmation=data.get("require_confirmation", True),
        )


@dataclass
class RiskState:
    """Mutable state tracked by the risk guard."""

    peak_balance_cents: int = 0
    current_balance_cents: int = 0
    cumulative_spend_cents: int = 0  # Running total of all position entries
    daily_loss_cents: int = 0
    daily_trade_count: int = 0
    open_position_count: int = 0
    day_start_balance_cents: int = 0
    day_start_timestamp: float = 0.0
    killed: bool = False
    kill_reason: str = ""
    positions: dict[str, int] = field(default_factory=dict)  # ticker -> cost_cents


class RiskGuard:
    """Enforces hard limits on live trading. Fail-closed design.

    Every order passes through ``check_order()`` before execution.
    If any limit is breached, the order is rejected. If a critical
    threshold is hit (drawdown, daily loss), the kill switch fires
    and ALL further trading is halted until manual reset.

    Usage::

        guard = RiskGuard(config, initial_balance_cents=149942)
        rejection = guard.check_order(ticker, side, contracts, price_cents)
        if rejection:
            logger.warning("Order rejected: %s", rejection)
        else:
            # Safe to execute
            order = await client.create_order(...)
            guard.record_fill(ticker, cost_cents)
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        initial_balance_cents: int = 0,
    ) -> None:
        self.config = config
        self.state = RiskState(
            peak_balance_cents=initial_balance_cents,
            current_balance_cents=initial_balance_cents,
            day_start_balance_cents=initial_balance_cents,
            day_start_timestamp=time.time(),
        )

    @property
    def is_killed(self) -> bool:
        return self.state.killed

    @property
    def kill_reason(self) -> str:
        return self.state.kill_reason

    def kill(self, reason: str) -> None:
        """Activate the kill switch. All further orders are rejected."""
        self.state.killed = True
        self.state.kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(self) -> None:
        """Manually reset the kill switch. Use with caution."""
        self.state.killed = False
        self.state.kill_reason = ""
        logger.warning("Kill switch manually reset")

    def check_order(
        self,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
    ) -> str | None:
        """Check whether an order should be allowed.

        Returns None if the order passes all checks, or a rejection
        reason string if it should be blocked.

        This method is fail-closed: any unexpected error results in
        rejection and kill switch activation.
        """
        try:
            return self._check_order_inner(ticker, side, contracts, price_cents)
        except Exception as e:
            reason = f"Unexpected error in risk check: {e}"
            self.kill(reason)
            return reason

    def _check_order_inner(
        self,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
    ) -> str | None:
        # 1. Kill switch
        if self.state.killed:
            return f"Kill switch active: {self.state.kill_reason}"

        # 2. Live trading enabled
        if not self.config.enabled:
            return "Live trading not enabled in config"

        # 3. Bet size limit
        cost_cents = contracts * price_cents
        if cost_cents > self.config.max_bet_size_cents:
            return (
                f"Bet size ${cost_cents/100:.2f} exceeds max "
                f"${self.config.max_bet_size_cents/100:.2f}"
            )

        # 3b. Cumulative spend cap
        new_cumulative = self.state.cumulative_spend_cents + cost_cents
        if new_cumulative > self.config.max_buy_usd_cum_cents:
            return (
                f"Cumulative spend ${new_cumulative/100:.2f} would exceed cap "
                f"${self.config.max_buy_usd_cum_cents/100:.2f} "
                f"(already spent ${self.state.cumulative_spend_cents/100:.2f})"
            )

        # 4. Daily trade count
        self._maybe_reset_daily_counters()
        if self.state.daily_trade_count >= self.config.max_daily_trades:
            return f"Daily trade limit reached ({self.config.max_daily_trades})"

        # 5. Open positions limit
        if self.state.open_position_count >= self.config.max_open_positions:
            return f"Max open positions reached ({self.config.max_open_positions})"

        # 6. Daily loss limit
        if self.state.daily_loss_cents >= self.config.max_daily_loss_cents:
            self.kill(
                f"Daily loss limit reached: "
                f"${self.state.daily_loss_cents/100:.2f} >= "
                f"${self.config.max_daily_loss_cents/100:.2f}"
            )
            return self.state.kill_reason

        # 7. Portfolio drawdown from peak
        if self.state.peak_balance_cents > 0:
            drawdown = 1.0 - (
                self.state.current_balance_cents / self.state.peak_balance_cents
            )
            if drawdown >= self.config.max_portfolio_drawdown:
                self.kill(
                    f"Portfolio drawdown {drawdown:.1%} >= "
                    f"max {self.config.max_portfolio_drawdown:.1%} "
                    f"(peak ${self.state.peak_balance_cents/100:.2f}, "
                    f"current ${self.state.current_balance_cents/100:.2f})"
                )
                return self.state.kill_reason

        # 8. Sufficient balance
        if cost_cents > self.state.current_balance_cents:
            return (
                f"Insufficient balance: need ${cost_cents/100:.2f}, "
                f"have ${self.state.current_balance_cents/100:.2f}"
            )

        return None  # All checks passed

    def record_fill(self, ticker: str, cost_cents: int) -> None:
        """Record that an order was filled."""
        self.state.daily_trade_count += 1
        self.state.current_balance_cents -= cost_cents
        self.state.cumulative_spend_cents += cost_cents
        self.state.open_position_count += 1
        self.state.positions[ticker] = self.state.positions.get(ticker, 0) + cost_cents

    def record_settlement(self, ticker: str, payout_cents: int) -> None:
        """Record a position settlement (win or loss)."""
        cost = self.state.positions.pop(ticker, 0)
        self.state.current_balance_cents += payout_cents
        self.state.open_position_count = max(0, self.state.open_position_count - 1)

        # Track peak balance
        if self.state.current_balance_cents > self.state.peak_balance_cents:
            self.state.peak_balance_cents = self.state.current_balance_cents

        # Track daily P&L
        pnl = payout_cents - cost
        if pnl < 0:
            self.state.daily_loss_cents += abs(pnl)

    def update_balance(self, balance_cents: int) -> None:
        """Sync balance from Kalshi API (call periodically)."""
        self.state.current_balance_cents = balance_cents
        if balance_cents > self.state.peak_balance_cents:
            self.state.peak_balance_cents = balance_cents

    def _maybe_reset_daily_counters(self) -> None:
        """Reset daily counters if a new day has started (UTC)."""
        import datetime
        now = time.time()
        today_start = datetime.datetime.now(
            datetime.timezone.utc
        ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        if self.state.day_start_timestamp < today_start:
            self.state.daily_loss_cents = 0
            self.state.daily_trade_count = 0
            self.state.day_start_balance_cents = self.state.current_balance_cents
            self.state.day_start_timestamp = now

    def format_status(self) -> str:
        """Human-readable risk status."""
        s = self.state
        c = self.config
        drawdown = (
            1.0 - s.current_balance_cents / s.peak_balance_cents
            if s.peak_balance_cents > 0 else 0.0
        )
        killed_str = f"KILLED: {s.kill_reason}" if s.killed else "Active"
        return (
            f"Risk Guard: {killed_str}\n"
            f"  Balance: ${s.current_balance_cents/100:.2f} "
            f"(peak ${s.peak_balance_cents/100:.2f}, "
            f"drawdown {drawdown:.1%}/{c.max_portfolio_drawdown:.1%})\n"
            f"  Cumulative spend: ${s.cumulative_spend_cents/100:.2f}"
            f"/${c.max_buy_usd_cum_cents/100:.2f}\n"
            f"  Daily: {s.daily_trade_count}/{c.max_daily_trades} trades, "
            f"${s.daily_loss_cents/100:.2f}/${c.max_daily_loss_cents/100:.2f} loss\n"
            f"  Positions: {s.open_position_count}/{c.max_open_positions}\n"
            f"  Max bet: ${c.max_bet_size_cents/100:.2f}"
        )
