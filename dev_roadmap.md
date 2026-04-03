# Development Roadmap — Niche Market Edge Scanner & Trading System

## Phase 1: Core Infrastructure & Weather Edge Engine (2-3 weeks)

| # | Deliverable | Status | Dependencies | Target Files |
|---|------------|--------|--------------|-------------|
| 1 | MarketScanner module with Polymarket API integration and category filtering | Not Started | None | `src/scanner/market_scanner.py`, `src/scanner/models.py` |
| 2 | ICAO station mapping configuration (city -> station code -> resolution source) | Not Started | None | `config.yaml`, `src/edges/weather/station_mapping.py` |
| 3 | WeatherEdgeEngine with NOAA forecast fetching, bucket probability calculation, and boundary analysis | Not Started | #1, #2 | `src/edges/weather_edge_engine.py`, `src/edges/weather/noaa_client.py`, `src/edges/weather/bucket_analysis.py` |
| 4 | KellyPositionSizer with fractional Kelly, portfolio limits, and kill switch | Not Started | None | `src/sizing/kelly_position_sizer.py`, `src/sizing/risk_limits.py` |
| 5 | TradeJournal with SQLite persistence and basic performance metrics | Not Started | None | `src/journal/trade_journal.py`, `src/journal/models.py` |
| 6 | Paper trading simulation engine that records hypothetical trades without execution | Not Started | #1, #4, #5 | `src/execution/paper_engine.py` |
| 7 | Weather API caching layer with configurable TTL (30-60 min Visual Crossing, 15 min NOAA) | Not Started | #3 | `src/edges/weather/cache.py` |
| 8 | Unit tests for bucket probability calculation and Kelly sizing math | Not Started | #3, #4 | `tests/test_bucket_probability.py`, `tests/test_kelly_sizer.py` |

## Phase 2: Sports Edge Engine & Favorite Bias Detection (2 weeks)

| # | Deliverable | Status | Dependencies | Target Files |
|---|------------|--------|--------------|-------------|
| 9 | SportsEdgeEngine with Football-Data.org / API-Football integration | Not Started | #1 | `src/edges/sports_edge_engine.py`, `src/edges/sports/football_client.py` |
| 10 | Historical win rate calculator by team, home/away, competition, and form | Not Started | #9 | `src/edges/sports/win_rate_calculator.py` |
| 11 | Favorite bias scanner running every 5 minutes on La Liga and UCL markets | Not Started | #9, #10 | `src/edges/sports/bias_scanner.py` |
| 12 | NO-bet edge detection for overpriced favorites (Atletico/PSG NO pattern) | Not Started | #9, #10 | `src/edges/sports/no_bet_detector.py` |
| 13 | NBA outcome prediction module with probability weighting | Not Started | #1 | `src/edges/sports/nba_engine.py`, `src/edges/sports/nba_client.py` |
| 14 | Backtesting framework to validate edge estimates against historical Polymarket resolution data | Not Started | #5, #9 | `src/journal/backtesting.py` |
| 15 | Paper trade logging for all sports signals | Not Started | #6, #9 | `src/journal/trade_journal.py` (extend) |

## Phase 3: Niche Prop Scanner & Low-Liquidity Detection (2 weeks)

| # | Deliverable | Status | Dependencies | Target Files |
|---|------------|--------|--------------|-------------|
| 16 | NichePropScanner with participant count filtering and category classification | Not Started | #1 | `src/edges/niche_prop_scanner.py`, `src/edges/props/category_filter.py` |
| 17 | Spread analysis module calculating bid-ask width relative to true probability estimates | Not Started | #16 | `src/edges/props/spread_analysis.py` |
| 18 | Wallet profiler that retrieves historical P&L, prediction count, and win rate for wallets active in scanned markets | Not Started | #16 | `src/edges/props/wallet_profiler.py` |
| 19 | Informed participant detector (high P&L wallet enters a dead room = signal) | Not Started | #18 | `src/edges/props/informed_detector.py` |
| 20 | Copytrade signal generator with configurable follow delay and size scaling | Not Started | #19 | `src/edges/props/copytrade.py` |
| 21 | Category-specific scanning for golf (PGA), esports (CS2, LoL, Dota2, Valorant), and goalscorer props | Not Started | #16 | `src/edges/props/category_scanners.py` |

## Phase 4: Execution Engine, Alerting & Live Trading (2-3 weeks)

| # | Deliverable | Status | Dependencies | Target Files |
|---|------------|--------|--------------|-------------|
| 22 | ExecutionEngine with abstract broker interface, py-clob-client default, Simmer SDK optional | Not Started | #4, #6 | `src/execution/execution_engine.py`, `src/execution/broker_interface.py`, `src/execution/clob_broker.py`, `src/execution/simmer_broker.py` |
| 23 | Telegram AlertManager with signal alerts, execution confirmations, daily summaries, and command interface | Not Started | None | `src/alerts/alert_manager.py`, `src/alerts/telegram_bot.py` |
| 24 | Exit strategy engine: take-profit, stop-loss, and time-based exits | Not Started | #22 | `src/execution/exit_strategies.py` |
| 25 | Live trading mode with configurable position size caps per strategy | Not Started | #22, #4 | `src/execution/execution_engine.py` (extend) |
| 26 | Kill switch implementation with automatic position unwinding at drawdown threshold | Not Started | #4, #22 | `src/sizing/kill_switch.py` |
| 27 | Remote control commands: /start, /stop, /status, /positions, /exit_all, /set_params | Not Started | #23 | `src/alerts/commands.py` |
| 28 | VPS deployment configuration with systemd service files for 24/7 operation | Not Started | #22, #23 | `deploy/niche-scanner.service`, `deploy/setup.sh` |
| 29 | State persistence layer saving open positions, session P&L, consecutive-loss counter, and kill switch status to SQLite | Not Started | #5, #22 | `src/journal/state_persistence.py` |
| 30 | Secrets management for wallet private key: encrypted keystore or OS-level secret store, VPS hardening steps | Not Started | #28 | `deploy/hardening.md`, `src/config/secrets.py` |

## Phase 5: Tail Risk Strategy, Calibration & Optimization (2-3 weeks)

| # | Deliverable | Status | Dependencies | Target Files |
|---|------------|--------|--------------|-------------|
| 31 | Tail risk scanner (Taleb barbell mode) for weather and sports markets priced at 2-8 cents | Not Started | #3, #9 | `src/edges/tail_risk_scanner.py` |
| 32 | Calibration feedback loop: realized edge analysis feeding back to threshold adjustment | Not Started | #5, #3, #9, #16 | `src/journal/calibration.py` |
| 33 | International weather support via Meteoblue/Windy API for non-US markets (London, Sao Paulo) | Not Started | #3 | `src/edges/weather/meteoblue_client.py`, `src/edges/weather/windy_client.py` |
| 34 | Multi-strategy performance dashboard with per-vertical win rates, edge decay tracking, and Sortino ratios | Not Started | #5 | `src/journal/dashboard.py` |
| 35 | Dynamic threshold optimization engine | Not Started | #32 | `src/edges/threshold_optimizer.py` |
| 36 | Edge decay detector that flags strategies where the market is becoming more efficient | Not Started | #5, #32 | `src/journal/edge_decay.py` |
| 37 | Forward-looking validation report: pre-deployment gate confirming each strategy's edge on out-of-sample historical data (3+ months) | Not Started | #14, #32 | `src/journal/validation_report.py` |
