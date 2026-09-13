---
description: Architecture workflow — system architecture, tech stack, pipeline implementation, database schema, and operational overview for VLTHR
---

# Architecture Workflow — VLTHR System Overview

## When To Use
- Understanding the complete system architecture
- Learning the pipeline implementation details
- Debugging production issues
- Onboarding new developers (read after setup.md, before adcos.md)
- Reviewing database schema and data flow

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **OS** | Linux (Ubuntu) | Host operating system |
| **Database** | TimescaleDB (PostgreSQL 15) | Time-series data, persistent storage |
| **Pipeline** | Python 3.11 | Portfolio orchestrator, signal generation |
| **Ingestion** | Python 3.11 | OHLCV data fetching from Bybit |
| **Dashboard Backend** | Node.js + Express | Trade monitoring, auto-close logic |
| **Dashboard Frontend** | React + TypeScript, Vite, Zustand | Web UI |
| **AI Engine** | FastAPI | Backtest engine, NVIDIA AI proxy |
| **Infrastructure** | Docker Compose, ngrok | Container orchestration, external access |
| **Data Storage** | Parquet files | OHLCV data (Bybit) |

## Container Stack

| Container | Role | Cadence | Key Processes |
|---|---|---|---|
| `vlthr-postgres` | TimescaleDB (pg15) | Always | Database server, persistent volume `vlthr_pgdata` |
| `vlthr-data-ingestion` | Bybit OHLCV ingestion | 60s | 1m/5m/15m/1h/4h data fetching, gap detection |
| `vlthr-pipeline` | Portfolio orchestrator | 15min | Signal scan, DQS computation, gates, trade creation |
| `vlthr-dashboard-backend` | Express server | 15s | Trade monitoring, auto-close, dynamic SL |
| `vlthr-dashboard-frontend` | Vite SPA build | Always | Web UI (served via nginx) |
| `vlthr-dashboard-redis` | Redis caching | Always | Backend cache layer |
| `vlthr-dashboard-ngrok` | Ngrok tunnel | Always | External access to dashboard |
| `ai_trader_engine` | FastAPI backtest engine | On-demand | Historical backtesting, validation |

## Directory Structure

```
./
├── .env                           # DB_URL, API keys, ngrok config
├── Dockerfile.scheduler           # Data ingestion image
├── data/bybit/                    # Parquet OHLCV data
│   └── workers/
│       ├── data_scheduler.py      # Multi-TF ingestion (5m/15m/1h/4h)
│       ├── minute_scheduler.py    # 1m bar ingestion (60s cadence)
│       └── run_ingestion.sh       # Container entrypoint
├── v3/
│   ├── docs/                      # Documentation
│   ├── Backtest-Engine/           # Backtest tooling
│   ├── departments/               # Strategy research, platform engineering
│   └── paper_trade_unzipped/vlthr-signal-dashboard/
│       ├── engine/                # Core pipeline engine
│       │   ├── portfolio_orchestrator.py   # Main pipeline (1425 lines)
│       │   ├── portfolio_config.py         # All constants/thresholds
│       │   ├── portfolio_gates.py          # RiskBudgetLedger, CorrelationGuard, TradeCountGuard
│       │   ├── v2_filters.py               # Volume/RSI/momentum filters
│       │   ├── safety_layer.py             # Pre/post-flight checks, error logging
│       │   ├── signal_state_manager.py     # Signal lifecycle (ACTIVE/BOOSTED/EXPIRED)
│       │   ├── db_migration.py             # Schema creation/migration
│       │   ├── run_pipeline.py             # Entry point (--loop --interval 900)
│       │   ├── dqs_calibration.py          # DQS→win-rate calibration
│       │   └── calibration_engine.py       # Isotonic regression calibration
│       ├── backend/               # Dashboard backend
│       │   ├── server.cjs         # Express server, trade auto-close logic
│       │   ├── routes/paper.cjs   # Paper trade CRUD + close endpoint
│       │   └── db.cjs             # pg connection pool
│       ├── src/                   # React frontend
│       ├── docker-compose.linux.ngrok.yml
│       ├── docker-compose.linux.yml
│       └── Dockerfile.pipeline    # Pipeline container image
└── .devin/workflows/              # VLTHR workflow definitions
```

## Key Files Reference

| File | Purpose | Lines |
|---|---|---|
| `engine/portfolio_orchestrator.py` | Main pipeline — scan, reprice, gates, trade creation, invariants | 1425 |
| `engine/portfolio_config.py` | All constants — symbols, thresholds, Kelly bands, risk tiers, Bybit mechanics | ~350 |
| `engine/portfolio_gates.py` | RiskBudgetLedger, CorrelationGuard, TradeCountGuard | ~300 |
| `engine/v2_filters.py` | Volume ratio, RSI momentum, daily bias, BTC correlation filters | ~250 |
| `engine/safety_layer.py` | Pre/post-flight checks, error logging (calls conn.rollback()) | ~200 |
| `engine/signal_state_manager.py` | Signal lifecycle management (ACTIVE/BOOSTED/EXPIRED) | ~150 |
| `engine/run_pipeline.py` | Entry point (--loop --interval 900) | ~100 |
| `engine/db_migration.py` | Schema creation/migration | ~150 |
| `backend/server.cjs` | Express server, trade auto-close logic, dynamic SL, live price sync | ~1500 |
| `backend/routes/paper.cjs` | Paper trade CRUD, manual open/close endpoints | ~300 |
| `docker-compose.linux.ngrok.yml` | Main container stack (postgres, ingestion, redis, backend, frontend, ngrok) | ~80 |
| `docker-compose.linux.yml` | Pipeline + AI engine containers | ~40 |

## Pipeline Steps (run_iteration in portfolio_orchestrator.py)

1. **Pre-flight checks** — DB connectivity, data freshness, circuit breaker
2. **Step 0a: Fallback trade monitor** — Safety net SL/TP/time-exit check for OPEN trades
3. **Step 0: Cleanup** — Expire stale signal_state rows (>2h), purge old data
4. **Step 0c: Reconcile risk_ledger** — Close ledger entries with no matching OPEN trade
5. **Step 0d: Shadow decisions** — Resolve recently CLOSED trades
6. **Step 1: Scan symbols** — Load enriched data, compute DQS, apply V2 filters
7. **Step 1c: Reprice PENDING** — Re-validate PENDING trades with fresh DQS, promote to OPEN or expire
8. **Step 2: Signal state** — Upsert into signal_state (ACTIVE/BOOSTED)
9. **Step 3: Rank** — Score and rank signals
10. **Step 4: Gates** — V7 veto, risk budget, correlation guard, trade count guard, directional balance
11. **Step 5: Upsert** — Insert approved signals into high_confidence_signals
12. **Step 5c: Create PENDING** — Auto-create PENDING paper_trades from approved signals
13. **Step 6: Snapshot** — Portfolio snapshot to portfolio_snapshot table
14. **Step 7: Telegram** — Alert for EXCELLENT signals
15. **Step 8: Post-flight** — Safety checks, symbol disable check
16. **Step 9: Invariants** — 6 invariant checks (risk ledger count, max trades, portfolio risk, signal state count, expiry sync, DQS range)

## Trade Lifecycle

```
Ingestion (60s) → Signal Scan (15min) → DQS Computation → Ranking → Gates →
PENDING Creation → Reprice (next run) → PENDING→OPEN Promotion →
Dashboard Auto-Close (15s) OR Pipeline Fallback Monitor (15min) → CLOSED
```

**Dual-layer monitoring:**
- **Primary:** Dashboard backend (server.cjs) every 15s with 1m parquet prices, dynamic SL, liquidation checks
- **Fallback:** Pipeline Step 0a every 15min with 15m close prices, basic SL/TP/time-exit only

## Database Schema (Core Tables)

| Table | Purpose | Key Columns |
|---|---|---|
| `paper_trades` | Trade records | id, signal_id, symbol, side, status (PENDING/OPEN/CLOSED/EXPIRED/CANCELLED), entry_price_actual, sl_price, tp_price, qty_contracts, confidence, exit_price, exit_reason, exit_time_utc, net_pnl_usd, net_pnl_pct, hours_held |
| `high_confidence_signals` | Signal store | id, symbol, confidence, signal_bar_utc, is_expired, expiry_reason, price_at_signal, sl_price, tp_price, strategy, session |
| `signal_state` | Live signal tracking | symbol, signal_id, status (ACTIVE/BOOSTED/EXPIRED), current_dqs, current_entry, current_sl, current_tp |
| `risk_ledger` | Open risk tracking | trade_id, symbol, entry, sl, qty, dollar_risk, risk_pct, is_open, opened_at, closed_at |
| `paper_account` | Account balance | balance, equity, margin_used, open_risk_usd, open_risk_pct |
| `portfolio_snapshot` | Per-run snapshot | run_time, balance, equity, margin_used, open_risk, active_count, daily_count |
| `pipeline_trace` | Per-node execution trace | run_time, node, status, duration_ms, detail |
| `signal_audit_log` | Signal event log | scan, gate, reprice, expire decisions |
| `error_log` | Error log | safety_layer writes here |
| `ingestion_log` | Data ingestion log | symbol, timeframe, rows_added, gaps, completed_at |

## Key Configuration (portfolio_config.py)

| Parameter | Value | Notes |
|---|---|---|
| `SYMBOLS` | BTC, ETH, SOL, XRP, BNB | DOGE disabled (V7: WR=35%) |
| `DQS_THRESHOLDS` | min_to_track=40, min_to_execute=50 | |
| `DQS_VETO_THRESHOLD` | 85 | Kelly=0.0, negative edge |
| `KELLY_V7_BANDS` | dqs_ge_85=0, ge_80=2.5, ge_75=1.5, ge_70=1.0, ge_65=0.7, lt_65=0.3 | |
| `PORTFOLIO.max_active_trades` | 5 | CorrelationGuard cap |
| `PORTFOLIO.max_portfolio_risk_pct` | 10.0 | Total open risk % of balance |
| `MIN_RR_FLOOR` | 2.0 | Minimum risk:reward |
| `MIN_NOTIONAL` | 5.0 | $5 USD minimum for USDT perps |
| `TAKER_FEE` | 0.00055 | 0.055% per side |
| `MMR` | 0.005 | 0.5% maintenance margin |
| `QTY_STEP` | Per-symbol (BTC:0.001, ETH:0.001, SOL:0.1, XRP:1, BNB:0.01) | |
| `SIGNAL_LIFECYCLE.max_age_hours` | 2.0 | Raw signal expiration |
| `SIGNAL_LIFECYCLE.max_trade_hours` | 12.0 | Max hold before TIME_EXIT |

## Risk Tiers

| Tier | DQS Range | risk_pct | sl_mult | tp_mult | rr |
|---|---|---|---|---|---|
| excellent | 75-100 | 5.0% | 3.0 | 9.0 | 3.00 |
| good | 65-74 | 3.0% | 3.0 | 7.5 | 2.50 |
| fair | 50-64 | 2.0% | 3.0 | 6.0 | 2.00 |

## DQS Weighting (V7)

```
DQS_WEIGHTS = {technical: 0.00, structure: 0.00, context: 1.00}
```

Currently only context domain is weighted. Technical and structure scores are computed but not used in DQS.

## Critical Gotchas (Top 15)

1. **`safety.log_error()` calls `conn.rollback()`** — If you're in a loop updating multiple trades and one errors, `log_error` rolls back ALL uncommitted updates. **Fix:** commit after each successful update/promotion/expiry in loops.

2. **DQS ≥ 85 = Kelly 0.0 = veto** — V7 Kelly bands intentionally set `dqs_ge_85: 0.0` because calibration showed negative edge at high DQS. These signals are vetoed in both gate (Step 4) and reprice (Step 1c).

3. **`RISK_TIERS.excellent.max_dqs` must be 100, not 84** — If set to 84, DQS 85+ falls through to "none" tier with `sl_mult=0`, causing `SL = entry` → `ValueError: LONG SL >= entry`.

4. **V2 filters should NOT be re-applied during reprice** — They're applied during scan (Step 1). Re-applying in reprice causes PENDING trades to expire when market conditions haven't changed.

5. **Trade monitoring is dual-layer** — Primary: dashboard backend (server.cjs) every 15s. Fallback: pipeline Step 0a every 15min. If dashboard goes down, pipeline catches trades within 15min.

6. **Dynamic SL adjustment syncs both `paper_trades` and `risk_ledger`** — `server.cjs:1267-1281` chains a `risk_ledger` UPDATE after the `paper_trades` UPDATE succeeds.

7. **`signal_strength` column does NOT exist** in `paper_trades`. Only `confidence` (integer 0-100).

8. **DOGEUSDT is disabled** (`DISABLED_SYMBOLS = ["DOGEUSDT"]`) due to V7 calibration showing 35% win rate and net negative PnL.

9. **DQS weights are currently context-only** — Technical and structure scores are computed but zero-weighted. This may change when D1-D7 calibration is run.

10. **Pipeline code is mounted read-only in Docker** — Code changes require container rebuild: `docker compose -f docker-compose.linux.yml up -d --build pipeline`.

11. **UFW blocks Docker inter-container + outbound traffic by default** — If UFW is active, Docker's custom bridge networks get blocked. Fix: add iptables ACCEPT rules to DOCKER-USER chain for the bridge interface.

12. **Docker container stop/kill often fails with "permission denied"** — Workaround: `docker inspect <name> --format '{{.State.Pid}}'` → `sudo kill -9 <pid>` → `docker rm -f <name>` → rebuild.

13. **`paper_trades` has no `entry_time` column** — Use `created_at` for time-in-trade calculations. Schema has 58 columns — always check `information_schema.columns` before writing queries.

14. **VLTHR_ROOT must NOT include `v3/`** — Docker-compose mounts `$VLTHR_ROOT/v3/departments` and `$VLTHR_ROOT/.env`. Including `v3/` causes path doubling.

15. **DB equity ≠ true equity** — `paper_account.equity` is set to `available = wallet_balance - margin_used`. True mark-to-market equity (`wallet_balance + floating_pnl`) is only computed in SSE enrichment and never persisted.

## Commands Quick Reference

```bash
# Start everything
export VLTHR_ROOT="."
export DASHBOARD_ROOT="$VLTHR_ROOT/paper_trade_unzipped/vlthr-signal-dashboard"
cd $DASHBOARD_ROOT
docker compose -f docker-compose.linux.ngrok.yml up -d --build
docker compose -f docker-compose.linux.yml up -d --build

# Check container status
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep vlthr

# Pipeline logs (last 50 lines)
docker logs --tail 50 vlthr-pipeline

# Ingestion logs
docker logs --tail 20 vlthr-data-ingestion

# Dashboard backend logs (trade monitoring)
docker logs --tail 20 vlthr-dashboard-backend

# DB query - open trades
docker exec vlthr-postgres psql -U postgres -d postgres -c "SELECT pt.id, pt.symbol, pt.side, pt.status, pt.confidence, pt.entry_price_actual, pt.sl_price, pt.tp_price, rl.dollar_risk FROM paper_trades pt LEFT JOIN risk_ledger rl ON rl.trade_id = pt.id WHERE pt.status = 'OPEN'"

# DB query - account balance
docker exec vlthr-postgres psql -U postgres -d postgres -c "SELECT * FROM paper_account ORDER BY id DESC LIMIT 1"

# DB query - recent errors
docker exec vlthr-postgres psql -U postgres -d postgres -c "SELECT * FROM error_log WHERE created_at > NOW() - INTERVAL '3 hours' ORDER BY created_at DESC LIMIT 10"

# Rebuild pipeline after code change
docker compose -f docker-compose.linux.yml up -d --build pipeline

# Restart just the pipeline
docker restart vlthr-pipeline

# Run test suite (32 tests)
cd $DASHBOARD_ROOT && python3 -m pytest engine/test_risk_logic.py -v
```

## Current System State Template

Fill this in when checking system health:

### Container Status
- vlthr-postgres: [healthy/unhealthy]
- vlthr-data-ingestion: [healthy/unhealthy]
- vlthr-pipeline: [healthy/unhealthy]
- vlthr-dashboard-backend: [healthy/unhealthy]
- vlthr-dashboard-frontend: [healthy/unhealthy]
- vlthr-dashboard-redis: [healthy/unhealthy]
- vlthr-dashboard-ngrok: [healthy/unhealthy]
- ai_trader_engine: [healthy/unhealthy]

### Database State
- paper_trades: [X] OPEN, [Y] CLOSED, [Z] EXPIRED
- paper_account: Balance $[amount], realized_pnl $[amount]
- risk_ledger: [X] open entries, total dollar_risk ~$[amount]
- signal_state: [X] ACTIVE, [Y] BOOSTED
- Ingestion: [status], gaps per symbol

### Recent Activity (Last 24h)
- New signals created: [X]
- New trades opened: [X]
- Trades closed: [X] (reasons: SL_HIT, TP_HIT, TIME_EXIT)
- PnL (24h): $[amount]
- Win rate (24h): [X]%

### Known Issues
- [List any current issues or warnings]

### Next Steps
- [Any actions needed]
