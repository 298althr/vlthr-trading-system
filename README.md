<div align="center">

# 📊 VLTHR Trading System

### An Open-Source Algorithmic Crypto Trading Pipeline with a 12-Gate Signal Approval Engine

**Built for Bybit V5. Running on Docker. Validated by Backtest. Open for Research.**

[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-success.svg)](https://mariadb.com/bsl11/)
[![CI](https://github.com/298althr/vlthr-trading-system/actions/workflows/ci.yml/badge.svg)](https://github.com/298althr/vlthr-trading-system/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Bybit V5](https://img.shields.io/badge/Bybit-V5%20API-F7A600?logo=bybit&logoColor=white)](https://bybit.com/)

</div>

---

## 🧭 Why This Project Exists

Most open-source trading bots are either:
- **Backtest-only toys** that look great in a notebook but cannot run live, or
- **Black-box SaaS products** where you cannot see or modify the signal logic.

VLTHR is neither. It is a **fully transparent, source-available trading pipeline** that:

1. **Ingests** real-time OHLCV data from Bybit (1m/5m/15m/1h/4h)
2. **Generates** signals using per-symbol strategies (trend following + mean reversion)
3. **Scores** every signal through a Dynamic Quality Score (DQS) with 3 weighted domains
4. **Gates** every signal through 12 sequential risk gates before approval
5. **Executes** on both a paper trading ledger and Bybit Demo API simultaneously
6. **Monitors** open positions with dynamic SL tightening, break-even stops, and time exits
7. **Logs** every decision (including vetoes) for intent-vs-outcome analysis

And it does all of this in Docker, with a React dashboard, Telegram alerts, and a full backtest engine.

**But it has problems we cannot solve alone.** That is why it is open source.

---

## 📣 Call for Research Collaboration

This system is live on Bybit Demo and has real performance data. We have identified **specific, well-documented gaps** where we need help from the community. If you are a quant researcher, ML engineer, or trading systems developer, we want to work with you.

### 🔴 Critical Research Gaps

| # | Problem | Evidence | What We Need |
|---|---------|----------|---------------|
| 1 | **BTC strategy is broken** | 13 live trades, 30.8% WR, -$563 net. All LONGs in a declining market. The trend_following strategy does not detect ranging conditions. | A BTC-specific strategy that detects regime shifts. The 4h log return direction signal is too noisy. |
| 2 | **SHORT signals are blocked** | Backtest shows SHORT PF 4.41 (n=249) but the live pipeline generates almost zero SHORTs. The V2 daily bias filter blocks SHORTs in bull markets. | A multi-timeframe trend structure filter that allows intraday pullback SHORTs without false signals. |
| 3 | **TP hit rate is low** | Backtest TP hit rate: 41.4% (barely passes 40% gate). Live Bybit TP hit rate: 11%. TP distances (3 ATR) are still beyond where price typically reaches (MFE 2.6 ATR). | Adaptive TP placement based on MFE distribution, or a trailing take-profit mechanism. |
| 4 | **No regime adaptation** | `REGIME_STRATEGY_OVERRIDE_ENABLED = False` because HMM-based switching hurt OOS PF by 20%. BTC is permanently trend_following, ETH is permanently mean_reversion. | A better regime switching approach: ensemble, Bayesian regime probability, or volatility-of-volatility gating. |
| 5 | **5 correlated symbols = no diversification** | All 5 symbols are crypto with 0.7-0.9 correlation. `max_same_side = 5` means all can be LONG simultaneously. 12% max portfolio risk with 5 correlated positions is effectively 25-30% real risk. | Correlation-adjusted position sizing, portfolio VaR, or risk parity framework. |
| 6 | **Paper vs Bybit PnL divergence** | Trade 47: Bybit +$155.78 vs paper -$469.74 on the same SL_HIT. Paper monitor uses parquet OHLCV (wider spreads) vs actual Bybit fills. | A unified execution layer that uses actual exchange fills for both paper and live PnL. |
| 7 | **Backtest does not model slippage or funding** | Backtest assumes zero slippage and no funding costs. Trades held 7+ hours incur funding on perpetuals. | Realistic slippage and funding rate models for Bybit market orders. |

### 🟡 Engineering Gaps

| # | Problem | Impact |
|---|---------|--------|
| 8 | SL attachment failure has no rollback | If `set_trading_stop` fails after market fill, position is open with no SL |
| 9 | Daily loss breaker trips on stale trades after restart | Requires manual SQL to clear |
| 10 | Runtime tuning mutates global state from 20 trades | Below statistical significance for 33-43% WR strategy |
| 11 | Signal expiration cascade (77,918 expired vs 8 approved) | 2h max age + DQS trajectory decline kills signals in 10 min |
| 12 | No walk-forward optimization | `SESSION_PARAMS` and `SESSION_REGIME_PARAMS` are empty |

> **Want to help?** Open a [Discussion](https://github.com/298althr/vlthr-trading-system/discussions) with the `research` label. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full list.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BYBIT V5 API (Demo)                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ OHLCV + WebSocket
                               ▼
┌──────────────────┐  ┌────────────────┐  ┌──────────────────────────┐
│  Data Ingestion  │  │   Bybit WS     │  │     PostgreSQL 15        │
│   (60s cadence)  │  │  (real-time)   │  │   (TimescaleDB)          │
│  → Parquet files │  │  → klines/tick │  │   paper_trades            │
└────────┬─────────┘  └───────┬────────┘  │   signal_state            │
         │                    │           │   risk_ledger             │
         ▼                    │           │   decision_events         │
┌─────────────────────────────────────────┘   │   pipeline_trace          │
│           PIPELINE ENGINE (5-min loop)         └──────────────────────────┘
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐
│  │  Scan   │→│  DQS    │→│  Rank   │→│ 12 Gates │→│  Execute │
│  │ Signals │  │ Scoring │  │         │  │ (risk)   │  │ (paper + │
│  │         │  │ (40/30/ │  │         │  │          │  │  Bybit)  │
│  └─────────┘  └─────────┘  └─────────┘  └──────────┘  └──────────┘
└──────────────────────────────────────────────────────────────────────┘
         │                                            │
         ▼                                            ▼
┌──────────────────┐                    ┌──────────────────────────────┐
│  Backend (Node)  │                    │  Frontend (React + Vite)      │
│  Express API     │◄──────────────────►│  Dashboard on :5175          │
│  Trade Monitor   │                    │  Live trades, PnL, charts     │
│  (15s sync)      │                    └──────────────────────────────┘
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  Telegram (3 bots)│
│  Data / Trader /  │
│  DevOps alerts    │
└──────────────────┘
```

### Services

| Service | Container | Port | Role |
|---------|-----------|------|------|
| 🐘 PostgreSQL | `vlthr-postgres` | 5432 | TimescaleDB, all trade data |
| 🔴 Redis | `vlthr-dashboard-redis` | 6379 | Cache, signal state |
| 📥 Data Ingestion | `vlthr-data-ingestion` | — | Bybit OHLCV ingestion (60s) |
| 📡 Bybit WS | `vlthr-bybit-ws` | 8200 | Real-time market data |
| ⚙️ Pipeline | `vlthr-pipeline` | 8201 | Signal engine (5-min loop) |
| 🔄 Calibration Cron | `vlthr-calibration-cron` | — | Daily recalibration (00:00 UTC) |
| 🖥️ Backend | `vlthr-dashboard-backend` | 3003 | Express API + trade monitor |
| 📊 Frontend | `vlthr-dashboard-frontend` | 5175 | React SPA dashboard |
| 📱 Telegram | `vlthr-telegram` | — | 3-bot alert system |

---

## 🚀 Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- A [Bybit account](https://www.bybit.com/) (demo or live API keys)
- Optional: 3 Telegram bots (create via [@BotFather](https://t.me/BotFather))

### 1. Clone and Configure

```bash
git clone https://github.com/298althr/vlthr-trading-system.git
cd vlthr-trading-system

# Copy the environment template
cp .env.example .env

# Edit .env with your API keys
# At minimum, set:
#   POSTGRES_PASSWORD=your_secure_password
#   BYBIT_TESTNET=true
#   BYBIT_DEMO_API_KEY=your_demo_key
#   BYBIT_DEMO_API_SECRET=your_demo_secret
#   EXECUTION_MODE=demo
```

### 2. Build and Start

```bash
docker compose up -d --build
```

### 3. Verify

```bash
# Check all services are healthy
docker compose ps

# Pipeline health
curl http://localhost:8201/health

# Backend API
curl http://localhost:3003/api/health

# Open the dashboard
open http://localhost:5175
```

### 4. Run a Backtest

```bash
cd backtest
python3 backtest_runner.py --all --start 2026-01-01 --end 2026-06-30 \
  --output backtest_results.csv --quiet
python3 stress_test.py backtest_results.csv stress_test_results.csv
```

---

## 📈 Backtest Results

### 1-Year Backtest (Jul 2025 - Jul 2026)

| Metric | Baseline | Optimized | Gate |
|--------|----------|----------|------|
| 📊 Closed trades | 893 | 872 | — |
| 🎯 Win rate | 32.8% | 42.9% | >= 30% ✅ |
| 💰 Profit factor | 2.21 | 2.86 | >= 1.5 ✅ |
| 📉 Max drawdown | 6.97% | 6.22% | < 15% ✅ |
| ⏱️ Expired rate | 0.7% | 0.7% | < 30% ✅ |
| 🎯 TP hit rate | 29.9% | 41.4% | >= 40% ✅ |
| 📈 Sharpe ratio | 45.39 | 63.98 | — |
| 💵 Final balance | $105K | $127K | — |

### Per-Symbol Performance (Treatment B)

| Symbol | Strategy | Trades | Win Rate | PnL | TP | SL | TIME |
|--------|----------|--------|----------|-----|----|----|------|
| BNBUSDT | Mean Reversion | 57 | 70.2% | +$526 | 34 | 12 | 11 |
| BTCUSDT | Trend Following | 384 | 59.1% | +$2,323 | 184 | 87 | 112 |
| ETHUSDT | Mean Reversion | 279 | 54.5% | +$1,270 | 116 | 71 | 91 |
| SOLUSDT | Trend Following | 381 | 56.4% | +$3,202 | 174 | 93 | 113 |
| XRPUSDT | Mean Reversion | 340 | 52.6% | +$1,114 | 140 | 96 | 103 |

### Per-Session Performance

| Session | Trades | Win Rate | PnL |
|---------|--------|----------|-----|
| 🇺🇸 NY | 439 | 62.6% | +$4,452 |
| 🌏 Asia | 387 | 53.0% | +$2,075 |
| 🇬🇧 London | 379 | 53.8% | +$1,281 |
| 🌙 NY Late | 236 | 54.7% | +$627 |

### Backtest Assumptions (Read Before Trusting)

1. **No slippage** — fills assumed at bar close price
2. **No funding rate** — perpetual funding not modeled
3. **Perfect data** — no ingestion gaps or delays
4. **No partial fills** — all orders fully filled
5. **15-min cadence** — signals evaluated every 15 min in backtest (5 min live)

---

## 🎚️ The 12-Gate Pipeline

Every signal must pass all 12 gates before it becomes a trade:

```
Signal Generated
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  GATE 0A: EXPIRED     → Signal < 2h old?               │
│  GATE 0B: STALE       → Data freshness (bar < 20min)?  │
│  GATE 0C: REGIME      → Regime DQS filter (TF/MIXED≥65)?│
│  GATE 0D: ROUTER      → Strategy routing correct?       │
│  GATE 1:  RANKER      → DQS >= 50?                      │
│  GATE 2:  CONFIRMED   → DQS trajectory not declining?    │
│  GATE 3:  DAILY BREAK → Daily PnL > -3.0%?              │
│  GATE 4A: CORRELATION → No existing position on symbol? │
│  GATE 4B: DIRECTIONAL → Max 5 same-side positions?      │
│  GATE 4C: RISK BUDGET → Total risk <= 12%?              │
│  GATE 4D: TRADE COUNT → Daily/session limits OK?        │
│  GATE 5:  CALIBRATION → ML probability >= 0.40?         │
└─────────────────────────────────────────────────────────┘
    │
    ✓ All gates pass
    ▼
  PENDING → OPEN → CLOSED (SL / TP / TIME / LIQUIDATION)
```

### Dynamic Quality Score (DQS)

The DQS is the core scoring engine. It combines 3 weighted domains:

| Domain | Weight | What It Measures |
|--------|--------|------------------|
| 📊 Technical | 40% | RSI, ADX, ATR ratio, volume ratio, momentum |
| 📐 Structure | 30% | Support/resistance, trend lines, pivot strength |
| 🌍 Context | 30% | Session quality, symbol quality, regime fit |

Signals with DQS >= 85 are **vetoed** (Kelly = 0) because backtest showed 46.8% WR at that range. Signals with DQS 70-84 get Kelly multipliers from 1.0x to 2.5x.

---

## ⚙️ Key Parameters

All parameters live in `pipeline/engine/portfolio_config.py`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the frozen parameters list.

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Scan interval | 300s | How often the pipeline evaluates signals |
| Max active trades | 8 | Maximum concurrent open positions |
| Max leverage | 4x | Maximum leverage per position |
| Max portfolio risk | 12% | Total open risk as % of balance |
| Daily loss breaker | -3.0% | Halt all new trades if daily PnL drops below this |
| SL time decay | 0.15 ATR/hr | SL tightens after hour 3 of holding |
| Max trade hours | 12h | Force-close trades held too long |
| Taker fee | 0.055% per side | Bybit taker fee |
| Kelly V7 (DQS≥80) | 2.5x | Position size multiplier for high-quality signals |
| MR boost | 3.0x | Mean reversion strategy Kelly multiplier |

---

## 📁 Repository Structure

```
vlthr-trading-system/
├── docker-compose.yml          # Master orchestration (9 services)
├── .env.example                 # Environment template (copy to .env)
├── pipeline/
│   └── engine/
│       ├── portfolio_config.py     # ⚙️  All trading parameters
│       ├── portfolio_orchestrator.py # 🧠 Main pipeline logic
│       ├── portfolio_gates.py     # 🚦 12-gate risk system
│       ├── adaptive_scorer.py     # 📊 DQS scoring engine
│       ├── bybit_executor.py      # 📡 Bybit V5 API wrapper
│       ├── bybit_ws.py            # 📡 Real-time WebSocket
│       ├── dynamic_sl.py          # 📉 Dynamic stop-loss logic
│       ├── v2_filters.py          # 🧹 Signal filters
│       └── calibration.json       # 🎯 Calibration constants
├── backtest/
│   ├── backtest_runner.py         # 📈 Bar-by-bar backtest engine
│   ├── stress_test.py             # 📊 Per-symbol/regime/session analysis
│   └── METHODOLOGY.md             # 📋 Backtest procedure
├── backend/
│   └── src/
│       └── server.cjs             # 🖥️ Express API + trade monitor
├── frontend/
│   └── src/
│       └── App.tsx                # 📊 React dashboard
├── telegram/
│   ├── telegram_service.py        # 📱 3-bot architecture
│   └── telegram_polling_service.py
├── data-ingestion/
│   └── workers/                   # 📥 Bybit OHLCV ingestion
├── postgres/
│   └── db_migration.py            # 🐘 Schema creation/migration
├── docs/
│   ├── ARCHITECTURE.md            # 📐 System architecture
│   └── BYBIT_PERFORMANCE_REVIEW_JUL24.md  # 📊 Live performance data
└── .github/
    └── ISSUE_TEMPLATE/            # 🐛 Bug report + feature request
```

---

## 🔬 Research Roadmap

We have a clear roadmap of what needs to be researched and built. If you want to take on any of these, open a discussion:

### Phase 1: Fix What Is Broken (Immediate)
- [ ] BTC-specific strategy (regime detection for ranging vs trending)
- [ ] SHORT signal unlock (multi-timeframe trend structure)
- [ ] SL attachment failure rollback
- [ ] Daily loss breaker auto-cleanup on restart

### Phase 2: Improve What Works (Near-term)
- [ ] Adaptive TP placement (MFE-distribution-based)
- [ ] Correlation-adjusted position sizing (portfolio VaR)
- [ ] Dynamic Kelly (rolling trade history with sample size requirements)
- [ ] Slippage model for backtest (Bybit market order impact)
- [ ] Funding rate model for backtest

### Phase 3: Build What Is Missing (Medium-term)
- [ ] Regime-adaptive strategy switching (ensemble or Bayesian)
- [ ] Walk-forward optimization (IS/OOS with purge gap)
- [ ] Volatility targeting (reduce size when ATR spikes)
- [ ] Unified execution layer (paper + live use same fill data)

### Phase 4: Scale (Long-term)
- [ ] Additional symbols beyond crypto (if correlation-adjusted sizing works)
- [ ] Options or hedging instruments for tail risk
- [ ] Multi-exchange support (beyond Bybit)
- [ ] Real-time ML gate retraining

---

## 🛡️ Security

- **No secrets are committed.** All API keys, tokens, and passwords are loaded from `.env` (gitignored).
- **`.env.example`** contains only placeholder values.
- **Bybit mainnet keys are NOT included.** The default is `BYBIT_TESTNET=true` and `EXECUTION_MODE=internal` (paper simulation only).
- **Parameterized queries** throughout. No SQL injection vectors.
- If you find a security issue, please email the maintainer directly instead of opening a public issue.

---

## 📜 License

This project is licensed under the **Business Source License 1.1 (BSL 1.1)**. See [LICENSE](LICENSE) for the full text.

### What This Means

| Use Case | Free? | Attribution Required? |
|----------|-------|----------------------|
| 🔬 Research and academic use | Yes | Yes |
| 🎓 Education and teaching | Yes | Yes |
| 🧪 Personal projects and experimentation | Yes | Yes |
| 🛠️ Contributing back to this repo | Yes | Yes |
| 📖 Code review and audit | Yes | Yes |
| 💰 Production trading (fund management, prop desk) | **No** (commercial license required) | Yes |
| 🏢 SaaS or hosted service | **No** (commercial license required) | Yes |
| 📦 Commercial product integration | **No** (commercial license required) | Yes |

### Attribution Requirement

When you use, fork, or reference this project, you must:
1. Include the name **"VLTHR Trading System"** and a link to https://github.com/298althr/vlthr-trading-system
2. Retain all copyright and license notices in the source code
3. Cite the repository in any academic publication:
   ```
   VLTHR Trading System (2026), https://github.com/298althr/vlthr-trading-system
   ```
4. Mention the project in any public-facing context (blog post, demo, talk, social media)

See [ADDITIONAL_USE_GRANT.txt](ADDITIONAL_USE_GRANT.txt) for the full details.

### Change Date

On **2030-09-13**, this license automatically converts to the **Apache License 2.0**, making the project fully open source for all uses including commercial.

### Commercial License

For production trading, SaaS deployment, or commercial integration, contact: **vlthr@298althr.github**

See [LICENSES.md](LICENSES.md) for the licenses of all dependencies.

---

## 🙏 Acknowledgments

This project builds on the work of:
- The **pandas** and **numpy** teams
- The **scikit-learn** and **river** teams (ML tooling)
- The **React** and **Vite** teams (frontend)
- The **Express** and **PostgreSQL** teams (backend)
- The **Bybit V5 API** team (exchange integration)

---

<div align="center">

### ⭐ If this project is useful to your research, please star it.

**[💬 Start a Discussion](https://github.com/298althr/vlthr-trading-system/discussions)** ·
**[🐛 Report a Bug](https://github.com/298althr/vlthr-trading-system/issues/new?labels=bug)** ·
**[✨ Request a Feature](https://github.com/298althr/vlthr-trading-system/issues/new?labels=enhancement)**

</div>
