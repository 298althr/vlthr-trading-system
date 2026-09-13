# VLTHR Backtest Methodology

## Purpose
Validate that decoupled pipeline changes produce measurably better results than the current monolithic loop. Every change must be backtested before it touches live paper trading.

## Data
- **Source**: Local Parquet OHLCV (`DEVOPS/data/bybit/`)
- **Symbols**: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT
- **Timeframes**: 1m (entry/exit), 5m/15m/1h/4h (signal context)
- **Period**: Full available history (~31 days, 44,540 1m bars per symbol)
- **No look-ahead**: Signal at bar N can only use data from bars ≤ N-1

## Backtest Procedure

### Step 1 — Run the Current Pipeline (Control)
1. Run the existing `portfolio_orchestrator.py` logic in backtest mode over the full data period.
2. Record every signal found, every gate decision, every trade outcome.
3. Write results to `backtest_results.csv` with `notes=control`.

### Step 2 — Apply Phase 1 Fixes (Treatment A)
1. Move V7 Kelly veto from scanner to risk layer (scanner emits all signals; risk layer sizes or blocks).
2. Remove V2 filters from reprice step.
3. Fix equity accounting (add margin_used back for drawdown checks).
4. Re-run backtest over same period.
5. Write results to `backtest_results.csv` with `notes=treatment_a_phase1`.

### Step 3 — Apply Phase 2 Fixes (Treatment B)
1. Add event contracts: `signal.ranked`, `trade.approved`, `trade.opened`, `trade.closed`.
2. Run signal engine and risk engine as separate functions with explicit inputs/outputs.
3. Log intent vs outcome per signal.
4. Re-run backtest.
5. Write results to `backtest_results.csv` with `notes=treatment_b_phase2`.

### Step 4 — Compare
1. Load `backtest_results.csv`.
2. Group by `notes` (control vs treatment_a vs treatment_b).
3. Compute metrics per group (see Validation Metrics below).
4. Gate: Treatment must beat control on all primary metrics.

## Validation Metrics

### Primary (Must Pass All)
| Metric | Formula | Target |
|---|---|---|
| Win Rate | wins / closed_trades | ≥ 30% |
| Profit Factor | gross_profit / gross_loss | ≥ 1.5 |
| Expectancy | avg(pnl_usd) per trade | > $0 |
| Max Drawdown | max(peak - trough) / peak | < 15% |
| Expired Rate | expired / total_signals | < 30% |
| TP Hit Rate | tp_hits / closed_trades | ≥ 40% |

### Secondary (Track & Report)
| Metric | Formula | Target |
|---|---|---|
| Gate Approval Rate | approved / ranked | 5–20% |
| Promote Rate | opened / approved | > 60% |
| Avg Hold Time | avg(hours_held) | 4–12h |
| SL Hit Rate | sl_hits / closed_trades | < 50% |
| Signals Per Day | total_signals / days_run | > 0 |
| Invariant Breaches | count | 0 |

### Per-Symbol Breakdown
Each metric must also be computed per-symbol to catch symbol-specific regressions.

## CSV Schema
| Column | Type | Description |
|---|---|---|
| symbol | string | BTCUSDT, ETHUSDT, etc. |
| side | string | LONG or SHORT |
| entry_date | datetime | Signal generation timestamp |
| entry_price | float | Entry price at signal time |
| sl_price | float | Stop-loss price |
| tp_price | float | Take-profit price |
| dqs_score | int | DQS at signal time (0-100) |
| regime | string | Market regime classification |
| strategy | string | trend_following, mean_reversion |
| approved | bool | Did risk gate approve? |
| reject_reason | string | Why rejected (empty if approved) |
| promoted | bool | Did PENDING → OPEN? |
| exit_date | datetime | Exit timestamp |
| exit_price | float | Exit price |
| exit_reason | string | TP_HIT, SL_HIT, TIME_EXIT, EXPIRED |
| hours_held | float | Hours from entry to exit |
| pnl_usd | float | Net PnL in USD |
| pnl_pct | float | Net PnL percentage |
| risk_usd | float | Dollar risk at entry |
| kelly_fraction | float | Kelly fraction used (0.0–1.0) |
| win | bool | Was this a winning trade? |
| notes | string | control, treatment_a_phase1, treatment_b_phase2 |

## Rules
1. **Never modify control results** — append treatment rows only.
2. **One CSV per backtest run** — copy to `backtest_results_YYYYMMDD.csv` before starting a new run.
3. **No cherry-picking** — report all signals, including rejected and expired.
4. **Statistical minimum** — need ≥ 30 closed trades per treatment to claim significance.
5. **Walk-forward** — split data into IS (first 70%) and OOS (last 30%). Optimize on IS, report OOS.
