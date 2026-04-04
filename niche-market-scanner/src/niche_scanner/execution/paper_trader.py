"""Paper trader that records simulated trades to the journal."""

from __future__ import annotations

import logging

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.journal.trade_journal import TradeJournal, TradeRecord
from niche_scanner.sizing.kelly import PositionSize

logger = logging.getLogger(__name__)


class PaperTrader:
    """Execute paper trades by recording them to a :class:`TradeJournal`.

    No real orders are placed; this is purely for backtesting / dry-run
    evaluation of the scanning pipeline.
    """

    def __init__(self, journal: TradeJournal) -> None:
        self._journal = journal
        self._signal_count = 0
        self._total_cost_cents = 0

    async def execute(self, signal: EdgeSignal, size: PositionSize) -> int:
        """Record a paper trade and return the trade ID.

        Parameters
        ----------
        signal:
            The edge signal that triggered the trade.
        size:
            The position sizing output from the Kelly sizer.
        """
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
            thesis=signal.thesis,
        )
        trade_id = await self._journal.record_trade(record)

        self._signal_count += 1
        self._total_cost_cents += size.cost_cents

        logger.info(
            "PAPER %s %s %dx @ %dc  edge=%.1fpp  id=%d",
            signal.side.upper(),
            signal.ticker,
            size.contracts,
            size.price_cents,
            signal.edge_pp,
            trade_id,
        )
        return trade_id

    def summary(self) -> dict:
        """Return running totals for this paper-trading session."""
        return {
            "total_signals": self._signal_count,
            "total_cost_cents": self._total_cost_cents,
        }
