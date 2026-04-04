"""SQLite schema and initialization for the trade journal."""

from __future__ import annotations

import aiosqlite

SCHEMA = """\
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    engine       TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('yes', 'no')),
    action       TEXT    NOT NULL CHECK (action IN ('buy', 'sell')),
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
"""


async def init_db(path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database at *path* and apply the schema."""
    conn = await aiosqlite.connect(path)
    await conn.executescript(SCHEMA)
    await conn.commit()
    return conn
