"""Live trader — places real orders on Kalshi through the risk guard.

Every order passes through RiskGuard.check_order() before hitting the API.
If the risk guard rejects the order, it is logged but not placed.
If the risk guard kills trading, all further orders are blocked.

The live trader also records every trade to the journal for tracking.
"""

from __future__ import annotations

import logging

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.execution.risk_guard import RiskGuard
from niche_scanner.journal.trade_journal import TradeJournal, TradeRecord
from niche_scanner.kalshi.client import KalshiClient
from niche_scanner.sizing.kelly import PositionSize

logger = logging.getLogger(__name__)


class LiveTrader:
    """Execute real trades on Kalshi with risk guard protection.

    Every order is checked against the risk guard before execution.
    Rejected orders are logged but not placed. The journal records
    all attempts (including rejections) for audit trail.
    """

    def __init__(
        self,
        client: KalshiClient,
        journal: TradeJournal,
        risk_guard: RiskGuard,
    ) -> None:
        self._client = client
        self._journal = journal
        self._risk_guard = risk_guard
        self._signal_count = 0
        self._executed_count = 0
        self._rejected_count = 0
        self._total_cost_cents = 0

    @property
    def total_cost_cents(self) -> int:
        """Cumulative cost (in cents) of every successfully filled live order.

        Rejected and failed orders do not contribute — the counter
        increments only after :meth:`KalshiClient.create_order` returns
        a placed order.
        """
        return self._total_cost_cents

    async def execute(self, signal: EdgeSignal, size: PositionSize) -> int:
        """Attempt to place a real order. Returns trade journal ID.

        The order is checked against the risk guard first. If rejected,
        the trade is still logged to the journal with a rejection note
        in the thesis field, but no Kalshi order is placed.
        """
        self._signal_count += 1

        # Risk guard check — this is the safety gate
        rejection = self._risk_guard.check_order(
            ticker=signal.ticker,
            side=signal.side,
            contracts=size.contracts,
            price_cents=size.price_cents,
        )

        if rejection:
            self._rejected_count += 1
            logger.warning(
                "REJECTED %s %s %dx @ %dc: %s",
                signal.side.upper(), signal.ticker,
                size.contracts, size.price_cents, rejection,
            )
            # Record rejection in journal for audit
            record = TradeRecord(
                ticker=signal.ticker,
                engine=signal.engine,
                side=signal.side,
                action="rejected",
                contracts=size.contracts,
                price_cents=size.price_cents,
                model_prob=signal.model_prob,
                market_prob=signal.market_prob,
                edge_pp=signal.edge_pp,
                kelly_fraction=size.kelly_fraction_used,
                thesis=f"REJECTED: {rejection} | {signal.thesis}",
            )
            return await self._journal.record_trade(record)

        # Place the real order
        try:
            if signal.side == "yes":
                order = await self._client.create_order(
                    ticker=signal.ticker,
                    side="yes",
                    action="buy",
                    yes_price=size.price_cents,
                    no_price=0,
                    count=size.contracts,
                    time_in_force="ioc",  # Immediate-or-cancel for safety
                )
            else:
                order = await self._client.create_order(
                    ticker=signal.ticker,
                    side="no",
                    action="buy",
                    yes_price=0,
                    no_price=100 - size.price_cents,
                    count=size.contracts,
                    time_in_force="ioc",
                )

            # Record the fill in risk guard and journal
            self._risk_guard.record_fill(signal.ticker, size.cost_cents)
            self._executed_count += 1
            self._total_cost_cents += size.cost_cents

            logger.info(
                "LIVE %s %s %dx @ %dc = $%.2f  order_id=%s  edge=%.1fpp",
                signal.side.upper(), signal.ticker,
                size.contracts, size.price_cents,
                size.cost_cents / 100, order.order_id,
                signal.edge_pp,
            )

            record = TradeRecord(
                ticker=signal.ticker,
                engine=signal.engine,
                side=signal.side,
                action="buy",
                contracts=size.contracts,
                price_cents=size.price_cents,
                model_prob=signal.model_prob,
                market_prob=signal.market_prob,
                edge_pp=signal.edge_pp,
                kelly_fraction=size.kelly_fraction_used,
                thesis=f"LIVE order={order.order_id} | {signal.thesis}",
            )
            return await self._journal.record_trade(record)

        except Exception as e:
            # Order placement failed — kill switch as safety measure
            self._risk_guard.kill(f"Order placement failed: {e}")
            logger.exception(
                "LIVE ORDER FAILED for %s — kill switch activated",
                signal.ticker,
            )
            record = TradeRecord(
                ticker=signal.ticker,
                engine=signal.engine,
                side=signal.side,
                action="failed",
                contracts=size.contracts,
                price_cents=size.price_cents,
                model_prob=signal.model_prob,
                market_prob=signal.market_prob,
                edge_pp=signal.edge_pp,
                kelly_fraction=size.kelly_fraction_used,
                thesis=f"FAILED: {e} | {signal.thesis}",
            )
            return await self._journal.record_trade(record)

    def summary(self) -> dict:
        """Return running totals for this live trading session."""
        return {
            "total_signals": self._signal_count,
            "executed": self._executed_count,
            "rejected": self._rejected_count,
            "total_cost_cents": self._total_cost_cents,
            "risk_killed": self._risk_guard.is_killed,
            "kill_reason": self._risk_guard.kill_reason,
        }
