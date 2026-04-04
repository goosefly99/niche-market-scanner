"""Kalshi fee calculations.

All prices and fees are in cents. Kalshi charges a percentage of the
expected payout, which is ``price * (1 - price)`` per contract.
"""

from __future__ import annotations

import math


def taker_fee_cents(contracts: int, price_cents: int) -> int:
    """Kalshi taker fee in cents, rounded up.

    Formula: ceil(0.07 * contracts * (price/100) * (1 - price/100) * 100)
    """
    if price_cents <= 0 or price_cents >= 100:
        return 0
    p = price_cents / 100.0
    return math.ceil(0.07 * contracts * p * (1 - p) * 100)


def maker_fee_cents(contracts: int, price_cents: int) -> int:
    """Kalshi maker fee in cents, rounded up.

    Same formula as taker but with a 0.0175 multiplier.
    """
    if price_cents <= 0 or price_cents >= 100:
        return 0
    p = price_cents / 100.0
    return math.ceil(0.0175 * contracts * p * (1 - p) * 100)


def round_trip_fee_pp(price_cents: int, contracts: int = 1) -> float:
    """Two taker fees expressed as percentage points of contract value.

    Returns the round-trip fee cost as a proportion of the contract
    price, scaled to percentage points.
    """
    if price_cents <= 0 or price_cents >= 100:
        return 0.0
    fee = 2 * taker_fee_cents(contracts, price_cents)
    return fee / (contracts * price_cents) * 100
