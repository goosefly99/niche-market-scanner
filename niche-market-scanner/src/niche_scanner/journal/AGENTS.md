# Trade Journal Package — Agent Guide

## Architecture Overview

SQLite-backed persistence layer for trades, scan cycle logs, and
balance history. Uses **aiosqlite** with WAL mode for concurrent
reads from the dashboard while the scanner writes.

## Module Responsibilities

| Module | Role |
|---|---|
| `db.py` | Schema definition and `init_db(path)` function. Three tables: `trades` (with generated `cost_cents` column), `scan_cycles`, `balance_history`. WAL journal mode enabled on every connection. |
| `trade_journal.py` | `TradeJournal` class — async CRUD for trades. `TradeRecord` dataclass for inserts. Methods: `record_trade`, `record_outcome`, `get_trades`, `get_trade_by_id`, `get_daily_stats`, `get_stats`. Exposes `connection` property for shared access by dashboard components. |

## Key Conventions

- **Single shared connection.** `TradeJournal` owns the `aiosqlite.Connection`.
  Dashboard components (`ScanCycleLogger`, `BalanceTracker`, routes)
  access it via `journal.connection`. Never open a second connection
  to the same database file.
- **WAL mode** enables concurrent reads while writes are in progress.
- **`cost_cents` is a generated column:** computed as
  `contracts * price_cents` for YES, `contracts * (100 - price_cents)`
  for NO. Never insert it directly.
- **`action` column values:** `"buy"`, `"sell"`, `"rejected"`, `"failed"`.
- **`outcome` column values:** `"win"`, `"loss"`, or `NULL` (unresolved).
- Database path: `data/trades.db` (gitignored via `data/` in `.gitignore`).

## Testing

Tests in `tests/journal/`. Use temporary SQLite databases (`:memory:`
or `tmp_path` fixtures). No production database access.
