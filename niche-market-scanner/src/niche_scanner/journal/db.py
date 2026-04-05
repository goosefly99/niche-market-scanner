"""SQLite schema and initialization for the trade journal."""

from __future__ import annotations

import aiosqlite

SCHEMA = """\
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    engine       TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('yes', 'no')),
    action       TEXT    NOT NULL CHECK (action IN ('buy', 'sell', 'rejected', 'failed')),
    contracts    INTEGER NOT NULL CHECK (contracts > 0),
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0 AND price_cents <= 100),
    cost_cents   INTEGER GENERATED ALWAYS AS (
        CASE WHEN side = 'yes' THEN contracts * price_cents
             ELSE contracts * (100 - price_cents)
        END
    ) STORED,
    model_prob   REAL    NOT NULL,
    market_prob  REAL    NOT NULL,
    edge_pp      REAL    NOT NULL,
    kelly_fraction REAL  NOT NULL,
    thesis       TEXT    NOT NULL DEFAULT '',
    payout_cents INTEGER,
    outcome      TEXT    CHECK (outcome IN ('win', 'loss')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_engine ON trades (engine);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades (ticker);

CREATE TABLE IF NOT EXISTS scan_cycles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    markets_scanned   INTEGER NOT NULL,
    signals_found     INTEGER NOT NULL,
    executed          INTEGER NOT NULL,
    skipped           INTEGER NOT NULL,
    no_exposure_cents INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scan_cycles_created ON scan_cycles (created_at);

CREATE TABLE IF NOT EXISTS balance_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    balance_cents           INTEGER NOT NULL,
    peak_cents              INTEGER NOT NULL,
    drawdown_pct            REAL    NOT NULL,
    cumulative_spend_cents  INTEGER NOT NULL,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_balance_history_created ON balance_history (created_at);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database at *path* and apply the schema."""
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.executescript(SCHEMA)
    await conn.commit()
    return conn
