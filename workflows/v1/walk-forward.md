---
description: Walk-forward validation workflow — chronological IS/OOS splits, year-by-year validation, overfitting detection
---

# Walk-Forward Validation Workflow

## When To Use
- Validating strategy performance out-of-sample
- Detecting overfitting through chronological splits
- Measuring parameter stability across time periods

## Steps

1. **Define IS/OOS split**
   - Typical: 75% IS, 25% OOS by timestamp
   - VLTHR: IS 2025-06-01 to 2026-03-01 (9 months), OOS 2026-03-01 to 2026-06-01 (3 months)
   - Never shuffle — must be chronological

2. **Optimize on IS data only**
   - Run parameter optimization on IS period
   - Record best parameters and IS metrics

3. **Test on OOS data (no re-optimization)**
   - Apply IS-optimal parameters to OOS period
   - Record OOS metrics

4. **Measure decay**
   - WR decay = (IS_WR - OOS_WR) / IS_WR
   - Expectancy decay = IS_expectancy - OOS_expectancy
   - Acceptable: WR decay < 5%, expectancy decay < $0.50
   - VLTHR benchmark: WR decay -1.3%, expectancy decay -$0.07 (minimal)

5. **Year-by-year walk-forward**
```python
def year_by_year_walkforward(df, params, start_year, end_year):
    results = []
    for year in range(start_year, end_year + 1):
        train_df = df[(df['timestamp'] >= f'{year-2}-01-01') & 
                      (df['timestamp'] <= f'{year-1}-12-31')]
        val_df = df[(df['timestamp'] >= f'{year}-01-01') & 
                    (df['timestamp'] <= f'{year}-12-31')]
        best_params = optimize_parameters(train_df, params)
        val_result = backtest_strategy(val_df, best_params)
        results.append({'year': year, 'val_metrics': val_result})
    return results
```

6. **Check consistency**
   - All years should have positive expectancy (or at least same sign)
   - No single year should have > 3x average drawdown
   - Sharpe should be positive in > 70% of years

## VLTHR V7 Walk-Forward Results (GOLD STANDARD)
- **V7 split:** 50% IS / 50% OOS (standard institutional)
- **V7 trades:** 1,201 (filtered from 9,403 base decisions)
- **V7 PnL:** $13,401, WR 54%, PF 1.80, MaxDD -$1,288
- **Capital:** $10,000, 4x leverage
- **Window:** 12 months (Jun 2025 – May 2026)
- **Robustness pillar:** 11/15 (WF decay -13.6% at 50% split = PASS, but 75% split decay 45.7% = FAIL)

**Previous V2 walk-forward (deprecated):**
- IS: 15,111 trades, WR 41.5%, +$16,010, +160.1%
- OOS: 5,078 trades, WR 42.8%, +$5,733, +57.3%
- Decay: WR -1.3%, expectancy -$0.07 (minimal overfitting)
- Bootstrap: P5=$17,237, P50=$22,917, P95=$28,476, 100% prob positive year

## Anti-Patterns
- Using OOS data for parameter selection (defeats the purpose)
- Multiple OOS tests on same data (data snooping — use deflated Sharpe)
- Non-chronological splits (leaks temporal information)
- Too short OOS period (< 3 months for 15m strategy)
