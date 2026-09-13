---
description: Scenario testing workflow — stress test strategies across normal, high vol, low vol, crisis, and cost scenarios
---

# Scenario Testing Workflow

## When To Use
- Stress testing a strategy under different market conditions
- Validating robustness across cost regimes
- Pre-deployment risk assessment

## Scenario Types

| Scenario | Spread | Slippage | Vol Multiplier | Description |
|---|---|---|---|---|
| normal | 0.25 | 0.5 | 1.0 | Standard conditions |
| high_vol | 0.50 | 1.0 | 1.5 | News events, economic releases |
| low_vol | 0.15 | 0.3 | 0.7 | Range-bound markets |
| crisis | 2.00 | 5.0 | 3.0 | Liquidity crisis, extreme events |
| 2x_cost | 2x normal | 2x normal | 1.0 | Cost stress test |
| maker_fee | reduced | reduced | 1.0 | Best-case fee scenario |
| inverted_bias | normal | normal | 1.0 | Wrong direction bias (control) |

## Steps

1. **Define scenarios** (see table above)
2. **Run backtest for each scenario** with same strategy parameters
3. **Compare metrics across scenarios:**
   - Win rate should not collapse (> 35% in worst case)
   - Sharpe should degrade gracefully (< 50% drop from normal)
   - Max drawdown should not exceed 3x normal
4. **Identify failure scenarios** where strategy goes negative
5. **Document which scenarios are safe vs unsafe for deployment**

## VLTHR V7 Cost Model (GOLD STANDARD)

V7 uses maker fee model (40% of taker + 50% slippage reduction) → 59.2% cost reduction.
This achieves 15/15 on Cost Sensitivity pillar.

**V7 final metrics:** 1,201 trades, $13,401 PnL, 54% WR, 1.80 PF, MaxDD -$1,288

## VLTHR V2 Scenario Results (OOS, 3 months — deprecated)
- Normal: n=2260, WR=48.9%, E=$3.43, Sharpe=2.27, MaxDD=$521
- Inverted bias: n=2158, WR=41.4%, E=$0.63, Sharpe=0.42, MaxDD=$1533
- 2x cost: n=2260, WR=46.6%, E=$1.61, Sharpe=1.07, MaxDD=$826
- Maker fee: n=2260, WR=50.0%, E=$4.59, Sharpe=3.03, MaxDD=$427

## Key Insight
The inverted bias scenario is the most dangerous — it confirms the strategy depends on correct direction prediction. With wrong bias, Sharpe drops 81% and MaxDD triples.

## Decoupler Stress Test
```python
def decoupler_stress_test(df, strategy_func, params):
    baseline = backtest_strategy(df, params)
    stress = {
        'seed_change': backtest with different random seed,
        'shuffled': shuffle trade labels (should go to ~0),
        'noisy_price': add 1% Gaussian noise to close prices,
        'reduced_history': remove last 252 bars,
        'different_asset': test on unrelated asset
    }
    # All degradation < 50% of baseline Sharpe = decoupled (not overfitted)
```

## Files
- `Backtest-Engine/stress_test.py`
- `Backtest-Engine/institutional_validation_v7.py` — V7 cost sensitivity validation
- `backtestsystem/SKILLS.md` (Section 7, 12)
