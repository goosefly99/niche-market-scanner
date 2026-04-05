"""Thin-market edge engine for cross-category dead room detection.

Identifies markets where thin orderbooks create pricing inefficiencies.
Unlike weather/economics engines that use external data sources for model
probabilities, this engine derives fair value from the orderbook itself
(mid-price) and compares against executable prices (bid/ask).

Works across all market categories — receives the same market list as
other engines but evaluates through a liquidity/spread lens.
"""

from __future__ import annotations

import logging

from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.kalshi.models import Market, OrderBook
from niche_scanner.sizing.fees import round_trip_fee_pp

logger = logging.getLogger(__name__)


class ThinMarketEngine(EdgeEngine):
    """Detect edge opportunities in low-liquidity ("dead room") markets.

    Detection strategy:
    1. Estimate fair value from orderbook mid-price (preferred) or
       last trade price (fallback for one-sided books).
    2. Compare fair value against executable prices (yes_ask, no_ask).
    3. Subtract round-trip fees and filter by min_edge_pp.

    Only considers markets with spreads in the [min_spread, max_spread]
    range and close times within [min_hours, max_hours].
    """

    def __init__(
        self,
        min_edge_pp: float = 12.0,
        min_spread_cents: int = 8,
        max_spread_cents: int = 50,
        min_time_to_close_hours: float = 2.0,
        max_time_to_close_hours: float = 168.0,
    ) -> None:
        self._min_edge_pp = min_edge_pp
        self._min_spread_cents = min_spread_cents
        self._max_spread_cents = max_spread_cents
        self._min_hours = min_time_to_close_hours
        self._max_hours = max_time_to_close_hours

    # ------------------------------------------------------------------
    # Fair value estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_fair_value(
        market: Market,
        orderbook: OrderBook,
    ) -> float | None:
        """Estimate fair YES price in cents.

        Priority:
        1. Orderbook mid-price (requires both sides populated).
        2. Last trade price (only if market has some volume, suggesting
           the price is not completely stale).

        Returns None when no reasonable estimate is available.
        """
        mid = orderbook.mid_price
        if mid is not None:
            return mid

        if market.last_price > 0 and market.volume > 0:
            return float(market.last_price)

        return None

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _confidence(
        self,
        spread: int,
        hours_to_close: float,
        volume: int,
    ) -> float:
        """Score confidence from market characteristics.

        Thin-market signals are capped at 0.8 confidence since the
        fair value is derived from the orderbook itself rather than
        an independent external model.
        """
        conf = 0.5

        # Tighter spreads give more reliable mid-price estimates
        if spread <= 15:
            conf += 0.1
        elif spread >= 30:
            conf -= 0.15

        # Some volume means the market isn't completely abandoned
        if volume > 0:
            conf += 0.1
        if volume >= 10:
            conf += 0.05

        # Closer to close: less time for repricing, more urgent
        if hours_to_close < 24:
            conf += 0.1
        elif hours_to_close > 72:
            conf -= 0.1

        return max(0.1, min(conf, 0.8))

    # ------------------------------------------------------------------
    # Market evaluation
    # ------------------------------------------------------------------

    def _evaluate_market(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> EdgeSignal | None:
        """Score a single market for thin-liquidity edge."""
        # Time window filter
        hours = market.time_to_close_hours
        if hours < self._min_hours or hours > self._max_hours:
            return None

        # Spread filter
        spread = market.spread
        if spread < self._min_spread_cents or spread > self._max_spread_cents:
            return None

        # Fair value
        fair_cents = self._estimate_fair_value(market, orderbook)
        if fair_cents is None:
            return None

        model_prob = fair_cents / 100.0
        confidence = self._confidence(spread, hours, market.volume)
        best: EdgeSignal | None = None

        # --- YES side: buy YES at yes_ask ---
        if 0 < market.yes_ask < 100:
            market_prob_yes = market.yes_ask / 100.0
            raw_edge = (model_prob - market_prob_yes) * 100.0
            fee_cost = round_trip_fee_pp(market.yes_ask)
            adj_edge = raw_edge - fee_cost

            if adj_edge >= self._min_edge_pp:
                best = EdgeSignal(
                    engine="thin_market",
                    ticker=market.ticker,
                    side="yes",
                    model_prob=model_prob,
                    market_prob=market_prob_yes,
                    edge_pp=raw_edge,
                    fee_adjusted_edge=adj_edge,
                    confidence=confidence,
                    thesis=(
                        f"Thin market: spread {spread}c, "
                        f"fair value {fair_cents:.0f}c vs "
                        f"YES ask {market.yes_ask}c"
                    ),
                    metadata={
                        "spread_cents": spread,
                        "fair_value_cents": fair_cents,
                        "volume": market.volume,
                        "hours_to_close": round(hours, 1),
                        "detection": "wide_spread",
                    },
                )

        # --- NO side: buy NO at no_ask ---
        if 0 < market.no_ask < 100:
            model_prob_no = (100.0 - fair_cents) / 100.0
            market_prob_no = market.no_ask / 100.0
            raw_edge = (model_prob_no - market_prob_no) * 100.0
            fee_cost = round_trip_fee_pp(market.no_ask)
            adj_edge = raw_edge - fee_cost

            if adj_edge >= self._min_edge_pp:
                signal = EdgeSignal(
                    engine="thin_market",
                    ticker=market.ticker,
                    side="no",
                    model_prob=model_prob,
                    market_prob=1.0 - market_prob_no,
                    edge_pp=raw_edge,
                    fee_adjusted_edge=adj_edge,
                    confidence=confidence,
                    thesis=(
                        f"Thin market: spread {spread}c, "
                        f"fair value {fair_cents:.0f}c implies "
                        f"NO edge at {market.no_ask}c"
                    ),
                    metadata={
                        "spread_cents": spread,
                        "fair_value_cents": fair_cents,
                        "volume": market.volume,
                        "hours_to_close": round(hours, 1),
                        "detection": "wide_spread",
                    },
                )
                if best is None or signal.fee_adjusted_edge > best.fee_adjusted_edge:
                    best = signal

        return best

    # ------------------------------------------------------------------
    # Public scan interface
    # ------------------------------------------------------------------

    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]:
        """Evaluate all markets for thin-liquidity edge opportunities."""
        signals: list[EdgeSignal] = []

        for market in markets:
            if market.status != "active":
                continue

            ob = orderbooks.get(market.ticker)
            if ob is None:
                continue

            signal = self._evaluate_market(market, ob)
            if signal is not None:
                signals.append(signal)

        if signals:
            logger.info(
                "Thin-market scan: %d signal(s) from %d markets",
                len(signals),
                len(markets),
            )

        return signals
