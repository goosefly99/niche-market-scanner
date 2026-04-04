# Niche Market Edge Scanner — Design Spec

**Date:** 2026-04-04
**Source spec:** `strategies/specs/niche-market-edge-scanner-trading-system--19c9c94d.json` (v3.1)
**Approach:** Monolith scanner, paper trading first, weather + economics verticals

---

## Overview

Automated edge detection system for systematically mispriced event contracts on Kalshi. Targets low-competition niches where retail participants use wrong data sources or exhibit persistent biases. Runs as a single Python async service in Docker, starting in paper-trade mode against the Kalshi demo API.

**Starting verticals:** Weather (highest confidence), Economics (unique to Kalshi)
**Deferred verticals:** Thin markets (Phase 3), Sports (Phase 4), Earnings (Phase 3)

---

## Architecture

### Approach: Monolith Scanner

Single Python service. All engines, scanner, sizer, and journal share one async event loop with APScheduler. SQLite for state persistence. One Docker container, one `docker-compose up`.

Chosen over microservices (overkill for paper trading) and plugin architecture (premature abstraction). Refactor to plugin-based when transitioning to live trading.

### Project Structure

```
niche-market-scanner/
  src/niche_scanner/
    __init__.py
    main.py                    # Entry: starts scanner loop, scheduler, Telegram
    config.py                  # Pydantic BaseSettings from env + YAML

    kalshi/
      __init__.py
      client.py                # REST client, RSA-PSS signing, rate limiting
      websocket.py             # WebSocket for orderbook/fills streaming
      models.py                # Pydantic: Market, Event, OrderBook, Order, Fill

    scanner/
      __init__.py
      market_scanner.py        # Polls Kalshi, batch orderbooks (100/call), routes to engines

    engines/
      __init__.py
      base.py                  # Abstract EdgeEngine: scan(markets) -> list[EdgeSignal]
      weather.py               # ICAO mapping, NOAA, bucket boundary, 3 mispricing detectors
      economics.py             # BLS/FRED/Cleveland Fed/CME FedWatch, release calendar
      thin_market.py           # Dead room scanner, inefficiency scoring

    sizing/
      __init__.py
      kelly.py                 # Fractional Kelly: concentrated, barbell, NO-bet modes
      fees.py                  # Kalshi fee calculator (taker/maker formulas)

    execution/
      __init__.py
      executor.py              # Order placement, batch ops, position monitoring
      paper_trader.py          # Logs trades without executing, tracks simulated P&L

    alerts/
      __init__.py
      telegram.py              # Telegram bot: alerts + commands (/status, /positions, /stop)

    journal/
      __init__.py
      trade_journal.py         # SQLite: trade log, per-vertical metrics, edge decay
      models.py                # TradeRecord schema

    monitor/
      __init__.py
      health.py                # Heartbeats, API checks, dead-man's-switch

  tests/
  config/
    settings.yaml              # Scan intervals, Kelly params, edge thresholds, vertical toggles
    icao_stations.yaml         # Verified ICAO station mappings (Phase 0 deliverable)
    causal_graph.yaml          # Economics cascade dependencies (Phase 2+)

  Dockerfile
  docker-compose.yml
  pyproject.toml
  AGENTS.md
  .env.example
```

---

## Data Flow

```
APScheduler triggers per-vertical scan intervals:
  Weather: 15min (2min within 4h of resolution)
  Economics: 30min (5min within 48h of release)
  Thin market: 10min

Each tick:
  1. MarketScanner.fetch_markets(category) -> list[Market]
  2. MarketScanner.batch_orderbooks(tickers) -> dict[ticker, OrderBook]
  3. Engine.scan(markets, orderbooks) -> list[EdgeSignal]
  4. KellySizer.size(signal, portfolio) -> PositionSize | None
  5. PaperTrader.record(signal, size) -> SQLite + Telegram alert
```

### EdgeSignal (universal type between all components)

```python
@dataclass
class EdgeSignal:
    engine: str              # "weather", "economics", "thin_market"
    ticker: str              # Kalshi market ticker
    side: str                # "yes" or "no"
    model_prob: float        # Estimated true probability (0-1)
    market_prob: float       # Kalshi implied probability (price/100)
    edge_pp: float           # model_prob - market_prob in percentage points
    fee_adjusted_edge: float # edge minus round-trip fees
    confidence: float        # Engine-specific confidence (0-1)
    thesis: str              # Human-readable explanation
    metadata: dict           # Engine-specific (ICAO station, indicator name, etc.)
    timestamp: datetime
```

---

## Engine Designs

### Weather Engine

Core edge: NOAA station-specific forecasts are 85-90% accurate at 1-2 day horizons. Retail traders use city-level weather apps. The 3-8F gap between airport ICAO station microclimate and urban readings is decisive on narrow temperature buckets.

**Components:**
1. **ICAO Mapper** — Config-driven `{kalshi_city: ICAO_station}`. Refuses to price any city without verified mapping. Phase 0 blocking deliverable.
2. **NOAA Fetcher** — Station-specific hourly forecast from `api.weather.gov/gridpoints/{office}/{x},{y}/forecast/hourly`. 15min cache TTL.
3. **Probability Modeler** — Converts point forecast + uncertainty range into probability distribution across Kalshi buckets using `scipy.stats.norm`.
4. **Bucket Boundary Detector** — When forecast mean is within 1 std dev of a bucket boundary, flags as high-edge (retail anchors to point forecast, ignores distribution).
5. **Three mispricing detectors:**
   - Wrong source: model vs market divergence >threshold (retail using wrong data)
   - Stale forecast: Market hasn't moved but NOAA updated in last cycle
   - Time decay: <4h to resolution, market still pricing uncertainty when forecast confidence is 90%+

### Economics Engine

Core edge: Leading indicators (Cleveland Fed Nowcast for CPI, ADP for jobs, regional Fed surveys, CME FedWatch for rates) are public, free, and highly predictive. Retail prices based on stale consensus. Unique to Kalshi.

**Components:**
1. **Release Calendar** — Upcoming BLS/Fed/BEA dates. Intensifies scan to 5min within 48h.
2. **Indicator Aggregator** — Cleveland Fed Nowcast, ADP, FRED, CME FedWatch. Each has weight + historical accuracy.
3. **Model Probability** — Weighted combination of indicators per contract outcome.
4. **NO Bet Scanner** — Scans for near-certain outcomes priced 95-99c YES. Calculates premium collection probability. Routes to Kelly sizer in NO-bet mode (2% cap).

### Thin Market Scanner (Phase 3)

Ranks all Kalshi markets by inefficiency score: `spread_width * time_to_resolution / open_interest`. Discovers dead rooms across all categories using batch orderbook scanning (100 tickers/call).

---

## Sizing & Risk

### Kelly Position Sizer

```
fee = round_up(0.07 * P * (1-P))     # taker
net_edge = edge_pp - (round_trip_fee * 100)
kelly = (b * p - (1 - p)) / b         # where b = odds, p = model_prob
size = kelly * fraction * bankroll
```

**Three modes:**
- **Concentrated** (default): Half-Kelly, for high-confidence signals
- **Barbell** (Taleb): Many small positions at 1-8c, targeting 2-8x payoff
- **NO-bet underwriting**: 2% per position, 25% aggregate cap

**Constraints enforced:**
- Half-Kelly max on any position
- Per-market Kalshi position limits (queried via API)
- Fee-adjusted minimum edge threshold per vertical
- Daily drawdown halt at 3-5%
- Kill switch at -40% session drawdown
- Vertical activates only when bankroll supports 10 simultaneous half-Kelly positions

### Fee Calculator

```python
def taker_fee(contracts: int, price_cents: int) -> int:
    """Kalshi taker fee in centicents, rounded up."""
    p = price_cents / 100  # price in dollars
    return math.ceil(0.07 * contracts * p * (1 - p) * 10000)

def maker_fee(contracts: int, price_cents: int) -> int:
    p = price_cents / 100
    return math.ceil(0.0175 * contracts * p * (1 - p) * 10000)
```

Max taker fee at 50c: 1.75c/contract. At 95c (NO bets): ~0.33c. No settlement fees.

---

## Kalshi API Integration

### Authentication

RSA-PSS per-request signing (stateless, no tokens):
- Sign `{timestamp_ms}{HTTP_METHOD}{path_without_query_params}`
- Algorithm: RSA-PSS with SHA-256 digest, MGF1-SHA256, salt length = digest length
- Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`
- Requires `cryptography` package

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /markets` | List markets with category/series filters |
| `GET /markets/orderbooks` | Batch orderbooks (up to 100 tickers) |
| `POST /portfolio/orders` | Create order (limit/market, FOK/GTC/IOC) |
| `POST /portfolio/orders/batched` | Batch create (up to 20) |
| `DELETE /portfolio/orders/{id}` | Cancel order |
| `GET /portfolio/positions` | Current positions |
| `GET /portfolio/balance` | Account balance |
| `WSS /ws/v2` | Real-time orderbook + fills |

### Rate limits

| Tier | Read/s | Write/s | Qualification |
|---|---|---|---|
| Basic | 20 | 10 | Signup |
| Advanced | 30 | 30 | Application |
| Premier | 100 | 100 | 3.75% monthly volume |
| Prime | 400 | 400 | 7.5% monthly volume |

Paper trading uses Basic tier. BatchCancelOrders count as 0.2 per item.

### Environments

- **Demo:** `https://demo-api.kalshi.co/trade-api/v2` (paper trading)
- **Production:** `https://api.elections.kalshi.com/trade-api/v2` (live)

Toggle via `KALSHI_ENV` env var. All prices in cents (0-10000), balance in cents.

---

## Docker Build

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY config/ config/
COPY src/ src/
VOLUME /app/data
ENV KALSHI_ENV=demo
ENTRYPOINT ["python", "-m", "niche_scanner.main"]
```

### docker-compose.yml

```yaml
services:
  scanner:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./config:/app/config
      - ./secrets:/app/secrets:ro
    restart: unless-stopped
```

### Dependencies

```
httpx, websockets, cryptography, pydantic[yaml], apscheduler,
scipy, numpy, pandas, python-telegram-bot, pyyaml, aiosqlite
```

---

## Configuration

### .env (secrets)

```
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=/app/secrets/kalshi.pem
KALSHI_ENV=demo
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### settings.yaml (tuning)

```yaml
scanner:
  weather_interval_min: 15
  weather_urgent_interval_min: 2
  weather_urgent_hours_before: 4
  economics_interval_min: 30
  economics_urgent_interval_min: 5
  economics_urgent_hours_before: 48
  thin_market_interval_min: 10

sizing:
  kelly_fraction: 0.5
  no_bet_per_position_cap: 0.02
  no_bet_aggregate_cap: 0.25
  min_edge_pp: 12
  max_drawdown_daily: 0.05
  kill_switch_drawdown: 0.40
  paper_trade: true

verticals:
  weather: { enabled: true }
  economics: { enabled: true }
  thin_market: { enabled: false }
  sports: { enabled: false }
  earnings: { enabled: false }
```

### icao_stations.yaml (Phase 0 deliverable)

```yaml
stations:
  # Populated during Phase 0 market survey
  # new_york: { icao: KLGA, verified: true, last_checked: "2026-04-04" }
```

---

## Paper Trading Mode

When `sizing.paper_trade: true`:
- ExecutionEngine replaced with PaperTrader
- PaperTrader calls demo API to verify fills against orderbook depth
- Logs every signal + simulated trade to SQLite
- Tracks simulated P&L, win rate, edge captured per vertical
- Telegram alerts prefixed with `[PAPER]`
- Transition to live: `paper_trade: false`, `KALSHI_ENV=production`
- Live trading requires explicit user approval (not automated)

---

## Success Criteria (paper trading phase)

1. Weather engine: >55% win rate, average edge >10pp over first 500 paper trades
2. ICAO station mapping verified for 100% of active weather cities
3. Bucket boundary trades: >60% win rate specifically
4. Economics engine: >5 tradeable signals/month with realized edge >12pp
5. NO bet portfolio: >80% of positions collect premium
6. Combined Sortino ratio >2.0 over rolling 30-day window
7. Telegram alerts within 10 seconds of signal
8. System runs 7+ days without crash or missed scan cycle

---

## Implementation Phases

### Phase 0: Kalshi Market Survey (manual, 1-2 days)
Audit weather buckets, ICAO stations, economics calendar, fee impact at extreme prices. Go/no-go per vertical.

### Phase 1: Core Infra + Weather Engine
Kalshi client (RSA-PSS), MarketScanner, WeatherEdgeEngine, KellySizer, PaperTrader, TradeJournal, Telegram alerts. Paper trade weather.

### Phase 2: Economics Engine + NO Bets + Correlation Monitor
EconomicsEdgeEngine, NO bet sizing mode, cross-vertical correlation caps (15% per macro factor). Paper trade both verticals.

### Phase 3: Thin Markets + Earnings + OpenClaw Eval
ThinMarketScanner, EarningsEdgeEngine, OpenClaw/Simmer SDK evaluation.

### Phase 4: Sports + Live Execution + Deployment
SportsEdgeEngine (NFL/NBA primary), CascadeArbitrageEngine (experimental), full ExecutionEngine, WebSocket streaming, VPS deployment. **Live trading transition requires explicit approval.**

### Phase 5: Tail Risk, Calibration, Edge Decay
Barbell strategy, calibration feedback loop, edge decay detection, full correlation matrix, performance dashboard.
