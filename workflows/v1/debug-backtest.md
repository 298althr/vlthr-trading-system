---
description: Debug backtest workflow — identify and fix look-ahead bias, survivorship bias, overfitting, data snooping, and label leakage
---

# Debug Backtest Workflow

## When To Use
- Investigating suspiciously high backtest performance
- Diagnosing large IS/OOS performance gaps
- Tracking down label leakage in multi-timeframe merges
- Debugging unexpected trade behavior

## Common Issues & Diagnosis

### 1. Look-Ahead Bias / Label Leakage
**Symptoms:** Backtest performance too good, direction accuracy > 60%, IC > 0.3
**Diagnosis:**
- Check all multi-timeframe merges: are higher-TF timestamps shifted to candle CLOSE?
- Correlate each feature with forward returns: `corr(feature, fwd_return) ~= 0` expected
- Check: are any features computed using `.shift(-n)` (future data)?
- Check: does `merge_asof(direction="backward")` use unshifted open-timestamp bars?

**Fix (4h context leakage pattern):**
```python
# BUG: 4h bar timestamp = OPEN, but close/ADX known 4h later
# FIX: shift 4h timestamps forward by 4h before merge
df["timestamp"] = df["timestamp"] + pd.Timedelta(hours=4)
```

**Verification script:**
```python
# After fix, correlation should be ~0
corr = df["ctx_log_ret_4h"].corr(df["close"].shift(-4) / df["close"] - 1)
assert abs(corr) < 0.05, f"Leakage detected: corr={corr}"
```

### 2. Overfitting
**Symptoms:** Large IS/OOS decay, parameters very specific
**Diagnosis:**
- WR decay > 5%, expectancy decay > $0.50
- Strategy collapses under decoupler stress tests
- Too many free parameters (> 5)
**Fix:** Reduce parameters, use walk-forward, apply regularization

### 3. Data Snooping
**Symptoms:** Many trials on same dataset, no correction
**Diagnosis:**
- Count total parameter combinations tested
- Compute deflated Sharpe: `DS = sharpe - E_S_max`
- If DS < 0, the edge doesn't survive multiple-trial correction
**Fix:** Use fresh data, apply Bonferroni or FDR correction

### 4. Survivorship Bias
**Symptoms:** Only testing currently active assets
**Fix:** Include delisted/failed assets in backtest

### 5. Cost Underestimation
**Symptoms:** Backtest PnL >> live PnL
**Diagnosis:**
- Check: are fees, slippage, funding all included?
- Check: is slippage realistic (2-8 bps for crypto perpetuals)?
- Check: is funding rate included for positions held > 8h?
**Fix:** Use 3-scenario cost model (normal, 2x, maker)

## VLTHR Debugging History
- **ctx_log_ret_4h leakage:** correlation 0.42 before fix, ~0.00 after
- **Phase 8 100% direction accuracy:** tiny sample (n=4-6) + leaked feature
- **Hurst exponent bug:** single-element window in variance ratio
- **IC demotion bug:** used `ic < threshold` instead of `abs(ic) < threshold`
- **Bayesian prior weighting:** `prior_n = int(prior * 100)` caused quadratic weighting
- **500-bar silent cap:** `MAX_CACHED_BARS = 500` made `tail(2000)` a no-op

## Debug Toolkit
```python
# Add logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Validate intermediate results
assert len(signals) > 0, "No signals generated"
assert all('pnl' in t for t in trades), "Missing PnL in trades"

# Visual inspection
plt.plot(equity_curve)
plt.savefig('debug_equity.png')

# Check for decoupling
result = decoupler_stress_test(df, strategy, params)
assert result['decoupled'], "Strategy not decoupled - overfitted"
```

## Files
- `Backtest-Engine/verify_leakage.py` — leakage verification
- `backtestsystem/SKILLS.md` (Section 15)
