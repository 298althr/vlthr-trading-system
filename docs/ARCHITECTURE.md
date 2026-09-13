# VLTHR System Architecture

## Overview

VLTHR is an algorithmic paper-trading system that scans crypto markets for trading opportunities,
scores them using an adaptive quality scoring engine, and manages trades through a full lifecycle:
Signal → Pending → Open → Closed.

**Current status**: Phase 1 and Phase 2 complete. All 6 validation gates pass. Frontend authentication removed. 6 core services running; ngrok and telegram stopped at user request. Live paper trading under audit due to recent losing streak.

## Container Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                    vlthr-dashboard-net (bridge)                  │
│                                                                  │
│  ┌──────────┐  ┌─────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Postgres  │  │  Redis  │  │ Data         │  │ Pipeline     │ │
│  │ (pg15)    │  │ (cache) │  │ Ingestion    │  │ (15min loop) │ │
│  │ :5432     │  │ :6379   │  │ (60s cadence)│  │ :8200        │ │
│  └────┬─────┘  └────┬────┘  └──────┬───────┘  └──────┬───────┘ │
│       │              │              │                  │         │
│       │     ┌────────┴──────────────┤                  │         │
│       │     │  ┌────────────────────┤                  │         │
│       │     │  │                    │                  │         │
│  ┌────┴─────┴──┴──┐  ┌───────────┐  │  ┌────────────┐  │         │
│  │ Backend         │  │ Frontend  │  │  │ AI Trader  │  │         │
│  │ (Express)       │──│ (nginx)   │  │  │ Engine     │  │         │
│  │ :3002→3001      │  │ :5174→80  │  │  │ :8001→8000 │  │         │
│  └─────────────────┘  └─────┬─────┘  │  └────────────┘  │         │
│                             │        │                   │         │
│                        ┌────┴───┐    │  ┌────────────┐  │         │
│                        │ Ngrok  │    │  │ Telegram   │  │         │
│                        │ :4040  │    │  │ (3 bots)   │  │         │
│                        └────────┘    │  └────────────┘  │         │
└──────────────────────────────────────┴──────────────────┘─────────┘
```

## Currently Deployed Services (6 of 9 Active)

| Service | Container | Status | Notes |
|---|---|---|---|
| Postgres | vlthr-postgres | Healthy | 67 tables restored from backup |
| Redis | vlthr-dashboard-redis | Running | Cache for backend |
| Data Ingestion | vlthr-data-ingestion | Healthy | 60s cadence, 5 symbols |
| Pipeline | vlthr-pipeline | Healthy | 15-min loop, processing signals |
| Backend | vlthr-dashboard-backend | Running | Express API + trade monitor |
| Frontend | vlthr-dashboard-frontend | Running | React SPA on nginx; Google TOTP auth removed |
| Ngrok | vlthr-dashboard-ngrok | Stopped | User requested shutdown |
| Telegram | vlthr-telegram | Stopped | User requested shutdown; API unreachable |
| AI Trader Engine | ai_trader_engine | Not deployed | Available on demand |

## Service Details

### Postgres (TimescaleDB pg15)
- **Image**: `timescale/timescaledb:latest-pg15`
- **Container**: `vlthr-postgres`
- **Volume**: `vlthr_pgdata` (persistent database storage)
- **Schema**: 67 tables restored from `backups/20260710_142836/postgres_dump.sql`
- **Key tables**: `paper_trades`, `high_confidence_signals`, `signal_state`, `risk_ledger`, `paper_account`, `portfolio_snapshot`, `pipeline_trace`, `signal_audit_log`, `error_log`, `ingestion_log`, `decision_events`, `signal_node_log`, `confidence_matrix`, `calibration_log`, `symbol_status`

### Redis
- **Image**: `redis:7-alpine`
- **Container**: `vlthr-dashboard-redis`
- **Config**: `redis.conf` with 256mb maxmemory, LRU eviction, no persistence
- **Used by**: Backend for caching

### Data Ingestion
- **Container**: `vlthr-data-ingestion`
- **Cadence**: 60 seconds (1m bars), periodic for higher timeframes
- **Processes**: `data_scheduler.py` (5m/15m/1h/4h + funding/OI), `minute_scheduler.py` (1m), `scheduler_monitor.py` (Telegram alerts)
- **Data path**: Writes parquet files to `/app/data/bybit/`
- **Symbols**: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT (DOGEUSDT disabled)

### Pipeline (Signal Engine)
- **Container**: `vlthr-pipeline`
- **Cadence**: 15-minute loop (`--interval 900`)
- **Entry point**: `engine.run_pipeline --loop --interval 900`
- **Health**: HTTP `/health` on port 8200
- **Engine code**: Volume-mounted read-only from `pipeline/engine/`
- **Current pipeline steps** (post-Phase 1 & 2):
  1. Pre-flight checks (DB, data freshness, circuit breaker)
  2. Cleanup (expire stale signals)
  3. Risk ledger reconciliation
  4. Shadow decision resolution
  5. Scan symbols (DQS scoring, regime detection) — scanner emits ALL signals, no veto
  6. Reprice PENDING signals (promote to OPEN or expire) — no V2 filters, crowding reduction applied
  7. Signal state tracking
  8. Ranking
  9. Gates (risk budget, correlation, trade count, directional balance, V7 veto as sizing rule, data quality gate, crowding detection)
  10. Upsert approved signals
  11. Create PENDING paper trades
  12. Portfolio snapshot
  13. Telegram alerts
  14. Post-flight safety checks
  15. Invariant checks
  16. Intent-vs-outcome logging (Phase 2)

### Backend (Express API + Trade Monitor)
- **Container**: `vlthr-dashboard-backend`
- **Port**: 3002→3001
- **Auth**: TOTP authentication routes removed (`routes/auth.cjs` and `auth/totp.cjs` no longer mounted)
- **Trade monitoring**: Every 15s, checks OPEN trades against live parquet prices
- **Auto-close**: SL_HIT, TP_HIT, TIME_EXIT, LIQUIDATION
- **Dynamic SL**: `computeDynamicSL` adjusts SL based on price trajectory
- **Flash-wick guard**: Bar-to-bar comparison (not entry-based) to prevent false triggers

### Frontend (Vite SPA)
- **Container**: `vlthr-dashboard-frontend`
- **Port**: 5174→80 (nginx)
- **Stack**: React 19, TypeScript, Vite, Zustand, Lucide icons
- **Auth**: Google TOTP authentication removed; dashboard opens directly
- **Build**: Builder image switched to `node:22-slim`; asset filenames include build timestamp for cache busting
- **PWA**: Installable on mobile via manifest.json + service worker
- **Pages**: Dashboard, Paper Trades, Signals, Watchlist, Backtest, VTC, Power

### Ngrok
- **Container**: `vlthr-dashboard-ngrok`
- **Port**: 4040 (inspector)
- **Purpose**: Public HTTPS tunnel for mobile/PWA access
- **Config**: Static domain via NGROK_DOMAIN env var

### AI Trader Engine (not currently deployed)
- **Container**: `ai_trader_engine`
- **Port**: 8001→8000
- **Stack**: FastAPI, uvicorn
- **Purpose**: Backtest engine with AI chat panel
- **Resource limits**: 2 CPUs, 1GB RAM

### Telegram (3-Bot Architecture)
- **Container**: `vlthr-telegram`
- **Bots**: Data bot, Trader bot, DevOps bot
- **Polling**: Concurrent polling for all 3 bots
- **Commands**: /status, /trades, /close, /balance, /errors, etc.
- **Alerts**: Signal alerts, execution alerts, error alerts, hourly ingestion reports

## Data Flow

```
Bybit API
    │
    ▼
Data Ingestion (60s) ──→ Parquet files (/data/bybit/)
    │
    ▼
Pipeline Scan (15min) ──→ Enriched data ──→ Quality scoring
    │
    ▼
Gates (risk budget, correlation, crowding, data quality) ──→ PENDING trades
    │
    ▼
Reprice (next run) ──→ OPEN trades ──→ Risk ledger entry
    │
    ▼
Backend Monitor (15s) ──→ SL/TP/Time/Liq checks ──→ CLOSED
    │
    ▼
Closed trade outcomes ──→ Intent vs outcome logged (Phase 2)
    │
    ▼
Next scan cycle (feedback loop)
```

## What Changed in Phase 1

- V7 Kelly veto moved from scanner to risk gate (scanner emits all signals; risk gate sizes or blocks)
- Equity accounting fixed (margin_used added back for drawdown checks)
- V2 filters removed from reprice step
- Closed-trade logging fixed (trades removed from open_trades list after close)
- SL/TP recomputation at reprice fixed (uses max(reprice_dqs, 50) to avoid None returns)

**Result**: Identical trade outcomes to control — same 1,441 trades, same win rate, same PnL. The refactor changed code structure without changing behavior.

## What Changed in Phase 2

- **Crowding detection**: When 2+ correlated symbols fire same direction with quality scores within 5 points, position size is reduced by 50%. Applied at both risk gate and reprice stages.
- **Data quality scoring**: Computes freshness (40%), completeness (40%), outlier score (20%) per signal. Rejects if score < 60.
- **Intent-vs-outcome logging**: Every signal logs intent (veto/reject/trade), outcome (not_traded/win/loss/expired), crowded flag, and data_quality score.
- **Crowding reduction at reprice**: Reprice recalculates position sizing from scratch, so crowding reduction is re-applied for crowded pending trades.

**Result**: 252 crowded trades identified. PnL reduced by $42.43 (0.5%) — the expected cost of risk management. Crowded signals had 55.6% win rate vs 56.6% for non-crowded, validating the reduction.

## Key Parameters (Current Production Config)

All parameters live in `pipeline/engine/portfolio_config.py`:

| Parameter | Value | Notes |
|---|---|---|
| Kelly band for quality ≥ 85 | 0.0 (blocked) | Validated by stress test |
| Kelly band for quality ≥ 80 | 2.5x | |
| Kelly band for quality ≥ 75 | 1.5x | |
| Kelly band for quality ≥ 70 | 1.0x | |
| Kelly band for quality ≥ 65 | 0.7x | |
| Kelly band for quality < 65 | 0.3x | |
| Quality score weights | 40% technical, 30% structure, 30% context | |
| Max active trades | 8 | |
| Max daily trades | 10 | |
| Max portfolio risk | 12% | |
| Max same-direction trades | 5 | |
| Correlation limit | 8 open | |

Phase 2 backtest parameters (in `backtest/backtest_runner.py`):

| Parameter | Value |
|---|---|
| Crowding correlation clusters | BTC/ETH/SOL, BTC/ETH/XRP (min 2 matching) |
| Crowding quality range | 5 points |
| Crowding position size reduction | 50% |
| Data quality minimum score | 60.0 |

## Validation Gates (All Pass)

| Gate | Target | Result |
|---|---|---|
| Win rate | ≥ 30% | 56.4% |
| Profit factor | ≥ 1.5 | 1.56 |
| Expectancy | > $0 | $5.85 |
| Max drawdown | < 15% | 3.56% |
| Expired rate | < 30% | 1.4% |
| Take-profit hit rate | ≥ 40% | 45.0% |

## Backtest Results Summary

- **Period**: 6 months (Jan–Jun 2026)
- **Symbols**: BTC, ETH, SOL, XRP, BNB
- **Total trades**: 1,441 closed
- **Win rate**: 56.4%
- **Profit factor**: 1.56
- **Total PnL**: $8,434.41 (from $10,000 initial)
- **Max drawdown**: 3.56%
- **Crowded trades identified**: 252 (17.5% of all trades)

## Live Paper-Trading Status

**User observation (July 2026)**: The system is currently experiencing a losing streak described as "five losses, one win, five losses." This diverges from the 6-month backtest win rate of 56.4%.

**Next agent should audit**:
1. Whether the live sample size is large enough to be statistically meaningful.
2. Whether recent market conditions (July 2026) differ from the Jan–Jun 2026 backtest period.
3. Whether any runtime tuning (`tune_tp_from_history`, `tune_regime_from_history`) is overfitting to a small live sample.
4. Whether the V7 Kelly bands, `MR_BOOST = 3.0`, and `DQS_VETO_THRESHOLD = 85` remain appropriate in the current regime.
5. Whether data ingestion delays or execution timing are causing fill-price divergence.

See `docs/AGENT_HANDOFF.md` for the full audit checklist.

## Next Steps

1. **Audit the losing streak** before changing parameters. Run a 30-day backtest using current parameters to distinguish parameter drift from execution/data issues.
2. **Paper trading** — continue on local paper account, compare live vs backtest, gather at least 100 closed trades before tuning.
3. **Phase 3** (after paper trading validates) — empirical optimization: learn quality weights from live data, dynamic position sizing, walk-forward validation
