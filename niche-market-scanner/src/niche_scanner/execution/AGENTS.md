# Trade Execution Package — Agent Guide

## Architecture Overview

This package handles the execution side of the pipeline: taking sized
signals and either recording them as paper trades or placing real
orders on Kalshi through the risk guard.

Both `PaperTrader` and `LiveTrader` share the same interface:
`async execute(signal, size) -> int` (returns the journal trade ID).
The `MarketScanner` calls whichever trader was configured at startup.

## Module Responsibilities

| Module | Role |
|---|---|
| `paper_trader.py` | `PaperTrader` — records simulated trades to `TradeJournal`. No real orders. Tracks running totals (signal count, total cost). |
| `live_trader.py` | `LiveTrader` — places real orders on Kalshi via `KalshiClient`. Every order passes through `RiskGuard.check_order()` first. Rejected orders are logged with action=`"rejected"`. Failed API calls trigger the kill switch. Uses IOC (immediate-or-cancel) time-in-force for safety. |
| `risk_guard.py` | `RiskGuard` — fail-closed safety gate. Checks: kill switch, live trading enabled, bet size limit, cumulative spend cap, daily trade count, open position limit, daily loss limit, portfolio drawdown from peak, sufficient balance. `LiveTradingConfig` dataclass holds all limits. `RiskState` tracks mutable counters. |

## Key Conventions

- **All monetary values are in cents.** `cost_cents`, `price_cents`,
  `max_bet_size_cents`, etc.
- **RiskGuard is fail-closed.** Any unexpected exception in
  `check_order()` activates the kill switch and rejects the order.
- **Kill switch is sticky.** Once activated, ALL further trades are
  blocked until `reset_kill_switch()` is called manually.
- **LiveTrader records everything.** Successful fills, rejections,
  and failures all get journal entries with appropriate `action` values
  (`"buy"`, `"rejected"`, `"failed"`).
- Daily counters reset at UTC midnight via `_maybe_reset_daily_counters()`.
- `LiveTradingConfig.from_dict()` converts dollar amounts from YAML
  to cents automatically.

## Testing

Tests in `tests/execution/`. All tests use synthetic data — no real
Kalshi API calls. `KalshiClient` methods are mocked with `AsyncMock`.
