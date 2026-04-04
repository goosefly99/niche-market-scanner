"""Kelly-criterion position sizer with NO-bet risk caps."""

from __future__ import annotations

import math
from dataclasses import dataclass

from niche_scanner.engines.base import EdgeSignal
from niche_scanner.sizing.fees import round_trip_fee_pp


@dataclass
class SizingConfig:
    """Configuration knobs for the Kelly sizer."""

    kelly_fraction: float = 0.5
    min_edge_pp: float = 12.0
    no_bet_per_position_cap: float = 0.02
    no_bet_aggregate_cap: float = 0.25
    max_position_pct: float = 0.05


@dataclass
class PositionSize:
    """Output of the Kelly sizer."""

    ticker: str
    side: str
    contracts: int
    price_cents: int
    cost_cents: int
    kelly_raw: float
    kelly_fraction_used: float


class KellySizer:
    """Compute position sizes via fractional Kelly with risk caps."""

    def __init__(self, config: SizingConfig | None = None) -> None:
        self.config = config or SizingConfig()

    def size(
        self,
        signal: EdgeSignal,
        bankroll_cents: int,
        no_exposure_pct: float = 0.0,
    ) -> PositionSize | None:
        """Size a position for *signal* given *bankroll_cents*.

        Parameters
        ----------
        signal:
            The edge signal to size.
        bankroll_cents:
            Total bankroll in cents.
        no_exposure_pct:
            Current aggregate NO-side exposure as a fraction of bankroll
            (0.0 -- 1.0).

        Returns
        -------
        PositionSize or None if the bet should be skipped.
        """
        price_cents = round(signal.market_prob * 100)
        price_cents = max(1, min(99, price_cents))

        # 1. Calculate round-trip fee PP
        fee_pp = round_trip_fee_pp(price_cents)

        # 2. Net edge after fees
        net_edge = signal.edge_pp - fee_pp
        if net_edge < self.config.min_edge_pp:
            return None

        # 3. Kelly fraction: f = (b*p - q) / b
        if signal.side == "yes":
            b = (100 - price_cents) / price_cents
            p = signal.model_prob
        else:  # "no"
            b = price_cents / (100 - price_cents)
            p = 1 - signal.model_prob  # probability that NO wins
        q = 1 - p

        if b <= 0:
            return None

        kelly_raw = (b * p - q) / b
        if kelly_raw <= 0:
            return None

        # 4. Apply fractional Kelly
        kelly_used = kelly_raw * self.config.kelly_fraction

        # 5. Cap at max_position_pct
        kelly_used = min(kelly_used, self.config.max_position_pct)

        # 6. NO-bet caps
        if signal.side == "no":
            remaining_no = self.config.no_bet_aggregate_cap - no_exposure_pct
            if remaining_no <= 0:
                return None
            kelly_used = min(kelly_used, self.config.no_bet_per_position_cap)
            kelly_used = min(kelly_used, remaining_no)

        # 7. Convert to contracts
        if signal.side == "yes":
            cost_per_contract = price_cents
        else:
            cost_per_contract = 100 - price_cents

        budget_cents = kelly_used * bankroll_cents
        contracts = math.floor(budget_cents / cost_per_contract) if cost_per_contract > 0 else 0

        if contracts <= 0:
            return None

        cost_cents = contracts * cost_per_contract

        return PositionSize(
            ticker=signal.ticker,
            side=signal.side,
            contracts=contracts,
            price_cents=price_cents,
            cost_cents=cost_cents,
            kelly_raw=kelly_raw,
            kelly_fraction_used=kelly_used,
        )
