"""Base classes for edge-detection engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from niche_scanner.kalshi.models import Market, OrderBook


@dataclass
class EdgeSignal:
    """A detected edge opportunity on a Kalshi market."""

    engine: str
    ticker: str
    side: str  # "yes" or "no"
    model_prob: float
    market_prob: float
    edge_pp: float
    fee_adjusted_edge: float
    confidence: float
    thesis: str
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class EdgeEngine(ABC):
    """Abstract base for edge-detection engines."""

    @abstractmethod
    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]: ...
