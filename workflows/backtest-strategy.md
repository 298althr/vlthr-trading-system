---
description: Full backtesting workflow — load data, generate signals, simulate trades, calculate metrics, validate edge
---

# Backtest Strategy Workflow

## When To Use
- Researching and validating a new trading strategy
- Running a full backtest with realistic costs
- Comparing strategy variants

## Steps

1. **Load and validate OHLCV data**
   - Check: required columns (open, high, low, close, volume, timestamp)
   - Check: timestamps monotonic, no duplicates
   - Check: high >= low, high >= open/close, low <= open/close
   - Source: `data/bybit/{SYMBOL}/{TF}/*.parquet`

2. **Generate signals**
   - Apply strategy logic to generate buy/sell signals
   - Record timestamp, direction, confidence for each signal

3. **Simulate trades with realistic costs**
   - Apply spread, commission, slippage per trade
   - Use per-symbol slippage model (2-8 bps for crypto)
   - Include funding rate for perpetuals
   - Cost model: `entry_price = price + spread/2 + slippage; exit_price = price - spread/2 - slippage`

4. **Calculate performance metrics**
   - Total return, max drawdown, Sharpe ratio, win rate, profit factor
   - Per-symbol breakdown
   - SL hit rate, TP hit rate, TIME_EXIT rate

5. **Run scenario tests**
   - Normal, high volatility, low volatility, crisis, 2x cost, maker fee
   - Strategy should degrade gracefully, not collapse

6. **Statistical validation**
   - Bootstrap p-value (10,000 iterations)
   - Deflated Sharpe ratio (correct for multiple trials)
   - Factor regression to isolate alpha

7. **Walk-forward validation**
   - IS/OOS split (typically 75/25 by timestamp)
   - Year-by-year walk-forward with parameter reset
   - Check WR decay and expectancy decay

8. **Stress test (decoupler)**
   - Seed change, shuffled labels, noisy price, reduced history, different asset
   - All degradation < 50% of baseline Sharpe

9. **Document results**
   - Performance JSON, trade log JSON, equity curve PNG
   - Folder: `Backtest-Engine/results/`

## Key Files
- `Backtest-Engine/institutional_validation_v7.py` — V7 institutional validation (GOLD STANDARD)
- `Backtest-Engine/backtest_portfolio.py` — main backtest runner
- `Backtest-Engine/stress_test.py` — stress test scenarios
- `backtestsystem/SKILLS.md` — full skills reference
- `backtestsystem/BACKTEST_FRAMEWORK.md` — framework docs
- `engine/portfolio_config.py` — `tune_tp_from_history()` for runtime TP adjustment
- `backend/server.cjs` — flash-wick spike filter in auto-close logic
- `engine/test_risk_logic.py` — 31 unittest tests for risk path

## V7 Institutional Validation (GOLD STANDARD 88/100)

The V7 validation script (`institutional_validation_v7.py`) scores the strategy across 8 pillars:

| Pillar | Score | Max |
|--------|-------|-----|
| Edge Existence | 15 | 15 |
| Statistical Significance | 15 | 15 |
| Robustness | 11 | 15 |
| Risk Analysis | 11 | 15 |
| Cost Sensitivity | 15 | 15 |
| Calibration | 8 | 10 |
| Concentration | 8 | 10 |
| Operational Readiness | 5 | 5 |
| **TOTAL** | **88** | **100** |

**V7 key parameters (DO NOT CHANGE):**
- Entry filter: `calibrated_win_prob > 0.48`
- Kelly bands: 85+:0x, 80-85:2.5x, 75-80:1.5x, 70-75:1.0x, 65-70:0.7x, <65:0.3x
- MR boost: 3.0x
- Symbol PnL cap: 30%, allocation cap: 25%
- Walk-forward split: 50%
- Maker fee model: 40% of taker + 50% slippage reduction
- DOGEUSDT: disabled

Run: `python Backtest-Engine/institutional_validation_v7.py`

## Anti-Patterns to Avoid
- Look-ahead bias: using future data in signal generation
- Survivorship bias: only testing surviving assets
- Overfitting: too many parameters, poor OOS performance
- Data snooping: repeated testing on same data without correction
- **Flash-wick false triggers** (Jul 2026): Using raw high/low for SL/TP checks instead of close price. Fixed with 10% spike filter in both server.cjs and fallback monitor.
- **Stale TP targets** (Jul 2026): TP multipliers set once and never adjusted. Fixed with `tune_tp_from_history()` auto-tuning from closed-trade exit reasons.
