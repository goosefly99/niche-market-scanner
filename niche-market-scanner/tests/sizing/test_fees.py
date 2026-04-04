"""Tests for Kalshi fee calculations."""

from __future__ import annotations

from niche_scanner.sizing.fees import (
    maker_fee_cents,
    round_trip_fee_pp,
    taker_fee_cents,
)


def test_taker_fee_at_50_cents() -> None:
    # ceil(0.07 * 1 * 0.5 * 0.5 * 100) = ceil(1.75) = 2
    assert taker_fee_cents(1, 50) == 2


def test_taker_fee_at_95_cents() -> None:
    # ceil(0.07 * 1 * 0.95 * 0.05 * 100) = ceil(0.3325) = 1
    assert taker_fee_cents(1, 95) == 1


def test_taker_fee_at_5_cents() -> None:
    # Symmetric with 95c: ceil(0.07 * 1 * 0.05 * 0.95 * 100) = ceil(0.3325) = 1
    assert taker_fee_cents(1, 5) == 1


def test_maker_fee_at_50_cents() -> None:
    # ceil(0.0175 * 1 * 0.5 * 0.5 * 100) = ceil(0.4375) = 1
    assert maker_fee_cents(1, 50) == 1


def test_taker_fee_10_contracts_at_50() -> None:
    # ceil(0.07 * 10 * 0.5 * 0.5 * 100) = ceil(17.5) = 18
    assert taker_fee_cents(10, 50) == 18


def test_round_trip_fee_pp() -> None:
    # At 50c: taker fee = 2 cents, round trip = 4 cents
    # PP = 4 / (1 * 50) * 100 = 8.0
    result = round_trip_fee_pp(50)
    assert result == 8.0


def test_round_trip_fee_pp_at_95() -> None:
    # At 95c: taker fee = 1 cent, round trip = 2 cents
    # PP = 2 / (1 * 95) * 100 ≈ 2.105
    result = round_trip_fee_pp(95)
    assert result < 3.0


def test_zero_price_and_hundred_price() -> None:
    assert taker_fee_cents(1, 0) == 0
    assert taker_fee_cents(1, 100) == 0
    assert round_trip_fee_pp(0) == 0.0
    assert round_trip_fee_pp(100) == 0.0
