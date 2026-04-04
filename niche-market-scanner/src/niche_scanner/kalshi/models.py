"""Pydantic models for Kalshi API responses.

All prices are in cents (0-10000). This matches Kalshi's native format.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class Market(BaseModel):
    """A single Kalshi market (contract).

    Kalshi weather/ranged markets include ``floor_strike`` and ``cap_strike``
    to define bucket boundaries in the underlying unit (e.g. degrees F):

    - Range bucket ("74F to 76F"): ``floor_strike=74.0, cap_strike=76.0``
    - Above threshold ("80 or above"): ``floor_strike=80.0, cap_strike=None``
    - Below threshold ("Below 50"): ``floor_strike=None, cap_strike=50.0``
    - Non-ranged markets: both ``None``
    """

    ticker: str
    event_ticker: str
    subtitle: str = ""
    yes_bid: int = 0
    yes_ask: int = 0
    no_bid: int = 0
    no_ask: int = 0
    last_price: int = 0
    volume: int = 0
    open_interest: int = 0
    status: str = "active"
    close_time: datetime
    result: str = ""
    floor_strike: float | None = None
    cap_strike: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def strike_type(self) -> Literal["range", "above", "below"] | None:
        """Classify the market based on its strike fields.

        Returns ``"range"`` when both strikes are set, ``"above"`` for floor
        only, ``"below"`` for cap only, or ``None`` when neither is set.
        """
        if self.floor_strike is not None and self.cap_strike is not None:
            return "range"
        if self.floor_strike is not None:
            return "above"
        if self.cap_strike is not None:
            return "below"
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def strike_low(self) -> float | None:
        """Lower boundary of the strike range, or ``None``."""
        return self.floor_strike

    @computed_field  # type: ignore[prop-decorator]
    @property
    def strike_high(self) -> float | None:
        """Upper boundary of the strike range, or ``None``."""
        return self.cap_strike

    @computed_field  # type: ignore[prop-decorator]
    @property
    def implied_prob(self) -> float:
        """Implied probability from last traded price (0.0-1.0)."""
        return self.last_price / 100.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread(self) -> int:
        """Bid-ask spread in cents on the YES side."""
        return self.yes_ask - self.yes_bid

    @computed_field  # type: ignore[prop-decorator]
    @property
    def time_to_close_hours(self) -> float:
        """Hours remaining until market close."""
        now = datetime.now(timezone.utc)
        close = self.close_time if self.close_time.tzinfo else self.close_time.replace(
            tzinfo=timezone.utc,
        )
        delta = (close - now).total_seconds() / 3600.0
        return max(delta, 0.0)


class OrderBookRow(BaseModel):
    """A single price-quantity level in the order book."""

    price: int
    quantity: int


class OrderBook(BaseModel):
    """Order book for a Kalshi market.

    Kalshi returns order book data as ``[[price, qty], ...]``.
    The validator accepts both that raw format and pre-parsed dicts.
    """

    ticker: str
    yes: list[OrderBookRow] = Field(default_factory=list)
    no: list[OrderBookRow] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _parse_raw_levels(cls, data: dict) -> dict:  # type: ignore[type-arg]
        """Convert Kalshi ``[[price, qty], ...]`` lists into OrderBookRow dicts."""
        for side in ("yes", "no"):
            raw = data.get(side, [])
            if raw and isinstance(raw[0], (list, tuple)):
                data[side] = [
                    {"price": row[0], "quantity": row[1]} for row in raw
                ]
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def best_yes_bid(self) -> int | None:
        """Highest YES bid price, or None if book is empty."""
        if not self.yes:
            return None
        return max(row.price for row in self.yes)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def best_no_bid(self) -> int | None:
        """Highest NO bid price, or None if book is empty."""
        if not self.no:
            return None
        return max(row.price for row in self.no)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid_price(self) -> float | None:
        """Mid-price derived from best YES bid and implied NO bid.

        Returns None if either side of the book is empty.
        """
        if self.best_yes_bid is None or self.best_no_bid is None:
            return None
        # YES bid and (100 - best NO bid) bracket the fair price.
        implied_yes_ask = 100 - self.best_no_bid
        return (self.best_yes_bid + implied_yes_ask) / 2.0


class Event(BaseModel):
    """A Kalshi event containing one or more markets."""

    event_ticker: str
    title: str
    category: str = ""
    series_ticker: str = ""
    markets: list[Market] = Field(default_factory=list)


class Order(BaseModel):
    """A Kalshi order."""

    order_id: str
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    type: Literal["limit", "market"]
    yes_price: int = 0
    no_price: int = 0
    count: int = 0
    status: str = ""
    created_time: datetime
