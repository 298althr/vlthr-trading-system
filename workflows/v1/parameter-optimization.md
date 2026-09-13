---
description: Parameter optimization workflow — grid search, random search, walk-forward optimization for trading strategies
---

# Parameter Optimization Workflow

## When To Use
- Finding optimal strategy parameters (EMA periods, RSI thresholds, SL/TP multipliers)
- Comparing grid search vs random search approaches
- Preventing overfitting through proper walk-forward optimization

## Steps

1. **Define parameter space**
   ```python
   param_ranges = {
       'fast_period': range(4, 21),
       'slow_period': range(10, 51),
       'risk_pct': [1.0, 1.5, 2.0, 2.5, 3.0]
   }
   ```

2. **Choose search method**
   - **Grid search:** Exhaustive, good for small spaces (< 10,000 combos)
   - **Random search:** Better for large spaces, finds near-optimal faster
   - **Bayesian optimization:** For expensive evaluations

3. **Grid search implementation**
   ```python
   from itertools import product
   all_combinations = list(product(*param_ranges.values()))
   results = []
   for params in all_combinations:
       result = backtest_strategy(*params)
       results.append({'params': params, 'metrics': result})
   results.sort(key=lambda x: x['metrics']['sharpe'], reverse=True)
   ```

4. **Random search implementation**
   ```python
   import random
   for _ in range(n_iterations):
       fast = random.randint(4, 20)
       slow = random.randint(fast + 1, 50)
       result = backtest_strategy(fast, slow, risk)
   ```

5. **Walk-forward optimization (prevent overfitting)**
   - Train on Y-2 to Y-1, validate on Y
   - Reset parameters at each calendar year
   - Check: WR decay < 5%, expectancy decay < $0.50

6. **Select optimal parameters**
   - Primary metric: Sharpe ratio (risk-adjusted)
   - Secondary: Profit factor, max drawdown, total PnL
   - Constraint: OOS Sharpe within 30% of IS Sharpe

7. **Validate with stress tests**
   - Run decoupler stress test on optimal parameters
   - All degradation < 50% of baseline
   - Check cross-asset consistency (> 70% same Sharpe sign)

## VLTHR-Specific Parameters
- Per-symbol SL/TP multipliers: sl_mult 2.0-2.5, tp_mult 5.0-7.5
- Per-symbol strategy: BTC/SOL=trend_following, ETH/XRP/BNB/DOGE=mean_reversion
- Symbol multipliers: BTC 1.0, SOL 1.0, ETH 0.60, XRP 0.55, BNB 0.55, DOGE 0.60
- DQS thresholds: track=40, execute=50, good=65, excellent=75

## Anti-Patterns
- Optimizing on full dataset (no IS/OOS split)
- Too many parameters (> 5 free parameters)
- Selecting by total PnL instead of risk-adjusted return
- Ignoring transaction costs in optimization
