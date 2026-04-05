# Position Sizing Package — Agent Guide

## Architecture Overview

Computes how much to bet on each signal using the Kelly criterion
with conservative fractional scaling and NO-bet risk caps.

## Module Responsibilities

| Module | Role |
|---|---|
| `fees.py` | Kalshi fee calculations. `taker_fee_cents(contracts, price)`, `maker_fee_cents(contracts, price)`, `round_trip_fee_pp(price)`. Formula: `ceil(0.07 * C * P * (1-P) * 100)`. No settlement fees. |
| `kelly.py` | `KellySizer` — fractional Kelly with risk caps. `SizingConfig` dataclass (kelly_fraction, min_edge_pp, NO-bet caps). `PositionSize` output dataclass. Returns `None` for signals below min edge or negative Kelly. |

## Key Conventions

- **All prices in cents (0-100).** `round_trip_fee_pp()` returns the
  cost as percentage points of contract value.
- **Half-Kelly default** (`kelly_fraction=0.5`). Raw Kelly is scaled
  down before position sizing.
- **NO-bet caps:** 2% per position (`no_bet_per_position_cap`),
  25% aggregate (`no_bet_aggregate_cap`). These are fractions of
  bankroll, not absolute amounts.
- **Max position size:** 5% of bankroll (`max_position_pct`), applied
  after Kelly scaling.
- Fee is subtracted from edge BEFORE the min-edge check. A signal
  must have `edge_pp - fee_pp >= min_edge_pp` to be sized.

## Testing

Tests in `tests/sizing/`. Pure math — no I/O or mocking needed.
