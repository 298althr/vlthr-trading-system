---
description: Setup workflow — environment setup, dependency installation, data ingestion, and initial system bootstrap for VLTHR
---

# Setup Workflow

## When To Use
- Initial environment setup for new developers
- Rebuilding after environment corruption
- Setting up a new machine for VLTHR development
- Verifying deployment readiness

## Steps

### 1. Environment Setup
- Python 3.11+ required
- Key packages: pandas, numpy, scipy, statsmodels, psycopg2-binary, pyarrow
- Node.js 18+ for backend
- Docker + Docker Compose for containerized services

### 2. Environment Variables
Copy `.env.example` to `.env` and fill in:
- `DB_URL` — Supabase PostgreSQL connection string
- `BYBIT_API_KEY` / `BYBIT_API_SECRET` — for data ingestion
- `PROXY_URL` — (optional, currently disabled — direct connection)

### 3. Data Ingestion
```bash
# Run unified scheduler once to verify data flow
python -m engine.v3.ingestion.unified_scheduler --run-once --symbols BTCUSDT --tfs 15m
```
Verify: OHLCV, funding, OI, LS ratio all fetched successfully.

### 4. Database Setup
- Run Alembic migrations: `alembic upgrade head`
- Verify tables: `paper_trades`, `high_confidence_signals`, `hypothesis_registry`, `experiment_runs`, `shadow_decisions`, `decision_events`

### 5. Docker Services

**Linux deployment (see Doc 42):**
```bash
export VLTHR_ROOT=/home/<user>/VLTHR
export DASHBOARD_ROOT=$VLTHR_ROOT/paper_trade_unzipped/vlthr-signal-dashboard
cd $DASHBOARD_ROOT

# Layer 1: core services (postgres, data-ingestion, redis, backend, frontend, ngrok)
docker compose -f docker-compose.linux.ngrok.yml up -d --build

# Layer 2: pipeline + ai-trader-engine
docker compose -f docker-compose.linux.yml up -d --build

# Layer 3: monitoring (MLflow, Prometheus, Grafana, db-backup, db-maintenance)
docker compose -f docker-compose.ops.yml up -d
```

**IMPORTANT:** Both `VLTHR_ROOT` and `DASHBOARD_ROOT` must be set. `VLTHR_ROOT` must NOT include `v3/`.

Services: `vlthr-data-ingestion`, `vlthr-pipeline`, `vlthr-dashboard-backend`, `vlthr-dashboard-frontend`, `vlthr-postgres`, `vlthr-dashboard-redis`

### 6. Verification
```bash
python verify_deployment.py  # should print ALL PASSED
python test_phase9.py          # 20/20 tests
python test_phase10.py         # 18/18 tests
python Backtest-Engine/institutional_validation_v7.py  # 88/100 GOLD STANDARD

# Risk logic tests (Jul 2026 hardening)
cd engine && python3 test_risk_logic.py  # 31/31 tests
```

### 7. UCB Construction (if needed)
```bash
python Backtest-Engine/build_ucb_labels.py
python Backtest-Engine/build_ucb_microstructure.py
python Backtest-Engine/build_ucb_decisions.py
```

### 8. Calibration Matrix
```bash
python Backtest-Engine/build_cal_matrix.py
```
Verify: `engine/calibration.json` has correct gate thresholds.

### 9. ABSTAIN Gate
```bash
python Backtest-Engine/build_abstain_gate.py
```
Verify: `Backtest-Engine/results/abstain_gate.json` exists with all 42 cells.

## System Status (V7 GOLD STANDARD)
- **Institutional validation:** 88/100 — GO
- **Institutional hardening:** 12/12 fixes applied (Jul 2026)
- **All phases:** COMPLETE (0-10)
- **All tests:** 47/47 backtest + 31/31 risk logic tests passing
- **CI:** GitHub Actions on engine directory (`engine-ci.yml`)
- **Capital:** $10,000, 4x leverage
- **Trading window:** 12 months (Jun 2025 – May 2026)
- **V7 metrics:** 1,201 trades, $13,401 PnL, 54% WR, 1.80 PF, MaxDD -$1,288
- **Key docs:** Doc 40 (Strategy), Doc 41 (CTP-V4 Handoff), Doc 42 (Linux Deploy)

## Institutional Hardening Details (Jul 2026)
- **TLS verification:** All API calls use `verify=True` (removed `verify=False` from 6 sites)
- **Blocking validation:** Bad OHLCV data quarantined, not written
- **Checksum columns:** `error_log.checksum`, `signal_audit_log.checksum` (SHA-256, 16-char)
- **DB migration:** Run `ALTER TABLE ... ADD COLUMN IF NOT EXISTS checksum VARCHAR(16)` on both tables
- **Test suite:** `engine/test_risk_logic.py` — 31 unittest tests covering risk tiers, SL/TP, Kelly, gates, TP tuning
- **CI:** `.github/workflows/engine-ci.yml` — runs risk tests on engine path changes

## Key Paths
- Engine: `paper_trade_unzipped/vlthr-signal-dashboard/engine/`
- Backtest: `Backtest-Engine/`
- Data: `data/bybit/{SYMBOL}/{TF}/*.parquet`
- UCB: `data/ucb/layer*.parquet`
- Results: `Backtest-Engine/results/`
- Config: `engine/calibration.json`, `Backtest-Engine/results/abstain_gate.json`
- Tests: `engine/test_risk_logic.py` (31 unittest tests)
- CI: `.github/workflows/engine-ci.yml`
