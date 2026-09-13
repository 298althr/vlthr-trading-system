# VLTHR Project Overview

**Date**: 2026-07-24 23:30 UTC
**Author**: Cascade (AI coding agent)
**Status**: Active development. Pipeline running in dual execution mode (paper + Bybit demo). All 6 validation gates PASS after bug fix cycle.

---

## 1. Executive Summary

VLTHR is an algorithmic crypto trading system. It scans 5 cryptocurrency symbols every 5 minutes, scores each signal through a 12-gate quality and risk pipeline, and places trades on both a paper account and a Bybit demo account. The system has been in development for 8 to 9 months.

### What It Does

- Ingests OHLCV market data from Bybit every 60 seconds (1m, 5m, 15m, 1h, 4h timeframes)
- Scans for trading signals every 5 minutes using technical indicators, pattern detection, and regime classification
- Scores each signal 0 to 100 using a Dynamic Quality Score (DQS) with three domains: technical (40%), structure (30%), context (30%)
- Passes signals through 12 sequential gates that check regime fit, risk budget, correlation, trade count, directional balance, and calibration probability
- Sizes positions using V7 Kelly bands scaled by DQS tier, with a 3x boost for mean reversion strategies
- Places market orders on Bybit demo, then sets stop-loss and take-profit via a separate API call
- Monitors open trades every 15 seconds for SL, TP, time exit, and liquidation
- Adjusts stop-loss dynamically using three rules: break-even, time decay, and ATR expansion

### Results So Far

- 1-year backtest (Jul 2025 to Jul 2026): 872 trades, profit factor 2.86, win rate 42.9%, $10K to $127K, max drawdown 6.22%
- All 6 validation gates PASS (win rate, profit factor, expectancy, drawdown, expired rate, TP hit rate)
- Live Bybit demo: 9 trades placed over 4 days, net PnL +$1,252.71
- Live paper trading: 43 trades over 10 days, net PnL -$633.65
- Divergence between paper and Bybit is driven by position sizing differences ($10K paper vs $44K Bybit) and SL fill price differences

### Current State

6 Docker services running and healthy: postgres, redis, data-ingestion, pipeline, backend, frontend. Bug fix cycle completed on July 24, 2026. All code reviewed. 60 unit tests pass. Pipeline ready for next live validation run.

---

## 2. Project Timeline (Process Log)

### Phase 0: Foundation (Oct 2025 to Jan 2026)

Built the core system from scratch. Data ingestion, signal scanning, DQS scoring, paper trade execution, and the React dashboard.

**Milestones:**
- Bybit OHLCV ingestion service with 60-second cadence
- Pipeline engine with adaptive scorer, regime detection, and portfolio gates
- Paper trading system with Postgres storage and Express backend
- React frontend with dashboard, paper trades, signals, and watchlist pages
- Docker Compose orchestration for all services
- Telegram bot integration for alerts and commands

**Key decisions:**
- Chose 15m bars as execution timeframe, 4h as context timeframe
- Built DQS as a 0-100 composite score with fixed domain weights (40/30/30)
- Used V7 Kelly bands for position sizing instead of fixed percentage
- Set max active trades to 8, max portfolio risk to 12%

### Phase 1: Architecture Refactor (Jan to Jun 2026)

Decoupled the scanner from the risk gate. Moved V7 Kelly veto from scanner to risk layer. Fixed equity accounting, closed-trade logging, and SL/TP recomputation at reprice.

**Milestones:**
- Scanner now emits all signals. Risk gate sizes or blocks.
- V2 filters removed from reprice step (only applied at scan)
- Equity accounting fixed (margin_used added back for drawdown checks)
- 6-month backtest: 1,441 trades, PF 1.56, WR 56.4%, all 6 gates PASS
- Stress tested across 15 configurations (5 stages x 3 modes)
- Crowding detection added: 2+ correlated symbols firing same direction triggers 50% Kelly reduction
- Data quality gate added: freshness, completeness, outlier score. Rejects if score < 60

**Key decisions:**
- V7 veto validated: DQS >= 85 signals have 46.8% WR (worse than overall). Kelly = 0 for DQS >= 85 stays.
- Crowding clusters hardcoded: BTC/ETH/SOL and BTC/ETH/XRP (min 2 matching)
- Treatment B adopted: crowding + DQ gate. Within 0.3% of control. Safe to adopt.

### Phase 2: Live Deployment Prep (Jul 13 to Jul 22, 2026)

Removed frontend authentication. Fixed zero-signal bugs. Optimized pattern detection. Ran 1-year backtest. Implemented dual execution with Bybit demo.

**Milestones:**

**Jul 13-14**: Frontend auth removal, Docker network recovery after AppArmor issues.

**Jul 14-15**: Fixed 4 bugs causing zero signal approval:
1. V2 RSI momentum filter blocked mean_reversion signals (CRITICAL)
2. ETH rsi_oversold=0 prevented LONG entries
3. DQ freshness threshold too strict for 15m bars (15 min to 45 min)
4. Pre-flight bar_max_age_min too strict (10 min to 20 min)

**Jul 15-17**: Risk sizing fix. Removed $2K cap that was limiting position sizes. PF improved from 1.56 to 2.83.

**Jul 18-19**: Built Bybit demo executor (313 lines, HMAC-SHA256, V5 API). Implemented dual execution: every approved signal trades on both paper and Bybit demo. Added 14 new database columns for Bybit trade tracking. Built 3 new API endpoints for Bybit trade data.

**Jul 19**: WebSocket service for real-time market data (280 lines).

**Jul 19-20**: Pattern detection optimization. Reduced processing time from >60 seconds to 14.4 seconds per symbol. Replaced scipy linregress with inline numpy. Replaced O(n^2) pivot scan with O(log n) bisect cache lookup.

**Jul 20-22**: 1-year backtest (893 trades, PF 2.21, WR 32.8%, $10K to $105K). Parameter optimization: SL time decay 0.25 to 0.15, regime DQS filter TF/MIXED >= 65. Optimized PF 2.54.

**Jul 22**: Pipeline go-live. First Bybit demo order confirmed (SOLUSDT LONG, orderId=286c6af8). Dual execution verified.

### Phase 3: Bug Fix Cycle (Jul 22 to Jul 24, 2026)

Live monitoring revealed 7 critical bugs. Performance review conducted. All bugs fixed. Full backtest revalidation passed all gates.

**Bugs found and fixed:**

| ID | Bug | Impact | Fix |
|----|-----|--------|-----|
| V1 | Backtest SL_TIME_DECAY_RATE hardcoded 0.25 vs live 0.15 | Backtest validated wrong parameters | Import from portfolio_config |
| V3 | Backtest runner missing REGIME_STRATEGY_MIN_DQS gate | Could not re-validate live params | Import and apply gate in backtest |
| D13 | Symbol disable evaluates all historical trades | 3 of 5 symbols permanently disabled | Only evaluate trades after last disabled_at |
| R1 | Daily loss breaker at -2.0% too tight | 732 rejections in 2 days, trade starvation | Raised to -3.0% |
| E2 | Bybit SL placement fails on fast moves | Order rejected when price past SL | Split into market order + set_trading_stop |
| S2 | V2 daily bias filter blocks all SHORTs | System structurally LONG-only | 4h log return override: allow SHORT if log_ret < -1.5% |
| S3 | TP distances too far (tp_mult=4.0) | TP hit rate 30% (backtest), 11% (live) | Reduced to 3.0 for all symbols |

**Backtest results after fixes:**

| Metric | Before | After |
|--------|--------|-------|
| Profit factor | 2.21 | 2.86 |
| Win rate | 32.8% | 42.9% |
| Sharpe | 45.39 | 63.98 |
| TP hit rate | 29.9% (FAIL) | 41.4% (PASS) |
| SHORT trades | 16 | 249 |
| Max drawdown | 6.97% | 6.22% |
| Gates passed | 5/6 | 6/6 |

**Code review completed**: 60 unit tests pass (49 risk logic + 11 pattern tests). CITM review gate: PASS. No blockers, no critical issues.

---

## 3. Technical Architecture

### 3.1 Service Stack

All services run in Docker containers on a single host, connected via a bridge network.

| Service | Image | Port | Role |
|---------|-------|------|------|
| Postgres | timescale/timescaledb:pg15 | 5432 (internal) | Database. 67 tables. TimescaleDB extensions. |
| Redis | redis:7-alpine | 6379 (internal) | Cache for backend. 256MB maxmemory, LRU eviction. |
| Data Ingestion | Custom Python | None | OHLCV ingestion from Bybit API. 60s cadence for 1m, periodic for higher timeframes. |
| Pipeline | Custom Python | 8201 | Signal engine. 5-min loop. Scans, scores, gates, and places trades. |
| Backend | Node.js Express | 3003 | API server + trade monitor. Checks open trades every 15s. |
| Frontend | React + nginx | 5175 | SPA dashboard. 7 pages. PWA installable on mobile. |
| Bybit WS | Custom Python | None | Real-time WebSocket market data. |
| Telegram | Custom Python | None | 3-bot polling service for alerts and commands. |

### 3.2 Data Flow

```
Bybit API
    |
    v
Data Ingestion (60s) --> Parquet files (/data/bybit/)
    |
    v
Pipeline Scan (5min) --> DQS scoring --> Regime detection --> Pattern detection
    |
    v
12 Portfolio Gates --> PENDING paper trades
    |
    v
Reprice (next 5min cycle) --> OPEN trades --> Bybit market order + SL/TP
    |
    v
Backend Monitor (15s) --> SL/TP/Time/Liq checks --> CLOSED
    |
    v
Closed trade outcomes --> Next scan cycle (feedback loop)
```

### 3.3 Trade Lifecycle

1. **Signal generation**: Scanner produces a signal with direction (LONG/SHORT), DQS score, regime, and strategy.
2. **Pending**: Signal passes all 12 gates. Paper trade created with PENDING status.
3. **Reprice**: Next 5-min cycle. If DQS still valid and price confirmed, trade promoted to OPEN. Market order placed on Bybit. SL/TP set via separate API call.
4. **Monitoring**: Backend checks every 15 seconds against live parquet prices. Dynamic SL adjusts stop-loss based on break-even, time decay, and ATR expansion.
5. **Exit**: Trade closes via SL_HIT, TP_HIT, TIME_EXIT (12h max hold), or LIQUIDATION.
6. **Reconciliation**: Pipeline fetches Bybit positions every 5 minutes. If position gone, fetches close price and records PnL.

### 3.4 Codebase Structure

```
DEVOPS/
  docker-compose.yml          Master orchestration
  .env                        Production secrets (not committed)
  data/bybit/                 Parquet OHLCV data (612MB, self-contained)
  data-ingestion/             Bybit OHLCV ingestion workers
  pipeline/engine/            Signal engine (all Python code)
    portfolio_config.py       All tunable parameters
    portfolio_orchestrator.py Main pipeline entry point
    adaptive_scorer.py        DQS scoring
    portfolio_gates.py        Risk gates (correlation, trade count, risk budget)
    v2_filters.py             V2 signal filters (daily bias, RSI momentum)
    dynamic_sl.py             Dynamic stop-loss logic
    bybit_executor.py         Bybit V5 REST API wrapper
    bybit_ws.py               WebSocket market data service
    patterns/                 Pattern detection (9 patterns, 46 columns)
      pivot_detector.py       Fractal pivot detection with O(log n) cache
      trend_lines.py          Wedge, triangle, channel, trend break
      support_resistance.py   Horizontal S/R, dynamic S/R, BoS/CHoCH
  backtest/                   Backtest runner and stress tests
    backtest_runner.py        Bar-by-bar backtest engine
    stat_gates.py             Validation gate checks
  backend/src/                Express API + trade monitor
    server.cjs                Main server, trade monitoring, Bybit endpoints
  frontend/src/               React SPA
    App.tsx                   Main dashboard (auth removed)
  postgres/db_migration.py    Schema creation and migration
  telegram/                   3-bot Telegram polling service
  docs/                       Documentation
  workflows/                  VLTHR workflow definitions (33 files)
```

### 3.5 Technology Stack

| Layer | Technology |
|-------|-----------|
| Language (pipeline) | Python 3.11 |
| Language (backend) | Node.js 22 (CommonJS) |
| Language (frontend) | TypeScript |
| Frontend framework | React 19, Vite |
| Frontend styling | TailwindCSS, Lucide icons |
| Database | TimescaleDB (PostgreSQL 15) |
| Cache | Redis 7 |
| Container | Docker Compose |
| Data format | Parquet (pyarrow) |
| Exchange API | Bybit V5 REST + WebSocket |
| Charts | Chart.js |
| State management | Zustand |

---

## 4. Process Maps

### 4.1 Pipeline Gate Sequence (12 Gates)

Every signal must pass all 12 gates in order. One rejection stops the signal.

```
Gate 0A: EXPIRED        Signal must be less than 2 hours old
Gate 0B: STALE          Data must be fresh (bar_max_age 20 min)
Gate 0C: REGIME         TF/MIXED signals need DQS >= 65
Gate 0D: REGIME_ROUTER  Strategy must match regime
Gate 1:  RANKER         DQS must be >= 50
Gate 2:  CONFIRMED      DQS trajectory must not be declining (2 consecutive drops)
Gate 3:  DAILY_BREAKER  Daily PnL must be above -3.0%
Gate 4A: CORRELATION    No existing position on same symbol
Gate 4B: DIRECTIONAL    Max 5 same-direction trades
Gate 4C: RISK_BUDGET    Total open risk must be <= 12%
Gate 4D: TRADE_COUNT    Daily limit 10, session limits 4
Gate 5:  CALIBRATION    Calibrated probability must be >= 0.40
```

### 4.2 DQS Scoring Process

```
Input: OHLCV data for 1 symbol at 1 bar
    |
    +-- Technical domain (40%)
    |     RSI, ADX, MACD, Bollinger Band position, ATR rank
    |
    +-- Structure domain (30%)
    |     Pattern detection (9 patterns), support/resistance proximity,
    |     trend line break/retest, BoS/CHoCH
    |
    +-- Context domain (30%)
    |     Regime classification (TRENDING/MIXED/VOLATILE),
    |     session quality, symbol quality score, funding rate
    |
    v
DQS score (0-100) --> Kelly band selection --> Position sizing
```

### 4.3 Position Sizing Process

```
DQS score
    |
    v
Kelly band selection:
  DQS >= 85  -->  Kelly = 0.0  (vetoed, blocked)
  DQS >= 80  -->  Kelly = 2.5x
  DQS >= 75  -->  Kelly = 1.5x
  DQS >= 70  -->  Kelly = 1.0x
  DQS >= 65  -->  Kelly = 0.7x
  DQS <  65  -->  Kelly = 0.3x
    |
    v
Strategy boost:
  mean_reversion  -->  Kelly x 3.0
  trend_following -->  Kelly x 1.0
    |
    v
Crowding check:
  2+ correlated symbols same direction within 5 DQS  -->  Kelly x 0.5
    |
    v
Risk percentage = base_risk_pct x Kelly (capped at portfolio limits)
    |
    v
Position qty = balance x risk_pct / sl_distance
```

### 4.4 Dynamic Stop-Loss Process

Three rules applied in order. Each can only tighten the SL (move it toward entry), never loosen it.

```
Rule 1: BREAK-EVEN
  If floating PnL > 50% of TP distance:
    Move SL to entry price (LONG: entry x 0.997, SHORT: entry x 1.003)

Rule 2: TIME_DECAY
  If trade open > 3 hours:
    Tighten SL by (0.15 x ATR x hours_open_minus_3)
    SL moves toward entry as trade ages

Rule 3: ATR_EXPANSION
  If live ATR > 1.5x entry ATR:
    Widen SL to accommodate increased volatility
    (This is the only rule that can widen SL)
```

---

## 5. Key Parameters (Current Production Config)

All parameters live in `pipeline/engine/portfolio_config.py`.

### 5.1 Symbols

| Symbol | Strategy | sl_mult | tp_mult | risk_pct | max_hold_hours |
|--------|----------|---------|---------|----------|----------------|
| BTCUSDT | trend_following | 2.5 | 3.0 | 5% | 12 |
| ETHUSDT | mean_reversion | 2.0 | 3.0 | 10% | 12 |
| SOLUSDT | trend_following | 2.5 | 3.0 | 5% | 12 |
| XRPUSDT | mean_reversion | 2.5 | 3.0 | 5% | 12 |
| BNBUSDT | mean_reversion | 2.0 | 3.0 | 5% | 12 |

DOGEUSDT is disabled (WR 35%, net negative PnL in backtest).

### 5.2 Risk Limits

| Parameter | Value |
|-----------|-------|
| Max active trades | 8 |
| Max daily trades | 10 |
| Max portfolio risk | 12% |
| Max same-direction trades | 5 |
| Max leverage | 4x |
| Daily loss halt | -3.0% |
| Account drawdown halt | -20% |
| Symbol allocation cap | 25% |
| Symbol PnL cap | 30% |
| Min risk:reward | 1.0 |

### 5.3 Signal Lifecycle

| Parameter | Value |
|-----------|-------|
| Scan interval | 300 seconds (5 min) |
| Signal max age | 2 hours |
| Max trade hold | 12 hours |
| DQS decline threshold | 2 consecutive drops = expire |
| Bar max age | 20 minutes |
| Calibration gate | 0.40 |

### 5.4 Frozen Parameters

These parameters require human approval and a new backtest before changing:

- Kelly V7 bands (0.0, 2.5, 1.5, 1.0, 0.7, 0.3)
- MR boost 3x
- Calibration gate 0.40
- Crowding reduction 50%
- DQ gate threshold 60
- Max active trades 8
- Scan interval 300s
- Max leverage 4x
- Max portfolio risk 12%
- SL_TIME_DECAY_RATE 0.15
- REGIME_STRATEGY_MIN_DQS TF/MIXED = 65

---

## 6. Decisions Made

### Go-Live Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Go live with TD 0.15 + DQS 65 filter | Backtest showed PF improvement 2.21 to 2.54 |
| D5 | Veto DQS >= 85 signals (Kelly = 0) | 46.8% WR at high DQS. Worse than overall average. |
| D6 | MR boost 3x | Concentration balance between TF and MR strategies |
| D8 | Daily loss breaker at -3.0% (raised from -2.0%) | -2% too tight for 43% WR strategy. Single SL hit could trip it. |
| D9 | TP multipliers at 3.0x (reduced from 4.0x) | MFE averages 2.54 ATR. TP at 3.0 ATR puts it within reach 79% of the time. |

### Architectural Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D3 | Disable regime strategy override | HMM hurts OOS PF by 20%. Classifies 77-80% as TRENDING. |
| D11 | Direction = 4h log return sign | Simple but noisy on 15m bars. No multi-timeframe confirmation. |
| D12 | V2 daily bias filter with SHORT override | Allow SHORT if 4h log return < -1.5%, even in bull market. Unlocked 249 SHORT trades (was 16). |
| D13 | Symbol disable with grace period | Only evaluate trades after last disabled_at. Prevents deadlock. |

---

## 7. Validation Gates

All 6 gates must pass for the system to be considered production-ready.

| Gate | Target | Latest Backtest Result | Status |
|------|--------|----------------------|--------|
| Win rate | >= 30% | 42.9% | PASS |
| Profit factor | >= 1.5 | 2.86 | PASS |
| Expectancy | > $0 | $133.94 | PASS |
| Max drawdown | < 15% | 6.22% | PASS |
| Expired rate | < 30% | 0.7% | PASS |
| TP hit rate | >= 40% | 41.4% | PASS |

Unit tests: 60 pass (49 risk logic + 11 pattern tests).

---

## 8. Open Items

### Requires Action

1. **ETHUSDT calibration**: 0 approved trades in backtest due to calibration probability < 0.40. Needs recalibration or threshold adjustment.
2. **Live validation run**: Need 30+ live trades for statistical significance. Current live sample is too small to draw conclusions.
3. **Paper vs Bybit SL divergence**: Paper monitor uses parquet OHLCV (wider spreads). Bybit uses actual fill prices. Paper PnL is unreliable for decision-making.

### Deferred

1. **Walk-forward optimization** (V4): Infrastructure exists but is empty. Session and regime parameter sets not populated.
2. **Live validation pipeline** (V5): No automated comparison between backtest expectations and live results.
3. **Portfolio diversification** (P1, P5): All 5 symbols are correlated crypto. No non-crypto assets or hedging.
4. **Volatility targeting** (P2): No mechanism to reduce position size when volatility spikes.
5. **Stress testing** (R5): No scenario analysis for flash crashes, correlation breakdown, or funding spikes.

---

## 9. Document References

| Document | Location | Purpose |
|----------|----------|---------|
| README.md | `/README.md` | Setup, operations, and quick start guide |
| Architecture | `/docs/ARCHITECTURE.md` | System architecture and service details |
| Agent Handoff | `/docs/AGENT_HANDOFF.md` | Handoff for incoming engineers and agents |
| Performance Review | `/docs/BYBIT_PERFORMANCE_REVIEW_JUL24.md` | Full performance review with gap analysis |
| Session Plan | `/docs/SESSION_PLAN_JUL24.md` | Bug fix session plan and execution log |
| Backtest Methodology | `/backtest/METHODOLOGY.md` | Backtest procedure and validation gates |
| Regime Analysis | `/exports/regime_analysis_1year.md` | 1-year regime feature analysis |
| CCP | `/docs/workflows/CCP Content Communication Protocol.md` | Writing and communication standards |
| PIVP | `/docs/workflows/PIVP Master Validation Protocol.md` | Post-implementation validation protocol |

---

*Document created: 2026-07-24 23:30 UTC. Update this timestamp when the document is modified.*
