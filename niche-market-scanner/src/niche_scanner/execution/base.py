"""Shared execution contracts for paper and live traders.

Both :class:`~niche_scanner.execution.paper_trader.PaperTrader` and
:class:`~niche_scanner.execution.live_trader.LiveTrader` implement the
:class:`Trader` protocol defined here.  Scanner and orchestration code
should depend on :class:`Trader` (not on the concrete classes) so that
paper and live modes remain structurally interchangeable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.sizing.kelly import PositionSize


@runtime_checkable
class Trader(Protocol):
    """Minimum interface any trader must provide.

    Implementations:
      - :class:`~niche_scanner.execution.paper_trader.PaperTrader`
      - :class:`~niche_scanner.execution.live_trader.LiveTrader`

    Consumers (``MarketScanner``, ``main.py``) should type-hint against
    this protocol rather than the concrete classes so live and paper
    modes remain interchangeable.
    """

    @property
    def total_cost_cents(self) -> int:
        """Cumulative cost (in cents) of every trade executed so far.

        In paper mode this is the sum of ``PositionSize.cost_cents`` for
        every recorded paper trade.  In live mode it is the sum of the
        cost of every filled order (rejected / failed orders do not
        contribute).  Used by ``main.py`` to compute the paper-mode
        effective balance without reaching into private attributes.
        """
        ...

    async def execute(self, signal: EdgeSignal, size: PositionSize) -> int:
        """Execute a sized signal and return the journal trade ID.

        Implementations must always return a positive integer — the
        row id from ``TradeJournal.record_trade``.  Rejected or failed
        live orders still produce a journal row (with an appropriate
        ``action`` value), so the return type remains ``int``.
        """
        ...

    def summary(self) -> dict:
        """Return a snapshot of this trader's running counters.

        The exact keys vary between paper and live modes (live adds
        ``executed``/``rejected``/``risk_killed`` etc.), but both modes
        always expose ``total_signals`` and ``total_cost_cents``.
        """
        ...
